import json

import httpx

from rpidash_dashboard.client import AgentClient
from rpidash_dashboard.discovery import Rediscovery, scan
from rpidash_dashboard.registry import Pi, Registry


def agent_at(mapping):
    """mapping: ip -> agent id; other hosts refuse."""
    def h(req):
        ip = req.url.host
        if ip in mapping and req.url.path == "/health":
            return httpx.Response(200, json={"app": "rpidash-agent", "id": mapping[ip], "hostname": f"h-{mapping[ip]}",
                                             "version": "0.1.0"})
        raise httpx.ConnectError("refused")
    return h


async def test_scan_finds_agents():
    c = AgentClient("tok", transport=httpx.MockTransport(agent_at({"192.168.9.5": "A"})))
    found = await scan("192.168.9.4/30", c)
    assert [(h, d["id"]) for h, d in found] == [("192.168.9.5", "A")]


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


async def test_rediscovery_moves_host_and_rate_limits(tmp_path):
    reg = Registry(tmp_path / "pis.json")
    reg.upsert(Pi(id="A", hostname="h-A", host="192.168.9.5", port=8765, spec=None, added="x", last_seen=None))
    calls = []
    inner = agent_at({"192.168.9.6": "A"})

    def h(req):
        calls.append(req.url.host)
        return inner(req)

    clock = Clock()
    rd = Rediscovery(reg, AgentClient("tok", transport=httpx.MockTransport(h)), "192.168.9.4/30", clock=clock)
    assert await rd.maybe_run(set()) == {}
    assert calls == []
    assert await rd.maybe_run({"A"}) == {"A": "192.168.9.6"}
    assert reg.get("A").host == "192.168.9.6"
    assert json.loads((tmp_path / "pis.json").read_text())["pis"][0]["host"] == "192.168.9.6"
    calls.clear()
    clock.t += 299
    assert await rd.maybe_run({"A"}) == {}
    assert calls == []
    clock.t += 2
    await rd.maybe_run({"A"})
    assert calls  # scanned again after 300 s
