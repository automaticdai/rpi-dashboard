"""FastAPI app: JSON API over the agents, plus the HTML pages."""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from .bench_store import BenchStore
from .client import AgentAuthError, AgentClient, AgentError, AgentRejected
from .config import DashboardConfig
from .discovery import Rediscovery, scan
from .registry import Pi, Registry
from .status import health_warnings

# last_seen alone only forces a write this often; status changes are written at once.
SAVE_EVERY_S = 3600.0
# Hardware details are re-read when a Pi comes back online (reboot, new SD card...) and hourly.
SPEC_REFRESH_S = 3600.0
HERE = Path(__file__).parent


class AddPi(BaseModel):
    host: str


class Order(BaseModel):
    ids: list[str]


class BenchRound(BaseModel):
    ids: list[str]
    tests: list[str]


class StressRequest(BaseModel):
    cpu: bool = False
    ram: bool = False
    net: bool = False
    minutes: int = 5


BENCH_TESTS = ("cpu", "disk")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def create_app(cfg: DashboardConfig, client: AgentClient | None = None,
               registry: Registry | None = None) -> FastAPI:
    if registry is None:
        registry = Registry(cfg.registry_path)
        registry.load()
    agents = client or AgentClient(cfg.token, port=cfg.agent_port)
    rediscovery = Rediscovery(registry, agents, cfg.subnet)
    state = {"last_save": 0.0, "status": {}, "spec_at": {}, "spec_changed": False}
    bench = BenchStore(cfg.registry_path.parent / "bench.json")
    bench.load()
    log = logging.getLogger("rpidash_dashboard")

    def safe_save() -> None:
        try:
            registry.save()
        except OSError as e:  # e.g. read-only or full disk: keep serving, just don't persist
            log.warning("could not save %s: %s", registry.path, e)
    background: set[asyncio.Task] = set()

    @asynccontextmanager
    async def lifespan(_app):
        yield
        for t in list(background):
            t.cancel()
        await agents.aclose()

    app = FastAPI(title="rpi-dashboard", lifespan=lifespan)
    app.state.cfg, app.state.registry = cfg, registry
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=cfg.allowed_hosts)

    @app.middleware("http")
    async def same_origin_writes(request: Request, call_next):
        # No login (owner's choice), so refuse state-changing requests made by other websites.
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            cross = request.headers.get("sec-fetch-site") == "cross-site"
            if cross or (origin and urlsplit(origin).netloc != request.headers.get("host")):
                return JSONResponse({"detail": "cross-site request refused"}, status_code=403)
        return await call_next(request)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")

    @app.get("/", response_class=HTMLResponse)
    async def overview_page(request: Request):
        return templates.TemplateResponse(request, "overview.html")

    @app.get("/bench", response_class=HTMLResponse)
    async def bench_page(request: Request):
        return templates.TemplateResponse(request, "bench.html")

    @app.get("/stress", response_class=HTMLResponse)
    async def stress_page(request: Request):
        return templates.TemplateResponse(request, "stress.html")

    @app.get("/pi/{pi_id}", response_class=HTMLResponse)
    async def detail_page(request: Request, pi_id: str):
        return templates.TemplateResponse(request, "detail.html", {"pi": get_pi(pi_id)})

    def call_timeout() -> float:
        return agents.timeout + 0.5  # hard cap even if the transport ignores its timeout

    async def agent_call(coro):
        try:
            return await asyncio.wait_for(coro, call_timeout())
        except AgentAuthError:
            raise HTTPException(502, "agent rejected the dashboard token")
        except AgentRejected as e:
            raise HTTPException(e.status, str(e))
        except PermissionError as e:
            raise HTTPException(403, str(e))
        except (AgentError, asyncio.TimeoutError) as e:
            raise HTTPException(502, f"agent unreachable: {e}")

    def get_pi(pi_id: str) -> Pi:
        pi = registry.get(pi_id)
        if pi is None:
            raise HTTPException(404, "unknown Pi")
        return pi

    async def refresh_spec(pi: Pi, agent_version) -> None:
        last = state["spec_at"].get(pi.id)
        came_back = state["status"].get(pi.id) != "online"
        stored = (pi.spec or {}).get("agent_version")
        upgraded = agent_version is not None and stored is not None and agent_version != stored
        if not (came_back or upgraded) and last is not None and time.monotonic() - last < SPEC_REFRESH_S:
            return
        try:
            spec = await asyncio.wait_for(agents.spec(pi.host, pi.port), call_timeout())
        except (AgentError, PermissionError, asyncio.TimeoutError):
            return
        state["spec_at"][pi.id] = time.monotonic()
        if isinstance(spec, dict) and spec != pi.spec:
            pi.spec = spec
            state["spec_changed"] = True

    async def poll(pi: Pi) -> dict:
        status, live = "offline", None
        try:
            live = await asyncio.wait_for(agents.live(pi.host, pi.port), call_timeout())
            if live.get("id", pi.id) != pi.id:
                live = None  # its address now belongs to another Pi: treat as offline so rediscovery runs
            else:
                status = "online"
                pi.last_seen = _now_iso()
                await refresh_spec(pi, live.get("version"))
        except AgentAuthError:
            status = "auth_failed"
        except (AgentError, PermissionError, asyncio.TimeoutError):
            pass
        sample = (live or {}).get("sample")
        return {"id": pi.id, "hostname": pi.hostname, "host": pi.host, "status": status, "live": live,
                "spec": pi.spec, "last_seen": pi.last_seen,
                "warnings": health_warnings(sample, cfg, agent_version=(live or {}).get("version"))}

    @app.get("/api/pis")
    async def list_pis():
        rows = await asyncio.gather(*(poll(p) for p in registry.all()))
        statuses = {r["id"]: r["status"] for r in rows}
        changed = statuses != state["status"]
        state["status"] = statuses
        if state["spec_changed"]:
            changed, state["spec_changed"] = True, False
        if changed or time.monotonic() - state["last_save"] >= SAVE_EVERY_S:
            safe_save()
            state["last_save"] = time.monotonic()
        offline = {r["id"] for r in rows if r["status"] == "offline"}
        if offline:
            t = asyncio.create_task(rediscovery.maybe_run(offline))
            background.add(t)
            t.add_done_callback(background.discard)
        return rows

    @app.get("/api/pis/{pi_id}/history")
    async def pi_history(pi_id: str, range: str = "1h"):
        pi = get_pi(pi_id)
        return await agent_call(agents.history(pi.host, range, pi.port))

    @app.post("/api/pis")
    async def add_pi(body: AddPi):
        host = body.host.strip()
        health = await agent_call(agents.health(host))
        spec = await agent_call(agents.spec(host))
        old = registry.get(health["id"])
        pi = Pi(id=health["id"], hostname=health.get("hostname") or host, host=host, port=cfg.agent_port,
                spec=spec, added=old.added if old else _now_iso(), last_seen=_now_iso())
        registry.upsert(pi)
        safe_save()
        return asdict(pi)

    @app.delete("/api/pis/{pi_id}", status_code=204)
    async def delete_pi(pi_id: str):
        if not registry.remove(pi_id):
            raise HTTPException(404, "unknown Pi")
        safe_save()
        bench.forget(pi_id)
        bench.round.pop(pi_id, None)
        return Response(status_code=204)

    @app.put("/api/order", status_code=204)
    async def set_order(body: Order):
        registry.reorder(body.ids)
        safe_save()
        return Response(status_code=204)

    def save_bench() -> None:
        try:
            bench.save()
        except OSError as e:
            log.warning("could not save %s: %s", bench.path, e)

    @app.post("/api/bench")
    async def start_bench(body: BenchRound):
        tests = [t for t in BENCH_TESTS if t in body.tests]
        if not body.ids or not tests or len(tests) != len(set(body.tests)):
            raise HTTPException(400, f"choose at least one Pi and tests from {list(BENCH_TESTS)}")

        async def start_one(pi_id: str) -> dict:
            if registry.get(pi_id) is None:
                return {"error": "unknown Pi"}
            try:
                pi = await verified_pi(pi_id)
                started = await agent_call(agents.bench_start(pi.host, tests, pi.port))
            except HTTPException as e:
                return {"error": str(e.detail)}
            bench.round[pi_id] = {"job": started.get("job"), "tests": tests}
            save_bench()
            return {"started": True}

        ids = list(dict.fromkeys(body.ids))
        return dict(zip(ids, await asyncio.gather(*(start_one(i) for i in ids))))

    @app.get("/api/bench")
    async def bench_table():
        running = {}

        async def check(pi_id: str, entry: dict) -> None:
            tests = entry["tests"]
            pi = registry.get(pi_id)
            if pi is None:
                bench.round.pop(pi_id, None)
                return
            try:
                st = await asyncio.wait_for(agents.bench_status(pi.host, pi.port), call_timeout())
            except (AgentError, PermissionError, asyncio.TimeoutError):
                running[pi_id] = {"current": None, "tests": tests, "unreachable": True}
                return
            if st.get("id", pi.id) != pi.id:  # another Pi now answers at this address
                running[pi_id] = {"current": None, "tests": tests, "unreachable": True}
                return
            ours = st.get("job") == entry.get("job")
            if st.get("state") == "running" or (st.get("state") == "done" and not ours and st.get("job", 0) < (entry.get("job") or 0)):
                # still running, or this answer is about an older job (a poll that raced a new start)
                running[pi_id] = ({"current": st.get("current"), "tests": st.get("tests")} if ours
                                  else {"current": None, "tests": tests, "waiting": True})
                return
            if bench.round.get(pi_id) != entry:
                return  # a newer round started while we were asking; leave it alone
            finished = st.get("finished")
            at = (datetime.fromtimestamp(finished, timezone.utc).isoformat(timespec="seconds")
                  if isinstance(finished, (int, float)) else _now_iso())
            if st.get("state") == "done" and ours:
                for t, res in (st.get("results") or {}).items():
                    if t in tests:
                        if t == "disk" and isinstance(res, dict) and pi.spec:
                            # remember which disk this was, so a later card swap marks it stale
                            res = {**res, "root_size": pi.spec.get("root_size"),
                                   "root_device": pi.spec.get("root_device")}
                        bench.record(pi_id, t, res, at)
                for t, err in (st.get("errors") or {}).items():
                    if t in tests:
                        bench.record_error(pi_id, t, str(err)[:300], at)
            else:  # idle or a different job: the agent restarted and lost ours
                for t in tests:
                    bench.record_error(pi_id, t, "the agent restarted before the test finished", _now_iso())
            bench.round.pop(pi_id, None)
            save_bench()

        await asyncio.gather(*(check(i, dict(e)) for i, e in list(bench.round.items())))
        def known(results: dict) -> dict:  # hide results of tests that no longer exist
            return {t: r for t, r in results.items() if t in BENCH_TESTS}

        rows = [{"id": p.id, "hostname": p.hostname, "model": (p.spec or {}).get("model"),
                 "root_size": (p.spec or {}).get("root_size"),
                 "results": known(bench.results(p.id))} for p in registry.all()]
        return {"rows": rows, "running": running}

    @app.get("/api/now")
    async def live_now(ids: str):
        """Fresh readings for the Stress tab's live charts; None for Pis that don't answer."""
        async def read(pi: Pi):
            try:
                d = await asyncio.wait_for(agents.now(pi.host, pi.port), call_timeout())
            except (AgentError, PermissionError, asyncio.TimeoutError):
                return None
            return d.get("sample") if d.get("id") == pi.id else None

        pis = [p for p in (registry.get(i) for i in dict.fromkeys(ids.split(","))) if p]
        return dict(zip([p.id for p in pis], await asyncio.gather(*(read(p) for p in pis))))

    @app.post("/api/pis/{pi_id}/stress")
    async def stress_start(pi_id: str, body: StressRequest):
        pi = await verified_pi(pi_id)
        return await agent_call(agents.stress_start(pi.host, body.model_dump(), pi.port))

    @app.post("/api/pis/{pi_id}/stress/stop")
    async def stress_stop(pi_id: str):
        pi = await verified_pi(pi_id)
        return await agent_call(agents.stress_stop(pi.host, pi.port))

    @app.post("/api/scan")
    async def scan_network():
        found = await scan(cfg.subnet, agents)
        return [{"host": h, "id": d["id"], "hostname": d.get("hostname"), "known": registry.get(d["id"]) is not None}
                for h, d in found]

    async def verified_pi(pi_id: str) -> Pi:
        """Confirm the Pi at the stored address is still this Pi before acting on it."""
        pi = get_pi(pi_id)
        health = await agent_call(agents.health(pi.host, port=pi.port))
        if health["id"] != pi.id:
            raise HTTPException(409, f"{pi.host} now belongs to {health.get('hostname', 'another Pi')}; "
                                     f"wait for {pi.hostname} to be found again")
        return pi

    @app.post("/api/pis/{pi_id}/reboot")
    async def reboot(pi_id: str):
        pi = await verified_pi(pi_id)
        return await agent_call(agents.reboot(pi.host, pi.port))

    @app.post("/api/pis/{pi_id}/shutdown")
    async def shutdown(pi_id: str):
        pi = await verified_pi(pi_id)
        return await agent_call(agents.shutdown(pi.host, pi.port))

    @app.post("/api/pis/{pi_id}/services/{entry}/{action}")
    async def service_action(pi_id: str, entry: str, action: str):
        pi = await verified_pi(pi_id)
        return await agent_call(agents.service_action(pi.host, entry, action, pi.port))

    return app
