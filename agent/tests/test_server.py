import json
import subprocess
import threading
import time
import urllib.error
import urllib.request

import pytest

from rpidash_agent.collect import Sample
from rpidash_agent.config import AgentConfig
from rpidash_agent.history import History
from rpidash_agent.server import make_server
from rpidash_agent.services import Services

TOKEN = "secret-token"


class Runner:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "active\n", "")


def sample(ts):
    return Sample(ts=ts, cpu_pct=5.0, load1=0.1, mem_used=1, mem_total=2, swap_used=0, disk_pct=10.0,
                  net_rx_bps=0.0, net_tx_bps=0.0, temp_c=45.0, throttled=0, fan_rpm=None)


@pytest.fixture
def agent():
    runner = Runner()
    history = History(100)
    cfg = AgentConfig(token=TOKEN, services_allowlist=["cpu-performance", "user:oled-stats"])
    srv = make_server(cfg, history, Services(cfg.services_allowlist, runner=runner), host="127.0.0.1", port=0,
                      spec_fn=lambda: {"model": "Test Pi"}, id_fn=lambda: "mid-1",
                      top_fn=lambda: [], reboot_delay=0.05)
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    yield base, history, runner
    srv.shutdown()
    srv.server_close()


def call(url, method="GET", token=TOKEN):
    req = urllib.request.Request(url, method=method, data=b"" if method == "POST" else None)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, None


def test_health_no_auth(agent):
    base, _, _ = agent
    code, body = call(base + "/health", token=None)
    assert code == 200
    assert set(body) == {"app", "id", "hostname", "version"}
    assert body["app"] == "rpidash-agent" and body["id"] == "mid-1"


@pytest.mark.parametrize("path,method", [("/spec", "GET"), ("/live", "GET"), ("/history?range=1h", "GET"),
                                         ("/reboot", "POST")])
@pytest.mark.parametrize("token", [None, "wrong"])
def test_auth_required(agent, path, method, token):
    base, _, runner = agent
    assert call(base + path, method, token=token)[0] == 401
    assert runner.calls == []


def test_live_and_history(agent):
    base, history, _ = agent
    now = time.time()
    for i in range(3):
        history.append(sample(now - 30 + i * 10))
    code, live = call(base + "/live")
    assert code == 200 and live["sample"]["ts"] == now - 10
    assert live["services"][0] == {"name": "cpu-performance", "state": "active"}
    assert "uptime_s" in live and live["top"] == []
    code, hist = call(base + "/history?range=1h")
    assert code == 200 and len(hist["ts"]) == 3
    assert call(base + "/history?range=2h")[0] == 400
    assert call(base + "/spec")[1] == {"model": "Test Pi"}


def test_service_action_ok(agent):
    base, _, runner = agent
    code, body = call(base + "/services/cpu-performance/restart", "POST")
    assert code == 200 and body["ok"] is True
    assert runner.calls == [["systemctl", "restart", "cpu-performance"]]
    code, _ = call(base + "/services/user%3Aoled-stats/stop", "POST")
    assert code == 200
    assert runner.calls[-1] == ["systemctl", "--user", "-M", "yfrl@", "stop", "oled-stats"]


@pytest.mark.parametrize("path,expected", [
    ("/services/..%2Freboot/restart", 404),
    ("/services/ssh%3Brm%20-rf%20%2F/restart", 404),
    ("/services/sshd/restart", 403),
    ("/services/cpu-performance/enable", 403),
    ("/services/cpu-performance", 404),
])
def test_service_hostile(agent, path, expected):
    base, _, runner = agent
    assert call(base + path, "POST")[0] == expected
    assert runner.calls == []


def test_unknown_path(agent):
    base, _, _ = agent
    assert call(base + "/nope")[0] == 404


def test_reboot_delayed(agent):
    base, _, runner = agent
    code, body = call(base + "/reboot", "POST")
    assert code == 202 and body == {"ok": True}
    deadline = time.time() + 1
    while time.time() < deadline and ["systemctl", "reboot"] not in runner.calls:
        time.sleep(0.01)
    assert ["systemctl", "reboot"] in runner.calls


def test_live_includes_agent_id(agent):
    base, _, _ = agent
    assert call(base + "/live")[1]["id"] == "mid-1"


def test_successful_requests_not_logged(agent, capfd):
    base, _, _ = agent
    call(base + "/health", token=None)
    call(base + "/live")
    assert "GET" not in capfd.readouterr().err
    call(base + "/live", token="wrong")
    assert "401" in capfd.readouterr().err


def test_live_lists_top_ten_processes():
    import inspect
    from rpidash_agent.collect import top_processes
    assert inspect.signature(top_processes).parameters["n"].default == 10
