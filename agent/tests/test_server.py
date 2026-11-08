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


class FakeJobs:
    def __init__(self):
        self.started = []

    def start(self, tests):
        from rpidash_agent.bench import BenchError
        if tests == ["busy"]:
            raise BenchError("a benchmark is already running on this Pi")
        if tests == ["gpu"]:
            raise ValueError("bad test")
        self.started.append(tests)
        return 7

    def status(self):
        return {"state": "running", "job": 7}


STRESS_STATE = {"cpu": False, "ram": False, "net": False}


class FakeStress:
    def __init__(self):
        self.calls = []

    def start(self, **kw):
        from rpidash_agent.stress import StressError
        if kw["minutes"] > 30:
            raise ValueError("minutes must be between 1 and 30")
        if kw["minutes"] == 7:
            raise StressError("cpu: Failed to find executable rpibench")
        self.calls.append(("start", kw))
        return {"cpu": kw["cpu"], "ram": kw["ram"], "net": kw["net"]}

    def stop(self):
        self.calls.append(("stop",))
        return {"cpu": False, "ram": False, "net": False}

    def status(self):
        return dict(STRESS_STATE)


@pytest.fixture
def agent():
    runner = Runner()
    history = History(100)
    cfg = AgentConfig(token=TOKEN, services_allowlist=["cpu-performance", "user:oled-stats"])
    srv = make_server(cfg, history, Services(cfg.services_allowlist, runner=runner), host="127.0.0.1", port=0,
                      spec_fn=lambda: {"model": "Test Pi"}, id_fn=lambda: "mid-1",
                      top_fn=lambda: [], reboot_delay=0.05, jobs=FakeJobs(), stress=FakeStress(),
                      now_fn=lambda: {"ts": 123.0, "cpu_pct": 42.0},
                      link_fn=lambda: {"interface": "wlan0", "kind": "Wi-Fi", "signal_dbm": -72},
                      storage_fn=lambda: {"device": "/dev/mmcblk0p2", "read_only": False, "io_errors": 3},
                      peripherals_fn=lambda: {"displays": [], "cameras": ["imx219"], "i2c": [], "usb": []})

    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    yield base, history, runner
    srv.shutdown()
    srv.server_close()


def call(url, method="GET", token=TOKEN, body=None):
    data = json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None)
    req = urllib.request.Request(url, method=method, data=data)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except ValueError:
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


def test_idle_connection_is_closed():
    import socket
    cfg = AgentConfig(token=TOKEN)
    srv = make_server(cfg, History(10), Services([]), host="127.0.0.1", port=0, request_timeout=0.3,
                      spec_fn=dict, id_fn=lambda: "x", top_fn=list)
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    try:
        c = socket.create_connection(srv.server_address, timeout=3)
        c.sendall(b"GET /health HTTP/1.1\r\n")  # never finishes the request
        t0 = time.time()
        assert c.recv(1024) == b""  # server hung up
        assert time.time() - t0 < 2
    finally:
        srv.shutdown()
        srv.server_close()


def test_shutdown_delayed(agent):
    base, _, runner = agent
    assert call(base + "/shutdown", "POST", token="wrong")[0] == 401
    code, body = call(base + "/shutdown", "POST")
    assert code == 202 and body == {"ok": True}
    deadline = time.time() + 1
    while time.time() < deadline and ["systemctl", "poweroff"] not in runner.calls:
        time.sleep(0.01)
    assert ["systemctl", "poweroff"] in runner.calls





def test_bench_start_and_status(agent):
    base, _, _ = agent
    assert call(base + "/bench", "POST", token=None, body={"tests": ["cpu"]})[0] == 401
    code, body = call(base + "/bench", "POST", body={"tests": ["cpu", "disk"]})
    assert code == 202 and body == {"job": 7}
    assert call(base + "/bench")[1] == {"state": "running", "job": 7, "id": "mid-1"}
    assert call(base + "/bench", "POST", body={"tests": ["busy"]})[0] == 409
    assert call(base + "/bench", "POST", body={"tests": ["gpu"]})[0] == 400
    assert call(base + "/bench", "POST", body={"tests": "cpu"})[0] == 400


def test_bench_rejects_bad_json(agent):
    base, _, _ = agent
    req = urllib.request.Request(base + "/bench", method="POST", data=b"{not json")
    req.add_header("Authorization", f"Bearer {TOKEN}")
    try:
        urllib.request.urlopen(req, timeout=5)
        code = 200
    except urllib.error.HTTPError as e:
        code = e.code
    assert code == 400


def test_stress_start_stop_and_live(agent):
    base, _, _ = agent
    assert call(base + "/stress", "POST", token="wrong", body={"cpu": True})[0] == 401
    code, body = call(base + "/stress", "POST", body={"cpu": True, "ram": False, "net": True, "minutes": 5})
    assert code == 200 and body == {"cpu": True, "ram": False, "net": True}
    assert call(base + "/stress", "POST", body={"cpu": True, "minutes": 99})[0] == 400
    assert call(base + "/stress/stop", "POST")[1] == {"cpu": False, "ram": False, "net": False}
    STRESS_STATE["ram"] = True
    try:
        assert call(base + "/live")[1]["stress"] == {"cpu": False, "ram": True, "net": False}
    finally:
        STRESS_STATE["ram"] = False



def test_now_returns_a_fresh_sample(agent):
    base, _, _ = agent
    assert call(base + "/now", token=None)[0] == 401
    code, body = call(base + "/now")
    assert code == 200 and body == {"id": "mid-1", "sample": {"ts": 123.0, "cpu_pct": 42.0}}


def test_live_reports_agent_version(agent):
    from rpidash_agent import __version__
    base, _, _ = agent
    assert call(base + "/live")[1]["version"] == __version__


def test_bench_refused_while_stress_runs(agent):
    base, _, _ = agent
    STRESS_STATE["ram"] = True
    try:
        code, body = call(base + "/bench", "POST", body={"tests": ["cpu"]})
    finally:
        STRESS_STATE["ram"] = False
    assert code == 409 and "stress" in body["error"]


def test_bench_status_includes_agent_id(agent):
    base, _, _ = agent
    assert call(base + "/bench")[1]["id"] == "mid-1"


def test_stress_start_failure_is_409(agent):
    base, _, _ = agent
    code, body = call(base + "/stress", "POST", body={"cpu": True, "minutes": 7})
    assert code == 409 and "rpibench" in body["error"]


def test_agent_restart_replies_then_restarts(agent):
    base, _, runner = agent
    assert call(base + "/agent/restart", "POST", token="wrong")[0] == 401
    assert runner.calls == []
    code, body = call(base + "/agent/restart", "POST")
    assert code == 202 and body == {"ok": True}
    want = ["systemctl", "--no-block", "restart", "rpidash-agent.service"]
    deadline = time.time() + 1
    while time.time() < deadline and want not in runner.calls:
        time.sleep(0.01)
    assert want in runner.calls


def test_live_reports_agent_process_uptime(agent):
    base, _, _ = agent
    up = call(base + "/live")[1]["agent_uptime_s"]
    assert isinstance(up, (int, float)) and 0 <= up < 3600



def test_storage_and_peripherals_endpoints(agent):
    base, _, _ = agent
    for path in ("/storage", "/peripherals"):
        assert call(base + path, token=None)[0] == 401
    assert call(base + "/storage")[1]["io_errors"] == 3
    assert call(base + "/peripherals")[1]["cameras"] == ["imx219"]


def test_live_has_link_and_storage_warnings(agent):
    base, _, _ = agent
    live = call(base + "/live")[1]
    assert live["link"]["signal_dbm"] == -72
    assert live["storage_warnings"] == ["storage_errors"]
