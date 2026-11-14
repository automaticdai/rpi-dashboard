import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from rpidash_dashboard import EXPECTED_AGENT_VERSION
from rpidash_dashboard.app import create_app
from rpidash_dashboard.client import AgentClient
from rpidash_dashboard.config import DashboardConfig
from rpidash_dashboard.registry import Pi, Registry

TOKEN = "super-secret-token-xyz"
SAMPLE = {"ts": 1.0, "cpu_pct": 5.0, "load1": 0.1, "mem_used": 100, "mem_total": 1000, "swap_used": 0,
          "disk_pct": 50.0, "net_rx_bps": 0.0, "net_tx_bps": 0.0, "temp_c": 45.0, "throttled": 0}


def make_pi(id_, host):
    return Pi(id=id_, hostname=f"h-{id_}", host=host, port=8765, spec={"model": f"Model {id_}"},
              added="2026-11-06T00:00:00+00:00", last_seen=None)


class FakeAgents:
    """Mock transport: host -> behaviour ('ok', 'hang', 'auth', 'forbid', 'down')."""

    def __init__(self, behaviours, ids=None):
        self.behaviours = behaviours
        self.ids = ids or {}  # host -> agent id it reports
        self.requests = []
        self.posted = []  # (path, json body) of POSTs
        self.bench_status = {}  # host -> /bench status
        self.bench_busy = False
        self.next_job = 1
        self.spec = {"model": "Raspberry Pi 3"}
        self.uptime = 5.0
        self.link = {"interface": "wlan0", "kind": "Wi-Fi", "signal_dbm": -50}
        self.storage_warnings = []
        self.app_status = None  # a Pico's /live "app"

    def agent_id(self, host):
        return self.ids.get(host, "ID-" + host)

    async def __call__(self, req):
        self.requests.append(req)
        b = self.behaviours.get(req.url.host, "down")
        if b == "hang":
            await asyncio.sleep(10)
        if b == "auth":
            return httpx.Response(401, json={"error": "unauthorized"})
        if b == "down":
            raise httpx.ConnectError("refused")
        if b == "forbid" and req.method == "POST":
            return httpx.Response(403, json={"error": "service not allowlisted"})
        p = req.url.path
        if req.method == "POST":
            body = json.loads(req.content) if req.content else None
            self.posted.append((p, body))
            if p == "/bench":
                if self.bench_busy:
                    return httpx.Response(409, json={"error": "a benchmark is already running on this Pi"})
                job, self.next_job = self.next_job, self.next_job + 1
                return httpx.Response(202, json={"job": job})
            if p.startswith("/stress"):
                return httpx.Response(200, json={"cpu": True, "ram": False, "net": False})
        if p == "/bench":
            st = self.bench_status.get(req.url.host, {"state": "idle"})
            return httpx.Response(200, json={"id": self.agent_id(req.url.host), **st})
        if p == "/storage":
            return httpx.Response(200, json={"device": "/dev/mmcblk0p2", "io_errors": 0, "read_only": False})
        if p == "/peripherals":
            return httpx.Response(200, json={"displays": [{"name": "DSI-1", "connected": True}], "cameras": [], "i2c": [], "usb": []})
        if p == "/now":
            return httpx.Response(200, json={"id": self.agent_id(req.url.host), "sample": SAMPLE})
        if p == "/health":
            return httpx.Response(200, json={"app": "rpidash-agent", "id": self.agent_id(req.url.host),
                                             "hostname": "new-pi", "version": "0.1.0"})
        if p == "/spec":
            return httpx.Response(200, json=self.spec)
        if p == "/live":
            return httpx.Response(200, json={"id": self.agent_id(req.url.host), "version": EXPECTED_AGENT_VERSION, "sample": SAMPLE, "top": [], "services": [],
                                             "uptime_s": self.uptime, "link": self.link,
                                             "storage_warnings": self.storage_warnings, "app": self.app_status})
        if p == "/history":
            return httpx.Response(200, json={"since": 0, "ts": [1.0], "cpu_pct": [5.0]})
        return httpx.Response(200, json={"ok": True, "output": ""})


@pytest.fixture
def make_client(tmp_path):
    def _make(behaviours, pis=(), ids=None):
        cfg = DashboardConfig(token=TOKEN, registry_path=tmp_path / "pis.json", subnet="192.168.9.4/30",
                              allowed_hosts=["testserver", "localhost", "127.0.0.1"])
        reg = Registry(cfg.registry_path)
        for p in pis:
            reg.upsert(p)
        fake = FakeAgents(behaviours, ids={p.host: p.id for p in pis} | (ids or {}))
        agents = AgentClient(TOKEN, timeout=0.5, transport=httpx.MockTransport(fake))
        app = create_app(cfg, client=agents, registry=reg)
        return TestClient(app), reg, fake
    return _make
