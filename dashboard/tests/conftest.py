import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

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

    def __init__(self, behaviours):
        self.behaviours = behaviours
        self.requests = []

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
        if p == "/health":
            return httpx.Response(200, json={"app": "rpidash-agent", "id": "ID-" + req.url.host,
                                             "hostname": "new-pi", "version": "0.1.0"})
        if p == "/spec":
            return httpx.Response(200, json={"model": "Raspberry Pi 3"})
        if p == "/live":
            return httpx.Response(200, json={"sample": SAMPLE, "top": [], "services": [], "uptime_s": 5})
        if p == "/history":
            return httpx.Response(200, json={"since": 0, "ts": [1.0], "cpu_pct": [5.0]})
        return httpx.Response(200, json={"ok": True, "output": ""})


@pytest.fixture
def make_client(tmp_path):
    def _make(behaviours, pis=()):
        cfg = DashboardConfig(token=TOKEN, registry_path=tmp_path / "pis.json", subnet="192.168.9.4/30")
        reg = Registry(cfg.registry_path)
        for p in pis:
            reg.upsert(p)
        fake = FakeAgents(behaviours)
        agents = AgentClient(TOKEN, timeout=0.5, transport=httpx.MockTransport(fake))
        app = create_app(cfg, client=agents, registry=reg)
        return TestClient(app), reg, fake
    return _make
