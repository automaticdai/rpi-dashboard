# rpi-dashboard — Design

Date: 2026-11-06
Status: approved by owner 2026-11-06 (revised: no database server)

## 1. Intent

The owner runs a small fleet of Raspberry Pis on a home network. They want
one place to see each Pi's hardware and live health, view recent history,
and take basic actions when something looks wrong. It should be
**lightweight**: no database server or other extra infrastructure, just
an agent on each Pi and a dashboard.

Success: open a browser on the desktop and immediately see which Pis are
up, how loaded they are, and whether any are hot, full or throttled. Then
click into one Pi to see its spec and charts such as "temperature over the
last hour", and reboot it or restart a service without SSHing in.

## 2. Goals, non-goals, anti-goals

**Goals (v1)**
- Fleet overview of all known Pis: online/offline, CPU %, RAM, disk,
  network throughput, uptime, CPU temperature, health flags.
- Per-Pi detail: hardware spec, live values, top processes, status of
  allowlisted services, historical charts.
- History: readings every 10 s, kept **in the agent's memory** for up to
  7 days, with chart ranges of 1h / 6h / 24h / 7d.
- Actions: reboot a Pi; start, stop or restart an allowlisted service.
- The dashboard remembers the Pis it knows (a local file), so there's no
  need to add or scan for them each time it starts.
- The dashboard can run on the owner's desktop (WSL2) **or** on a Pi,
  including both at once.

**Non-goals (v1)** (owner decisions)
- A database server. History lives only in each agent's memory and is
  **lost when the Pi or the agent restarts**. Owner: "for v1 they just live
  in memory".
- Alerts and notifications.
- Access from outside the home network.
- Arbitrary shell commands, log tailing, OS updates, bulk actions.
- `jetson-nano`: off-LAN, Python 3.6, no Pi health flags.

**Anti-goals** (a failure even if it works)
- An agent that noticeably loads a Pi. The target is under 2% CPU on
  average and under about 50 MB RSS **including a full 7 days of history**,
  because the Zero 2 has only about 270 MB free.
- Any writes to the Pis' SD cards during normal running (they are slow
  and cheap).
- A dashboard button that can affect services that aren't on the allowlist.
- One Pi being down slows or breaks the dashboard for the others.
- History with gaps just because the desktop was off.

## 3. Environment (verified 2026-11-06)

From `~/raspberry-pis.md`, checked live over SSH. All are aarch64 and reachable as
`yfrl` with key aliases in `~/.ssh/config`. IPs come from DHCP and can change.

| Alias | Board | OS / Python | RAM available | Notes |
|---|---|---|---|---|
| `rpi-zero-2` | Pi Zero 2 W | Debian 13 / 3.13 | **~270 MB** | root disk 76% used; Display HAT demo running |
| `rpi-3` | Pi 3 B | Debian 13 / 3.13 | ~680 MB | |
| `rpi-5` | Pi 5 | Debian 13 | n/a | **powered off** at present |
| `rpi-cm4` | CM4 | Debian 13 / 3.13 | ~7.5 GB | user service `oled-stats`, system service `cpu-performance` |
| `rpi-cm5` | CM5 | Ubuntu 24.04 / 3.12 | ~14.7 GB | `vcgencmd` present but needs `/dev/vcio` access; no `curl` |

- Every LAN Pi has internet access (PyPI).
- `sudo systemctl reboot` needs no password on the Pis, but any other sudo
  does. On the Debian Pis the sudo password is cached across sessions for
  about 15 minutes.
- The desktop is Windows + WSL2. WSL2 can reach LAN hosts over TCP by IP,
  but `.local` names don't resolve and it can't see mDNS or ARP. So discovery
  must be a TCP scan, not mDNS.
- The server at 192.168.0.102 (root SSH set up earlier) is **not used** by
  this design.

## 4. Architecture

```
 Browser ──▶ Dashboard (desktop and/or a Pi) ──HTTP :8765 + token──▶ agent on each Pi
               │                                                     (live stats,
               └─ pis.json (known Pis, last-known spec)              7-day in-memory history,
                                                                      actions)
```

- **Pull model.** The dashboard calls each agent. Agents don't know
  about the dashboard.
- The browser only talks to the dashboard. The dashboard calls agents, so
  the agent token never reaches the browser. *(Claude call.)*
- Calls to agents run concurrently with a short timeout of about 3 s, so a
  dead Pi only marks itself offline.

## 5. Components

### 5.1 Pi agent (Python ≥ 3.11, systemd service)
- **Dependencies: `psutil` only.** The HTTP server is the stdlib
  `http.server` (`ThreadingHTTPServer`). *(Claude call, for the Zero 2's RAM
  budget.)*
- **Sampling every 10 s** into an in-memory ring buffer that holds 7 days
  (60,480 samples). Fields per sample:
  - timestamp
  - CPU % (total) and load average (1 min)
  - memory used/total, swap used
  - root disk used %
  - network rx/tx bytes per second (non-loopback interfaces summed)
  - CPU temperature
  - throttle flags (bitmask)
  Store it compactly (for example, one `array` per field rather than a deque
  of dicts) so 7 days stays at a few MB. *(Approach left to the builder;
  the RSS target in §2 is binding.)*
- **Temperature** comes from `/sys/class/thermal` first. **Throttle flags**
  come from `vcgencmd get_throttled`. If that is missing or not permitted
  (rpi-cm5), they are reported as "unknown" rather than causing an error.
- **Identity:** a stable `id` from `/etc/machine-id`, plus hostname. It is
  used by the dashboard to recognise a Pi after its IP changes.
- **HTTP API**, port 8765. Everything except `/health` requires
  `Authorization: Bearer <token>`.
  - `GET /health` (no auth) returns only `{app: "rpidash-agent", id,
    hostname, version}`, enough for discovery. *(Claude call.)*
  - `GET /spec`: Pi model (`/proc/device-tree/model`), serial, CPU and core
    count, RAM total, root storage device and size, OS (`/etc/os-release`),
    kernel, arch, hostname, IPs and MACs, agent version, boot time.
  - `GET /live`: the latest sample plus the top 5 processes by CPU and the
    state of each allowlisted service.
  - `GET /history?range=1h|6h|24h|7d`: downsampled on the agent to about
    300 points per series (mean per bucket, max for temperature).
    *(Claude call.)*
  - `POST /reboot`
  - `POST /services/{name}/{start|stop|restart}`, which returns 403 unless
    `name` is allowlisted.
- **Config** `/etc/rpidash-agent.yaml` (or `.toml`, builder's choice; no extra
  dependency): token, port, interval, `services_allowlist`. Allowlist entries
  are system units (`cpu-performance`) or `yfrl` user units written as
  `user:oled-stats`, controlled with `systemctl --user -M yfrl@`. The
  allowlist is set **per Pi by the owner** at install time.
- Runs as **root** under systemd, because reboot and service control need it.
  *(Claude call, confirmed by owner. Kept safe by the token and allowlist
  and by not allowing arbitrary commands.)*
- **No disk writes** during normal running.
- Installed by `agent/install.sh`. It creates a venv in `/opt/rpidash-agent`,
  pip-installs `psutil`, writes the config and systemd unit, and enables the
  service. It is idempotent and safe to re-run for upgrades.

### 5.2 Dashboard (Python, FastAPI)
- Server-rendered HTML with a little vanilla JS. Charts use Chart.js
  (vendored or CDN). There is no frontend build step. *(Claude call.)*
- **Config** `dashboard.yaml`: agent token, scan subnet (default
  `192.168.0.0/24`), agent port.
- **Known-Pis registry** in a local JSON file next to the config
  (`pis.json`). For each Pi it stores `id`, hostname, last-known IP and
  port, the last fetched `/spec`, the date added and `last_seen`. It is
  loaded on start and written whenever it changes. Each dashboard instance
  keeps its own file, which can be copied between machines. *(Owner asked
  for this; the format is a Claude call.)*
- **Adding Pis:** an "Add" form (IP or hostname) and a "Scan network"
  button. The scan probes the subnet on port 8765 `/health` concurrently and
  offers any new agents found. A "Remove" button deletes a Pi from the list.
- **IP changes:** if a known Pi is unreachable, the dashboard rescans the
  subnet in the background, at most once every 5 minutes. If it finds the
  same `id` at a new IP, it updates `pis.json`. *(Claude call.)*
- **Overview page:** a card per known Pi, refreshed every 5 s. A Pi is
  **offline** if `/live` fails or times out; it still shows its name and
  last-known spec. Warning colours: temperature at or above 70 °C, disk at
  or above 90%, RAM at or above 90%, and any throttle flag. *(Thresholds
  are a Claude call and should be easy to change.)*
- **Pi detail page:** spec table, live values, top processes, service list
  with start/stop/restart buttons, a reboot button (confirmation dialog),
  and charts for temperature, CPU, RAM, disk and network with range buttons
  1h / 6h / 24h / 7d. The charts show "history since <boot/agent start>"
  so a short history after a reboot is clearly expected.
- **No login.** It's home network only. *(Claude call, confirmed by owner.)*
- Runs with `uvicorn` on the desktop (WSL2), or as a systemd service on a Pi.

## 6. Repo layout (Claude call)
```
agent/        agent package, install.sh, systemd unit, sample config
dashboard/    FastAPI app, templates, static, sample config
docs/
```

## 7. Rollout
Claude copies the agent over SSH (`scp`/`rsync` using the `~/.ssh/config`
aliases; no git remote is needed) and runs `install.sh`. The install needs
sudo, so the owner types the sudo password once per Pi.

Order: `rpi-cm4` first as the trial, then `rpi-3`, `rpi-zero-2` (check RSS
there) and `rpi-cm5`, then `rpi-5` when it's next powered on. The token is
generated once and never committed.

## 8. Testing
- Unit tests (pytest): metric collection with psutil and `vcgencmd` mocked;
  ring buffer wrap-around and downsampling; token and allowlist
  enforcement; registry load/save; IP-change re-discovery; offline
  handling.
- A local end-to-end run with the agent and dashboard on WSL. Actions are
  stubbed there.
- A manual check on `rpi-cm4`, plus RSS measured on `rpi-zero-2` after the
  buffer has filled. To check that quickly, a test option can shrink the
  interval.

## 9. Left to the builder
Exact library versions, internal code structure, ring-buffer
representation (within the RSS target), chart styling, config file
format, and how the token is distributed (it must not be committed).

## 10. Open items
- The per-Pi service allowlists, filled in by the owner at install time.
