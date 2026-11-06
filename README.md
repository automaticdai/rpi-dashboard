# rpi-dashboard

All your Pis, in one place. A small agent on each Raspberry Pi reports live stats, keeps
7 days of history in memory, and can reboot the Pi or restart allowlisted services. A web
dashboard shows every Pi at a glance, with charts and actions.

```
Browser ──▶ dashboard (desktop or a Pi) ──HTTP :8765 + token──▶ agent on each Pi
               └─ pis.json (the Pis it knows)
```

- **No database.** History lives in each agent's memory (about 3.5 MB for a full week) and
  restarts when the Pi or the agent restarts. Nothing is written to the SD card.
  Measured agent memory (RSS) on 2026-11-06: 27–29 MB on every board, Pi Zero 2 W included.
- **Pis are remembered.** The dashboard keeps `pis.json`, so you add a Pi once. If a Pi's
  DHCP address changes, the dashboard finds it again by its machine ID.
- **Safe actions.** Every call except `/health` needs the shared token. Services can only be
  started, stopped or restarted if they are in that Pi's allowlist.

## Make a token

```sh
mkdir -p ~/.config/rpidash && python3 -c "import secrets;print(secrets.token_urlsafe(32))" > ~/.config/rpidash/token
chmod 600 ~/.config/rpidash/token
```

Never commit it. The dashboard and every agent use the same token.

## Install the agent on a Pi

Needs Python 3.11 or newer, which Raspberry Pi OS (Debian 13) and Ubuntu 24.04 have.

```sh
rsync -a --delete --exclude tests --exclude '*.egg-info' agent/ <pi>:~/rpidash-agent/
ssh -t <pi> "cd ~/rpidash-agent && sudo RPIDASH_TOKEN=$(cat ~/.config/rpidash/token) \
  RPIDASH_SERVICES='cpu-performance,user:oled-stats' ./install.sh"
```

Running `install.sh` again upgrades the agent and keeps the existing `/etc/rpidash-agent.toml`.
The allowlist takes system units by name, or user units of `service_user` (default: the
sudo user) written as `user:<unit>`. To change it later, edit `/etc/rpidash-agent.toml`,
then run `sudo systemctl restart rpidash-agent`.

Check it's running: `curl http://<pi-ip>:8765/health`

## Run the dashboard

On the desktop (WSL works):

```sh
uv venv .venv && uv pip install -e dashboard
cp dashboard/dashboard.example.toml dashboard.toml   # set token = the token above
.venv/bin/python -m rpidash_dashboard --config dashboard.toml --port 8080
```

Open http://127.0.0.1:8080, then use **Scan network** or add a Pi by IP. Each dashboard
instance keeps its own `pis.json`, which you can copy between machines.

On a Pi: install into `/opt/rpidash-dashboard/venv`, put the config at
`/etc/rpidash/dashboard.toml` with `registry_path = "/var/lib/rpidash/pis.json"`, and use
`dashboard/rpidash-dashboard.service`.

## Develop

```sh
uv venv .venv && uv pip install -e agent -e dashboard pytest pytest-asyncio
.venv/bin/pytest
```

Design: `docs/superpowers/specs/2026-11-06-rpi-dashboard-design.md`
