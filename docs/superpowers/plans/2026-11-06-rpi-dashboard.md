# rpi-dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A lightweight agent on each Raspberry Pi (live stats, 7-day in-memory history, reboot/service actions) and a dashboard that remembers known Pis and shows them all in one browser page.

**Architecture:** Pull model. Each Pi runs `rpidash_agent`, a stdlib HTTP server on :8765 that samples every 10 s into a fixed-size in-memory ring buffer. `rpidash_dashboard` is a FastAPI app. It keeps a `pis.json` registry, calls agents concurrently with a 3 s timeout, and serves server-rendered pages with vanilla JS and Chart.js. The browser never sees the agent token.

**Tech Stack:** Python ≥ 3.11. Agent: `psutil` only, plus the stdlib (`http.server`, `tomllib`, `array`, `subprocess`). Dashboard: `fastapi`, `uvicorn`, `httpx`, `jinja2`, and Chart.js 4.4.1 vendored. Tests: `pytest`, `pytest-asyncio`. Dev env: `uv`, with Python 3.12 on WSL.

**Spec:** `docs/superpowers/specs/2026-11-06-rpi-dashboard-design.md`

## Global Constraints

- Agent runtime dependency: **`psutil` only**. Agent code must run on Python 3.11, 3.12 and 3.13.
- Agent RSS is **under about 50 MB with a full 7 days of history**. History storage is 60,480 samples (7 days × 8,640).
- Agent: **no disk writes** during normal running.
- Agent port is `8765`. Every endpoint except `GET /health` requires `Authorization: Bearer <token>`.
- `/health` returns only `{app: "rpidash-agent", id, hostname, version}`, where `id` comes from `/etc/machine-id`.
- History ranges are `1h|6h|24h|7d` (3600 / 21600 / 86400 / 604800 s), downsampled to about 300 points. Each bucket is a mean, except temperature, which is a max.
- Service actions are only `start|stop|restart`, and only for allowlisted entries. Entries are `name` (system unit) or `user:name` (controlled with `systemctl --user -M yfrl@`). Commands are passed as argument lists; never use `shell=True`.
- Dashboard → agent timeout is 3 s, and calls run concurrently. One dead Pi must not delay the others.
- Dashboard rediscovery scans at most once every 300 s. The default subnet is `192.168.0.0/24`.
- Warning thresholds (configurable): temperature ≥ 70 °C, disk ≥ 90%, RAM ≥ 90%, any throttle flag.
- The overview refreshes every 5 s. There is no login. The token is never committed and never sent to the browser.
- Config files are TOML, read with the stdlib `tomllib`, for both components. *(Builder's choice per spec §9.)*

## Review Focus

1. **One Pi hangs or is off.** The overview must still return within about 3.5 s, with the other Pis online. Test in Task 8.
2. **`pis.json` missing, empty or corrupt.** The dashboard should start with an empty registry and keep the bad file as `pis.json.bak` instead of crashing. Test in Task 6.
3. **Hostile service names**, such as `../reboot`, `ssh;rm -rf /`, URL-encoded names, or an allowlisted name with a disallowed action. The agent must return 403 or 404 and must never call the runner. Tests in Tasks 4 and 5.
4. **History right after boot, and after the ring buffer wraps.** The agent returns only the samples it has, with the correct `since`, and never errors on an empty buffer. Test in Task 3.
5. **Wrong token on the dashboard.** The Pi should show as "auth failed", not "offline", so the cause is obvious. Tests in Tasks 7 and 8.

---

## File Structure

```
agent/
  pyproject.toml                      package rpidash_agent, dep psutil
  rpidash_agent/__init__.py           __version__ = "0.1.0"
  rpidash_agent/config.py             AgentConfig, load_config
  rpidash_agent/collect.py            Sample, Collector, read_* helpers
  rpidash_agent/history.py            History ring buffer + query/downsample
  rpidash_agent/services.py           allowlist parsing, systemctl commands
  rpidash_agent/server.py             HTTP handler + make_server
  rpidash_agent/__main__.py           entrypoint: sampler thread + serve
  rpidash-agent.service               systemd unit
  rpidash-agent.example.toml
  install.sh
  tests/...
dashboard/
  pyproject.toml                      package rpidash_dashboard
  rpidash_dashboard/__init__.py
  rpidash_dashboard/config.py         DashboardConfig, load_config
  rpidash_dashboard/registry.py       Pi, Registry (pis.json)
  rpidash_dashboard/client.py         AgentClient, AgentError, AgentAuthError
  rpidash_dashboard/discovery.py      scan, Rediscovery
  rpidash_dashboard/status.py         health_warnings
  rpidash_dashboard/app.py            create_app (API + pages)
  rpidash_dashboard/__main__.py       uvicorn entrypoint
  rpidash_dashboard/templates/{base,overview,detail}.html
  rpidash_dashboard/static/{app.js,style.css,chart.umd.min.js}
  rpidash-dashboard.service
  dashboard.example.toml
  tests/...
README.md                             run/install instructions
```

---

### Task 1: Dev environment + agent config

**Files:**
- Create: `agent/pyproject.toml`, `agent/rpidash_agent/__init__.py`, `agent/rpidash_agent/config.py`, `agent/tests/test_config.py`, `.gitignore` (`.venv/`, `__pycache__/`, `*.toml.local`, `pis.json*`, `dashboard.toml`)

**Interfaces:**
- Produces:
  - `AgentConfig` dataclass with fields `token: str`, `port: int = 8765`, `interval: float = 10.0`, `services_allowlist: list[str] = []` and `service_user: str = "yfrl"`.
  - `load_config(path: Path) -> AgentConfig`, which raises `ValueError` if `token` is missing or empty.

- [ ] **Step 1: Create the dev venv.** Run `uv venv .venv && uv pip install -e agent pytest pytest-asyncio` from the repo root. Expected: install succeeds.
- [ ] **Step 2: Write failing tests** in `agent/tests/test_config.py`:
  - `test_load_full`: write TOML containing `token="t"`, `port=9000`, `interval=5` and `services_allowlist=["cpu-performance","user:oled-stats"]`. Assert each field is set, and that `service_user == "yfrl"`.
  - `test_defaults`: TOML with only `token="t"` gives `port == 8765`, `interval == 10.0` and `services_allowlist == []`.
  - `test_missing_token_raises` and `test_empty_token_raises`: both raise `pytest.raises(ValueError)`.
- [ ] **Step 3: Run** `.venv/bin/pytest agent/tests/test_config.py -v`. Expected: FAIL (import error).
- [ ] **Step 4: Implement** `load_config` with `tomllib`.
- [ ] **Step 5: Run the tests again.** Expected: PASS.
- [ ] **Step 6: Commit** with `feat(agent): config loading`.

### Task 2: Agent collectors

**Files:**
- Create: `agent/rpidash_agent/collect.py`, `agent/tests/test_collect.py`

**Interfaces:**
- Produces:
  - `Sample` (frozen dataclass), with fields in this order: `ts: float`, `cpu_pct: float`, `load1: float`, `mem_used: int`, `mem_total: int`, `swap_used: int`, `disk_pct: float`, `net_rx_bps: float`, `net_tx_bps: float`, `temp_c: float | None` and `throttled: int | None`.
  - `FIELDS: tuple[str, ...]`, the field names in order.
  - `class Collector`, with `sample(now: float) -> Sample`. Network rates come from the change in summed non-loopback `psutil.net_io_counters(pernic=True)` counters since the previous call. The first call gives 0.0, and a negative change (counter reset) is clamped to 0.0. CPU uses `psutil.cpu_percent(None)`.
  - `read_temperature() -> float | None`: the maximum of `/sys/class/thermal/thermal_zone*/temp` divided by 1000, falling back to `vcgencmd measure_temp`. Returns `None` if both fail.
  - `parse_throttled(text: str) -> int | None` and `read_throttled() -> int | None`. The latter runs `["vcgencmd","get_throttled"]` with a 2 s timeout and returns `None` on any error.
  - `read_spec() -> dict`, with keys `model`, `serial`, `cpu`, `cores`, `ram_total`, `root_device`, `root_size`, `os`, `kernel`, `arch`, `hostname`, `interfaces` (a list of `{name, ipv4, mac}`), `agent_version` and `boot_time`.
  - `machine_id() -> str`, read from `/etc/machine-id` and stripped.
  - `top_processes(n: int = 5) -> list[dict]`, each `{pid, name, cpu_pct, mem_pct}`, sorted by `cpu_pct` descending.
  - Every function that touches files takes an optional `root: Path = Path("/")` argument so tests can use `tmp_path`.

- [ ] **Step 1: Write failing tests:**
  - `test_parse_throttled`: `parse_throttled("throttled=0x50005\n") == 0x50005`, `parse_throttled("throttled=0x0") == 0`, and `parse_throttled("garbage") is None`.
  - `test_read_temperature_sysfs`: two zone files containing `48000` and `51500` give `51.5`.
  - `test_read_temperature_none`: no zones plus `vcgencmd` missing (monkeypatch `subprocess.run` to raise `FileNotFoundError`) gives `None`.
  - `test_read_throttled_missing_vcgencmd`: `FileNotFoundError` gives `None`.
  - `test_net_rates`: monkeypatch `psutil.net_io_counters` to return `lo` and `eth0`. The first `sample(100.0)` has `net_rx_bps == 0.0`. With `eth0.bytes_recv` up by 10,000, `sample(110.0)` has `net_rx_bps == 1000.0`, and `lo` is ignored. A counter decrease gives `0.0`.
  - `test_machine_id`: a file containing `"abc\n"` gives `"abc"`.
  - `test_read_spec_keys`: run against the real host. All listed keys are present and `cores >= 1`.
- [ ] **Step 2: Run** `.venv/bin/pytest agent/tests/test_collect.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `collect.py`. Read the model from `<root>/proc/device-tree/model` (NUL-stripped), falling back to `platform.machine()`. Read the OS from `<root>/etc/os-release` `PRETTY_NAME`.
- [ ] **Step 4: Run the tests again.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat(agent): metric collectors`.

### Task 3: History ring buffer

**Files:**
- Create: `agent/rpidash_agent/history.py`, `agent/tests/test_history.py`

**Interfaces:**
- Consumes: `Sample` and `FIELDS` from Task 2.
- Produces:
  - `RANGES = {"1h": 3600, "6h": 21600, "24h": 86400, "7d": 604800}`
  - `class History(capacity: int = 60480)` with:
    - `append(s: Sample) -> None`
    - `__len__() -> int`
    - `latest() -> Sample | None`
    - `oldest_ts() -> float | None`
    - `query(range_s: int, now: float, points: int = 300) -> dict`, which returns `{"since": oldest_ts_or_None, "ts": [...], <each field except ts>: [...]}`.
    - `nbytes() -> int`, the total size of its buffers.
  - Storage: one `array.array` per field, preallocated to `capacity`, with a write index and count. Types: `'d'` for `ts`; `'f'` for floats; `'q'` for the memory ints; `'f'` for `temp_c`, with `NaN` meaning `None`; `'i'` for `throttled`, with `-1` meaning `None`.
  - Downsampling: bucket width is `range_s / points`, and only samples with `ts >= now - range_s` are included. Each output point's `ts` is the bucket's mean `ts`. Fields use the mean, except `temp_c`, which uses the max ignoring NaN. `throttled` uses the bitwise OR of known values, or `None` if all are unknown. Empty buckets are omitted. Output uses `None` in place of NaN or -1.

- [ ] **Step 1: Write failing tests:**
  - `test_empty`: `History(10).query(3600, now=1000)` returns `since is None` and `ts == []`.
  - `test_wraps`: with `capacity=5`, append 8 samples with `ts` from 1 to 8. Then `len == 5`, `oldest_ts() == 4.0` and `latest().ts == 8.0`.
  - `test_query_window_and_since`: 400 samples every 10 s (`ts` from 0 to 3990); `query(3600, now=3990, points=300)` has `len(ts) <= 300`, `min(ts) >= 390` and `since == 0.0`.
  - `test_downsample_mean_and_max`: two samples in one bucket with `cpu_pct` of 10 and 30 and `temp_c` of 50 and 60 give one point with `cpu_pct == 20` and `temp_c == 60`.
  - `test_unknown_values`: `temp_c=None` and `throttled=None` round-trip as `None`. A bucket with `throttled` values 0x1 and 0x4 gives 0x5.
  - `test_full_week_memory`: `History()` with 60,480 appends has `nbytes() < 4_000_000`.
- [ ] **Step 2: Run** `.venv/bin/pytest agent/tests/test_history.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `history.py`.
- [ ] **Step 4: Run the tests again.** Expected: PASS. Also check that `query(604800, ...)` on a full buffer takes under 1 s. Add `test_query_speed`, asserting elapsed time < 1.0 using `time.perf_counter`.
- [ ] **Step 5: Commit** with `feat(agent): in-memory history ring buffer`.

### Task 4: Service allowlist and commands

**Files:**
- Create: `agent/rpidash_agent/services.py`, `agent/tests/test_services.py`

**Interfaces:**
- Produces:
  - `ACTIONS = frozenset({"start", "stop", "restart"})`
  - `Runner = Callable[[list[str]], subprocess.CompletedProcess]`. The default is `default_runner(cmd)`, which calls `subprocess.run(cmd, capture_output=True, text=True, timeout=30)`.
  - `class Services(allowlist: list[str], user: str = "yfrl", runner: Runner = default_runner)` with:
    - `allowed(entry: str) -> bool`, an exact string match against the allowlist.
    - `command(entry: str, action: str) -> list[str]`. A system unit gives `["systemctl", action, name]`, and `user:name` gives `["systemctl", "--user", "-M", f"{user}@", action, name]`. It raises `PermissionError` if the entry isn't allowed or the action isn't in `ACTIONS`.
    - `run(entry, action) -> tuple[bool, str]`: `ok = returncode == 0`, together with stdout and stderr.
    - `states() -> list[dict]`: `[{name: entry, state: <is-active output>}]` for each allowlisted entry, using `is-active` in place of the action. The state is `"unknown"` on error.
    - `reboot() -> None`, which runs `["systemctl", "reboot"]`.

- [ ] **Step 1: Write failing tests** using a fake runner that records commands:
  - `test_system_command`: `command("cpu-performance","restart") == ["systemctl","restart","cpu-performance"]`.
  - `test_user_command`: `command("user:oled-stats","stop") == ["systemctl","--user","-M","yfrl@","stop","oled-stats"]`.
  - `test_rejects_not_allowlisted` and `test_rejects_bad_action`: calling `run` with `"ssh;rm -rf /"`, `"../reboot"`, or an allowed name with `"enable"`, raises `PermissionError`, and the fake runner is never called.
  - `test_states_unknown_on_error`: a runner that raises `subprocess.TimeoutExpired` gives `state == "unknown"`.
- [ ] **Step 2: Run** `.venv/bin/pytest agent/tests/test_services.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `services.py`.
- [ ] **Step 4: Run the tests again.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat(agent): allowlisted service control`.

### Task 5: Agent HTTP server + entrypoint

**Files:**
- Create: `agent/rpidash_agent/server.py`, `agent/rpidash_agent/__main__.py`, `agent/tests/test_server.py`

**Interfaces:**
- Consumes: `AgentConfig`/`load_config` (Task 1), `Collector`, `read_spec`, `machine_id` and `top_processes` (Task 2), `History` and `RANGES` (Task 3), and `Services` (Task 4).
- Produces: `make_server(cfg: AgentConfig, history: History, services: Services, *, host: str = "0.0.0.0", spec_fn=read_spec, id_fn=machine_id, top_fn=top_processes, reboot_delay: float = 1.0) -> ThreadingHTTPServer`, plus these routes:
  - `GET /health` → 200 `{"app":"rpidash-agent","id","hostname","version"}`. No auth.
  - `GET /spec` → 200 with the spec dict.
  - `GET /live` → 200 `{"sample": <latest as dict or null>, "top": [...], "services": [...], "uptime_s": float}`.
  - `GET /history?range=1h` → 200 with the output of `History.query(RANGES[r], time.time())`. An unknown range gives 400.
  - `POST /reboot` → 202 `{"ok": true}`, then `services.reboot()` after `reboot_delay`, using `threading.Timer`.
  - `POST /services/<entry>/<action>` → 200 `{"ok", "output"}`. Gives 403 if `PermissionError`. `<entry>` is URL-decoded, and must match `^(user:)?[A-Za-z0-9@._-]+$`, otherwise 404.
  - Missing or wrong bearer token gives 401 (compare with `hmac.compare_digest`). An unknown path gives 404. Responses are JSON. Request logging goes to stderr only, so it doesn't write to disk.
- `__main__.main(argv=None)`: `--config` defaults to `/etc/rpidash-agent.toml`. It builds `Collector`, `History` and `Services`, starts a daemon thread that calls `history.append(collector.sample(time.time()))` every `cfg.interval` seconds (exceptions are logged and the loop continues), and then calls `serve_forever()`.

- [ ] **Step 1: Write failing tests.** Start `make_server` on port 0 in a thread, using a fake runner, and call it with `urllib.request`:
  - `test_health_no_auth`: 200, the keys are exactly `{"app","id","hostname","version"}`, and `app == "rpidash-agent"`.
  - `test_auth_required`: `/spec`, `/live`, `/history?range=1h` and `POST /reboot` without a token, or with a wrong one, give 401.
  - `test_live_and_history`: after appending 3 samples, `/live` returns `sample.ts` equal to the latest, and `/history?range=1h` returns `len(ts) == 3`. `/history?range=2h` gives 400.
  - `test_service_action_ok`: an allowlisted `cpu-performance` with `restart` gives 200 and the runner received the expected command.
  - `test_service_hostile`: `POST /services/..%2Freboot/restart` gives 404. `POST /services/sshd/restart` (not allowlisted) gives 403. `POST /services/cpu-performance/enable` gives 403. In all three cases the runner is never called.
  - `test_reboot_delayed`: with `reboot_delay=0.05`, the response is 202 and the runner receives `["systemctl","reboot"]` within 1 s.
- [ ] **Step 2: Run** `.venv/bin/pytest agent/tests/test_server.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `server.py` and `__main__.py`.
- [ ] **Step 4: Run** `.venv/bin/pytest agent -v`. Expected: all PASS.
- [ ] **Step 5: Smoke test.** Write a temporary config containing `token="dev"` and `interval=1` in the scratchpad. Run `.venv/bin/python -m rpidash_agent --config <it>` in the background, wait 3 s, then run `curl -s localhost:8765/health` and `curl -s -H 'Authorization: Bearer dev' 'localhost:8765/history?range=1h'`. Expected: valid JSON with at least 2 points. Stop the agent afterwards.
- [ ] **Step 6: Commit** with `feat(agent): HTTP API and entrypoint`.

### Task 6: Agent packaging + dashboard registry

These are kept together as small independent pieces, so the next tasks can build on the registry.

**Files:**
- Create: `agent/rpidash-agent.service`, `agent/rpidash-agent.example.toml`, `agent/install.sh`
- Create: `dashboard/pyproject.toml`, `dashboard/rpidash_dashboard/__init__.py`, `dashboard/rpidash_dashboard/config.py`, `dashboard/rpidash_dashboard/registry.py`, `dashboard/tests/test_registry.py`, `dashboard/tests/test_config.py`

**Interfaces:**
- Unit file `rpidash-agent.service`:
  - `ExecStart=/opt/rpidash-agent/venv/bin/python -m rpidash_agent --config /etc/rpidash-agent.toml`
  - `Restart=always`, `RestartSec=5`, `User=root`, `MemoryMax=80M`, `Nice=10`
- `install.sh` is run with sudo from a directory that contains the `agent/` sources. It:
  1. Creates `/opt/rpidash-agent/venv` if it's missing.
  2. Runs `pip install --upgrade <agent dir>`.
  3. Writes `/etc/rpidash-agent.toml` (mode 600) **only if it doesn't exist**, using `RPIDASH_TOKEN` and `RPIDASH_SERVICES` (comma-separated) from the environment, and failing if `RPIDASH_TOKEN` is unset.
  4. Installs the unit and runs `systemctl daemon-reload` and `systemctl enable --now rpidash-agent`, then `systemctl restart rpidash-agent`.
  5. Uses `set -euo pipefail` and can be run again for upgrades.
- Dashboard config:
  - `DashboardConfig` fields: `token: str`, `subnet: str = "192.168.0.0/24"`, `agent_port: int = 8765`, `registry_path: Path`, `warn_temp_c: float = 70`, `warn_disk_pct: float = 90` and `warn_mem_pct: float = 90`.
  - `registry_path` defaults to `pis.json` next to the config file.
  - `load_config(path: Path) -> DashboardConfig`, which raises `ValueError` without a token.
- Registry:
  - `Pi` dataclass with fields `id`, `hostname`, `host`, `port: int`, `spec: dict | None`, `added: str` (ISO 8601) and `last_seen: str | None`.
  - `class Registry(path: Path)` with `load()`, `save()` (an atomic write to a temp file followed by `os.replace`), `all() -> list[Pi]` (sorted by hostname), `get(id) -> Pi | None`, `upsert(pi) -> None` and `remove(id) -> bool`.
  - `load()` on a missing file gives an empty registry. On invalid JSON or a bad shape it moves the file to `<path>.bak`, logs a warning and gives an empty registry.

- [ ] **Step 1: Write failing tests:**
  - Config: the defaults are as listed, `registry_path` resolves next to the config file, and a missing token raises an error.
  - `test_registry_roundtrip`: `upsert` two Pis, `save`, then a new `Registry(path).load()` has the same `all()`, sorted by hostname.
  - `test_registry_missing_file`: gives an empty registry.
  - `test_registry_corrupt`: file contents `"{not json"` give `all() == []`, `pis.json.bak` exists with the original content, and `save()` then works.
  - `test_upsert_updates_host`: upserting the same `id` with a new `host` replaces the old entry.
  - `test_remove`: returns `True`, then `False`.
- [ ] **Step 2: Run** `uv pip install -e dashboard && .venv/bin/pytest dashboard/tests -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the dashboard `config.py` and `registry.py`. `dashboard/pyproject.toml` depends on `fastapi`, `uvicorn`, `httpx` and `jinja2`.
- [ ] **Step 4: Write** the unit, example config and `install.sh`. Run `bash -n agent/install.sh`. Expected: no output.
- [ ] **Step 5: Run** `.venv/bin/pytest dashboard/tests -v`. Expected: PASS.
- [ ] **Step 6: Commit** with `feat: agent packaging and dashboard registry`.

### Task 7: Dashboard agent client + discovery

**Files:**
- Create: `dashboard/rpidash_dashboard/client.py`, `dashboard/rpidash_dashboard/discovery.py`, `dashboard/tests/test_client.py`, `dashboard/tests/test_discovery.py`

**Interfaces:**
- Produces:
  - Exceptions `AgentError(Exception)` and `AgentAuthError(AgentError)`. A 401 response raises `AgentAuthError`; a timeout, connection error or other non-2xx response raises `AgentError`.
  - `class AgentClient(token: str, port: int = 8765, timeout: float = 3.0, transport: httpx.AsyncBaseTransport | None = None)`, all `async`:
    - `health(host) -> dict`, which raises `AgentError` unless `app == "rpidash-agent"`.
    - `spec(host) -> dict`
    - `live(host) -> dict`
    - `history(host, range_: str) -> dict`
    - `reboot(host) -> dict`
    - `service_action(host, entry, action) -> dict`, with `entry` URL-quoted using `safe=""`.
    - `aclose()`
  - `async def scan(subnet: str, client: AgentClient, concurrency: int = 64) -> list[tuple[str, dict]]`, which returns `(host_ip, health)` for every responding agent, with all hosts probed concurrently.
  - `class Rediscovery(registry: Registry, client: AgentClient, subnet: str, min_interval: float = 300.0, clock=time.monotonic)` with `async maybe_run(unreachable_ids: set[str]) -> dict[str, str]`. It returns `{id: new_host}` for Pis it moved. It does nothing (and returns `{}`) if `unreachable_ids` is empty or less than `min_interval` has passed since the last run. When it finds a matching `id` at a new IP, it updates `pi.host` and saves the registry.

- [ ] **Step 1: Write failing tests** using `httpx.MockTransport`:
  - `test_auth_header_sent` checks the request carries `Authorization: Bearer tok`.
  - `test_401_raises_auth_error` and `test_timeout_raises_agent_error`, where the handler raises `httpx.ConnectTimeout`.
  - `test_health_rejects_non_agent`, where the response is `{"app":"other"}`.
  - `test_service_action_quotes`: the entry `user:oled-stats` produces the path `/services/user%3Aoled-stats/restart`.
  - `test_scan_finds_agents`: a mock transport answers only `192.168.9.5` on the `/30` subnet `192.168.9.4/30`, so the result is `[("192.168.9.5", {...})]`.
  - `test_rediscovery_moves_host_and_rate_limits`: a registry Pi `id="A"` at `.5`, with the scan finding `A` at `.6`, gives `{"A": "192.168.9.6"}`, and the saved registry has the new host. A second call within 300 s on a fake clock returns `{}` without scanning.
- [ ] **Step 2: Run** `.venv/bin/pytest dashboard/tests/test_client.py dashboard/tests/test_discovery.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `client.py` and `discovery.py`. `scan` uses `ipaddress.ip_network(subnet).hosts()` and an `asyncio.Semaphore`, and treats any `AgentError` as "not an agent".
- [ ] **Step 4: Run the tests again.** Expected: PASS.
- [ ] **Step 5: Commit** with `feat(dashboard): agent client and discovery`.

### Task 8: Dashboard API

**Files:**
- Create: `dashboard/rpidash_dashboard/status.py`, `dashboard/rpidash_dashboard/app.py`, `dashboard/rpidash_dashboard/__main__.py`, `dashboard/tests/test_app.py`, `dashboard/tests/test_status.py`

**Interfaces:**
- Consumes: Tasks 6 and 7.
- Produces:
  - `health_warnings(sample: dict | None, cfg: DashboardConfig) -> list[str]`. It returns warnings from `"hot"` (`temp_c >= warn_temp_c`), `"disk"` (`disk_pct >= warn_disk_pct`), `"mem"` (`mem_used/mem_total*100 >= warn_mem_pct`) and `"throttled"` (`throttled` is not `None` and not 0). `None` values are skipped.
  - `create_app(cfg: DashboardConfig, client: AgentClient | None = None, registry: Registry | None = None) -> FastAPI`, with these JSON routes:
    - `GET /api/pis` → a list of `{id, hostname, host, status: "online"|"offline"|"auth_failed", live: dict|null, spec, warnings, last_seen}`. Calls to all Pis run concurrently with `asyncio.gather`. A successful call updates `last_seen`, and saves the registry at most once per minute. After that the request schedules `Rediscovery.maybe_run(offline_ids)` as a background task. It never awaits it inside the request.
    - `GET /api/pis/{id}/history?range=1h` → agent history. Gives 404 for an unknown id and 502 if the agent call fails.
    - `POST /api/pis` with body `{"host": str}` → calls `health` and `spec`, then `upsert`. Returns the Pi. Gives 502 if no agent is found there.
    - `DELETE /api/pis/{id}` → 204, or 404.
    - `POST /api/scan` → `[{host, id, hostname, known: bool}]`.
    - `POST /api/pis/{id}/reboot` and `POST /api/pis/{id}/services/{entry}/{action}` → proxied to the agent. Agent 403 is passed through as 403, and an `AgentError` gives 502.
    - Responses never contain the token.
  - `__main__.main()`: `--config dashboard.toml`, `--host 127.0.0.1`, `--port 8080`. Runs `uvicorn.run(create_app(...))`.

- [ ] **Step 1: Write failing tests** using `fastapi.testclient.TestClient` and an `AgentClient` with `httpx.MockTransport`:
  - `test_status_warnings`: `temp_c=70` gives `"hot"`, `disk_pct=89.9` gives none, `throttled=None` gives none, `throttled=0x4` gives `"throttled"`, and `sample=None` gives `[]`.
  - `test_list_mixed_status`: three Pis, where A answers, B hangs (the handler sleeps 10 s, with a client timeout of 0.5 s) and C returns 401. The statuses are `online`, `offline` and `auth_failed`, and the response comes back in under 2 s.
  - `test_add_pi`: posting `{"host":"192.168.9.5"}` stores it in the registry with its `spec`.
  - `test_add_pi_not_agent`: gives 502, and the registry is unchanged.
  - `test_service_proxy_403`: the agent returns 403, so the response is 403.
  - `test_no_token_in_responses`: the token string doesn't appear in any response body.
- [ ] **Step 2: Run** `.venv/bin/pytest dashboard/tests/test_app.py dashboard/tests/test_status.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `status.py`, the API routes in `app.py`, and `__main__.py`.
- [ ] **Step 4: Run** `.venv/bin/pytest dashboard -v`. Expected: all PASS.
- [ ] **Step 5: Commit** with `feat(dashboard): JSON API`.

### Task 9: Dashboard pages

**Files:**
- Create: `dashboard/rpidash_dashboard/templates/{base,overview,detail}.html`, `dashboard/rpidash_dashboard/static/{app.js,style.css,chart.umd.min.js}`
- Modify: `dashboard/rpidash_dashboard/app.py` (add `GET /` and `GET /pi/{id}`, and mount `/static`)
- Test: `dashboard/tests/test_pages.py`

**Interfaces:**
- Consumes: the `/api/*` routes from Task 8.
- Vendor Chart.js 4.4.1 with `curl -fsSL https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js -o dashboard/rpidash_dashboard/static/chart.umd.min.js`.
- **Overview (`/`)**:
  - A grid of cards from `GET /api/pis`, refreshed every 5 s.
  - Each card shows the hostname, the model (from the spec), a status badge (online, offline or auth failed), CPU %, RAM %, disk %, temperature, net rx/tx, uptime and warning chips.
  - Offline cards are greyed but still show the model, RAM and OS from the saved spec.
  - The header has an "Add Pi" form and a "Scan network" button. The scan results list each new agent with an "Add" button.
  - Each card has a "Remove" option behind a confirmation.
- **Detail (`/pi/{id}`)**:
  - A spec table, live values refreshed every 5 s, the top processes table, and the service list with state plus start, stop and restart buttons.
  - A reboot button that asks for confirmation with `confirm()`.
  - Range buttons for 1h, 6h, 24h and 7d, which drive five line charts: temperature, CPU %, RAM %, disk % and network rx/tx.
  - A caption "History since <since, local time>". If `since` is later than the start of the chosen range, it also says "(Pi rebooted or agent restarted)".
  - Action results appear as a short inline message.
- Theme: light and dark mode with `prefers-color-scheme`, and a single-column layout below 600 px.

- [ ] **Step 1: Write failing tests:**
  - `test_overview_renders`: `GET /` gives 200, and the body contains `id="pis"` and `/static/app.js`.
  - `test_detail_renders`: for a known id, `GET /pi/A` gives 200 and the body contains `id="charts"`. An unknown id gives 404.
  - `test_static_chartjs`: `GET /static/chart.umd.min.js` gives 200.
- [ ] **Step 2: Run** `.venv/bin/pytest dashboard/tests/test_pages.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the templates, JS and CSS.
- [ ] **Step 4: Run the tests again.** Expected: PASS.
- [ ] **Step 5: Run end to end locally.** Start the agent from the Task 5 smoke test (`token="dev"`, `interval=1`) and the dashboard with `token="dev"`. Add `127.0.0.1` in the UI. Use the `webapp-testing` skill (Playwright) to load `/`, check the card shows as online, open the detail page, and check the 1h chart has points. Take screenshots in light and dark mode.
- [ ] **Step 6: Commit** with `feat(dashboard): overview and detail pages`.

### Task 10: README, dashboard service, rollout

**Files:**
- Create: `dashboard/rpidash-dashboard.service`, `dashboard/dashboard.example.toml`
- Modify: `README.md`, covering what it is, a dev setup, running the dashboard on WSL, running it on a Pi, installing the agent, and the allowlist format.

**Interfaces:** none new.

The token is generated once with `python3 -c "import secrets;print(secrets.token_urlsafe(32))"`. It lives in `~/.config/rpidash/token` on the desktop (mode 600) and is never committed.

Steps with sudo need the owner's password, so ask the owner to run them. Suggest `! ssh -t <alias> 'cd ~/rpidash-agent && sudo -E RPIDASH_TOKEN=… RPIDASH_SERVICES=… ./install.sh'`.

- [ ] **Step 1: Write** the README, dashboard unit and example config. Commit with `docs: README and dashboard service`.
- [ ] **Step 2: Ask the owner for each Pi's service allowlist**, and suggest `user:oled-stats` and `cpu-performance` for rpi-cm4.
- [ ] **Step 3: Trial on rpi-cm4.** Run `rsync -a --delete agent/ rpi-cm4:~/rpidash-agent/` (excluding tests), then have the owner run the install. Verify:
  - `ssh rpi-cm4 systemctl is-active rpidash-agent` prints `active`.
  - `curl -s 192.168.0.18:8765/health` returns JSON.
  - Restarting `user:oled-stats` from the dashboard works.
- [ ] **Step 4: Roll out** to rpi-3, rpi-zero-2 and rpi-cm5 the same way, then add them with "Scan network" and check all four show as online.
- [ ] **Step 5: Check the Zero 2's memory.** Run `ssh rpi-zero-2 'ps -o rss= -C python3'`, or check the unit's `MemoryCurrent`. Expected: under 50 MB. The buffer fills over 7 days, so the full-buffer case is covered by `test_full_week_memory` plus the measured base RSS. Record the figure in the README.
- [ ] **Step 6: Test reboot** on rpi-3: click Reboot in the dashboard, then check it goes offline, comes back online, and shows the "since" caption.
- [ ] **Step 7: Commit** any fixes. `rpi-5` is deferred until it's powered on, which is noted in the README.
