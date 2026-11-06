# rpi-dashboard — Design

Date: 2026-11-06
Status: approved by owner 2026-11-06

## 1. Intent

The owner runs **10 Raspberry Pis on a home network**. They want one place to
see each Pi's hardware and live health, view recent history, and take basic
actions when something looks wrong. Today there is nothing: the repo holds
only a README ("All your PIs, in one place").

Success: open a browser on the desktop and immediately see which Pis are
up, how loaded they are, and whether any are hot, full or throttled. Then
click into one Pi to see its spec and charts such as "temperature over the
last hour", and reboot it or restart a service without SSHing in.

## 2. Goals, non-goals, anti-goals

**Goals (v1)**
- Fleet overview of all Pis: online/offline, CPU %, RAM, disk, network
  throughput, uptime, CPU temperature, health flags.
- Per-Pi detail: hardware spec, live values, top processes, status of
  important services, historical charts.
- History: readings every 10 s, kept for 7 days, with chart ranges of
  1h / 6h / 24h / 7d.
- Actions: reboot a Pi; start, stop or restart an allowlisted systemd service.
- The dashboard can run on the owner's desktop **or** on a Pi, including
  several instances at once, and all of them show the same data.

**Non-goals (v1)** (owner decided against these or deferred them)
- Alerts and notifications. Owner: "no alerts for now."
- Access from outside the home network.
- Running arbitrary shell commands, tailing logs, OS updates, bulk actions.
- `jetson-nano`. Owner: leave it out (off-LAN, Python 3.6, no Pi health flags).

**Anti-goals** (a failure even if it works)
- An agent that noticeably loads a Pi. The target is under 2% CPU on average
  and under about 60 MB RAM.
- A dashboard button that can stop or restart services that aren't on the allowlist.
- One Pi or the database being down breaks the rest. Agents must keep
  running and reconnect; the dashboard must still render the other Pis.
- History with gaps just because the desktop was off.

## 3. Environment (verified 2026-11-06)

- **Database server:** `192.168.0.102`, hostname `Databases`, Ubuntu 24.04
  LXC on Proxmox. 4 cores (Xeon E3-1265L v2, **has AVX**, so MongoDB 5+ is
  OK), 4 GB RAM, about 5.7 GB disk free. Docker is installed and running
  with no containers. Only SSH (22) is listening. Root SSH with the owner's
  desktop key works.
- **Desktop:** Windows with WSL2 (this repo is developed there).
- **Pis:** from `~/raspberry-pis.md`, checked live over SSH on 2026-11-06.
  All are aarch64 and reachable by SSH as `yfrl` with key aliases in
  `~/.ssh/config`. IPs come from DHCP and can change.

  | Alias | Board | OS / Python | RAM available | Notes |
  |---|---|---|---|---|
  | `rpi-zero-2` | Pi Zero 2 W | Debian 13 / 3.13 | **~270 MB** | root disk 76% used; Display HAT demo running |
  | `rpi-3` | Pi 3 B | Debian 13 / 3.13 | ~680 MB | |
  | `rpi-5` | Pi 5 | Debian 13 | n/a | **powered off** at present |
  | `rpi-cm4` | CM4 | Debian 13 / 3.13 | ~7.5 GB | user service `oled-stats`, system service `cpu-performance` |
  | `rpi-cm5` | CM5 | Ubuntu 24.04 / 3.12 | ~14.7 GB | `vcgencmd` present but needs `/dev/vcio` access |
  | `jetson-nano` | Jetson Nano (not a Pi, **out of scope**) | Ubuntu 18.04 / **3.6** | ~2.5 GB | **off-LAN**, Tailscale only; can't reach the DB server; no `vcgencmd` |

  Every LAN Pi can reach 192.168.0.102 and the internet (PyPI).
  `sudo systemctl reboot` needs no password on the Pis, but any other sudo
  does. On the Debian Pis the sudo password is cached across sessions for
  about 15 minutes. The fleet is smaller than the "10 Pis" first mentioned
  (see §9).

## 4. Architecture

```
 Pi agent (x10) ──insert reading every 10s──▶ MongoDB (192.168.0.102, Docker)
   ▲  HTTP :8765                                  │
   │  reboot / service actions (token)            │ read
   └──────────── Dashboard (desktop and/or a Pi) ◀┘ ◀── browser
```

- **Agents push, the dashboard reads.** The database is the single source of
  truth for both spec and history. The dashboard is stateless.
- **Actions go directly** from the dashboard to the agent on the target Pi.
  The dashboard finds the Pi's address in the database.
- **Self-registration.** Each agent writes its own host document, so the
  dashboard needs no list of Pis. *(Builder/Claude call.)*

## 5. Components

### 5.1 Database: MongoDB (owner's choice)
- Runs in Docker on 192.168.0.102 (`mongo:8` image, or `mongo:7` if 8
  misbehaves on this CPU) with a named volume and `--auth` on. The
  compose file lives in `deploy/mongo/`.
- Listens on the LAN at port 27017. It must not be exposed to the internet.
  *(Claude call: rely on the home router; no extra firewall in v1.)*
- Database `rpidash`:
  - `metrics`: a **time-series collection**, with `timeField: ts`,
    `metaField: host`, `granularity: seconds`, and
    `expireAfterSeconds: 604800` (7 days, auto-expiry).
  - `hosts`: one document per Pi with static spec, latest snapshot,
    `last_seen`, agent address/port and allowlisted services.
- Users:
  - `rpidash_agent` can only insert into `metrics` and insert/update `hosts`.
  - `rpidash_dashboard` is read-only on `rpidash`.
  - An admin user is used for setup only.

### 5.2 Pi agent (Python 3, systemd service)
- Libraries: only `psutil` and `pymongo`. The HTTP API uses the stdlib
  `http.server` (threaded), not FastAPI, because the Zero 2 has only about
  270 MB free. It targets Python ≥ 3.11. *(Claude call.)*
- **Every 10 s** it inserts one reading into `metrics`:
  - CPU % (total), load average
  - memory used/total, swap used
  - root disk used/total
  - network rx/tx bytes per second (all non-loopback interfaces summed)
  - CPU temperature
  - throttling flags from `vcgencmd get_throttled` (under-voltage,
    frequency capped, throttled, soft temp limit), current and since boot
- On each tick it also updates its `hosts` document with `last_seen`, the
  latest values, the **top 5 processes by CPU**, and the state of each
  allowlisted service. These live only on the host document, not in history.
- **On start, and hourly**, it refreshes the hardware spec in `hosts`:
  Pi model (`/proc/device-tree/model`), serial, CPU and core count, RAM
  total, storage device and size, OS (`/etc/os-release`), kernel, arch,
  hostname, IP and MAC per interface, and agent version.
- **HTTP API** on port 8765. Every request needs
  `Authorization: Bearer <shared token>`:
  - `POST /reboot`
  - `POST /services/{name}/{start|stop|restart}`, which returns 403 unless
    `name` is in the local allowlist
  - `GET /health`
- Config file `/etc/rpidash-agent.yaml` holds the Mongo URI, token, interval,
  and `services_allowlist`. The allowlist is set **per Pi by the owner**.
  Entries are system units (`cpu-performance`) or user units for `yfrl`,
  written `user:oled-stats`. User units are controlled with
  `systemctl --user -M yfrl@`. *(Claude call.)*
- Temperature comes from `/sys/class/thermal` first, then `vcgencmd`. If
  `vcgencmd` is missing or not permitted, throttle flags are reported as
  "unknown" rather than causing an error.
- Runs as root under systemd, because reboot and service control need it.
  *(Claude call, confirmed by owner. It is kept safe by the token and allowlist and by not
  allowing arbitrary commands.)*
- If MongoDB is unreachable, it drops readings and keeps retrying with
  backoff. There is no disk buffering. *(Claude call, confirmed by owner. Gaps only during DB
  outages.)*
- Installed by `agent/install.sh`. It creates a venv in `/opt/rpidash-agent`,
  pip-installs from PyPI, writes the config and systemd unit, and enables the
  service. It is idempotent and safe to re-run for upgrades.
- **Rollout:** Claude copies the agent over SSH (`scp`/`rsync` using the
  `~/.ssh/config` aliases; no git remote is needed) and runs the install.
  The install needs sudo, so the owner types the sudo password once per Pi.
  Order: `rpi-cm4` first as the trial, then `rpi-3`, `rpi-zero-2` and `rpi-cm5`,
  then `rpi-5` when it's next powered on.

### 5.3 Dashboard (Python, FastAPI)
- Server-rendered HTML with a little vanilla JS. Charts use Chart.js from a
  CDN, or vendored. *(Claude call. No frontend build step.)*
- Config `dashboard.yaml` holds the Mongo URI (read-only user) and the agent
  token.
- **Overview page:** a card per Pi from `hosts`, refreshed every 5 s.
  A Pi shows **offline** if `last_seen` is more than 30 s old. Warning colours:
  temperature at or above 70 °C, disk at or above 90%, RAM at or above 90%,
  any throttle flag. *(Thresholds are a Claude call and should be easy to
  change.)*
- **Pi detail page:** spec table, live values, top processes, service list
  with start/stop/restart buttons, a reboot button (confirmation dialog),
  and charts for temperature, CPU, RAM, disk and network with range buttons
  1h / 6h / 24h / 7d. Longer ranges are downsampled in MongoDB
  (`$dateTrunc` buckets, about 300 points per chart).
- **No login.** It's only on the home network. *(Claude call, confirmed by owner.)*
- Runs as `uvicorn` on the desktop (WSL), or as a systemd service on a Pi.

## 6. Repo layout (Claude call)
```
agent/        Pi agent package, install.sh, systemd unit, sample config
dashboard/    FastAPI app, templates, static, sample config
deploy/mongo/ docker-compose.yml + init script (collections, users)
docs/
```

## 7. Testing
- Unit tests (pytest) for metric collection with psutil and `vcgencmd`
  mocked, action allowlist and token enforcement, offline detection, and
  downsampling queries.
- An integration test against a throwaway MongoDB container.
- A manual check on one real Pi before rolling out to all 10.

## 8. Left to the builder
Exact library versions, code structure within each component, chart
styling, how the token is generated and distributed (it must not be
committed), and retry and backoff details.

## 9. Open items
- The per-Pi service allowlists, filled in by the owner at install time.
