import json
import time

from conftest import TOKEN, make_pi


def test_list_mixed_status(make_client):
    c, _, _ = make_client({"10.0.0.1": "ok", "10.0.0.2": "hang", "10.0.0.3": "auth"},
                          pis=[make_pi("A", "10.0.0.1"), make_pi("B", "10.0.0.2"), make_pi("C", "10.0.0.3")])
    with c:
        t0 = time.monotonic()
        r = c.get("/api/pis")
        elapsed = time.monotonic() - t0
    assert r.status_code == 200
    status = {p["id"]: p["status"] for p in r.json()}
    assert status == {"A": "online", "B": "offline", "C": "auth_failed"}
    assert elapsed < 2.0
    a = next(p for p in r.json() if p["id"] == "A")
    assert a["live"]["sample"]["cpu_pct"] == 5.0 and a["warnings"] == [] and a["last_seen"]
    b = next(p for p in r.json() if p["id"] == "B")
    assert b["live"] is None and b["spec"] == {"model": "Model B"}


def test_add_pi(make_client):
    c, reg, _ = make_client({"192.168.9.5": "ok"})
    with c:
        r = c.post("/api/pis", json={"host": "192.168.9.5"})
    assert r.status_code == 200
    assert r.json()["id"] == "ID-192.168.9.5"
    reg2 = type(reg)(reg.path)
    reg2.load()
    assert reg2.get("ID-192.168.9.5").spec == {"model": "Raspberry Pi 3"}


def test_add_pi_not_agent(make_client):
    c, reg, _ = make_client({})
    with c:
        assert c.post("/api/pis", json={"host": "192.168.9.5"}).status_code == 502
    assert reg.all() == []


def test_delete_pi(make_client):
    c, reg, _ = make_client({}, pis=[make_pi("A", "10.0.0.1")])
    with c:
        assert c.delete("/api/pis/A").status_code == 204
        assert c.delete("/api/pis/A").status_code == 404
    assert reg.all() == []


def test_scan(make_client):
    c, _, _ = make_client({"192.168.9.5": "ok"}, pis=[make_pi("ID-192.168.9.5", "192.168.9.5")])
    with c:
        r = c.post("/api/scan")
    assert r.json() == [{"host": "192.168.9.5", "id": "ID-192.168.9.5", "hostname": "new-pi", "known": True}]


def test_history_proxy(make_client):
    c, _, fake = make_client({"10.0.0.1": "ok"}, pis=[make_pi("A", "10.0.0.1")])
    with c:
        assert c.get("/api/pis/A/history?range=6h").json()["ts"] == [1.0]
        assert c.get("/api/pis/Z/history?range=1h").status_code == 404
    assert fake.requests[-1].url.params["range"] == "6h"


def test_history_agent_down(make_client):
    c, _, _ = make_client({}, pis=[make_pi("A", "10.0.0.1")])
    with c:
        assert c.get("/api/pis/A/history?range=1h").status_code == 502


def test_actions(make_client):
    c, _, fake = make_client({"10.0.0.1": "ok"}, pis=[make_pi("A", "10.0.0.1")])
    with c:
        assert c.post("/api/pis/A/reboot").status_code == 200
        assert c.post("/api/pis/A/services/user:oled-stats/restart").status_code == 200
    assert fake.requests[-1].url.raw_path == b"/services/user%3Aoled-stats/restart"


def test_service_proxy_403(make_client):
    c, _, _ = make_client({"10.0.0.1": "forbid"}, pis=[make_pi("A", "10.0.0.1")])
    with c:
        assert c.post("/api/pis/A/services/sshd/restart").status_code == 403


def test_no_token_in_responses(make_client):
    c, _, _ = make_client({"10.0.0.1": "ok", "192.168.9.5": "ok"}, pis=[make_pi("A", "10.0.0.1")])
    with c:
        bodies = [c.get("/api/pis").text, c.post("/api/scan").text, c.get("/api/pis/A/history").text,
                  c.post("/api/pis", json={"host": "192.168.9.5"}).text, c.post("/api/pis/A/reboot").text]
    assert all(TOKEN not in b for b in bodies)


def test_other_pi_at_registered_address_is_not_shown_as_this_pi(make_client):
    # rpi "A" was at 10.0.0.1; DHCP has since given that address to another Pi ("B").
    c, _, _ = make_client({"10.0.0.1": "ok"}, pis=[make_pi("A", "10.0.0.1")], ids={"10.0.0.1": "B"})
    with c:
        row = c.get("/api/pis").json()[0]
    assert row["status"] == "offline" and row["live"] is None


def test_action_refused_when_address_now_belongs_to_another_pi(make_client):
    c, _, fake = make_client({"10.0.0.1": "ok"}, pis=[make_pi("A", "10.0.0.1")], ids={"10.0.0.1": "B"})
    with c:
        assert c.post("/api/pis/A/reboot").status_code == 409
        assert c.post("/api/pis/A/services/x/restart").status_code == 409
    assert not [r for r in fake.requests if r.method == "POST"]


def test_rejects_unknown_host_header(make_client):
    c, _, _ = make_client({})
    with c:
        assert c.get("/api/pis", headers={"Host": "evil.example"}).status_code == 400


def test_rejects_cross_origin_post(make_client):
    c, _, fake = make_client({"10.0.0.1": "ok"}, pis=[make_pi("A", "10.0.0.1")])
    with c:
        assert c.post("/api/pis/A/reboot", headers={"Origin": "http://evil.example"}).status_code == 403
        assert c.post("/api/pis/A/reboot", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
        assert c.delete("/api/pis/A", headers={"Origin": "http://evil.example"}).status_code == 403
        assert c.post("/api/pis/A/reboot", headers={"Origin": "http://testserver"}).status_code == 200
    assert len([r for r in fake.requests if r.method == "POST"]) == 1


def test_put_order(make_client):
    c, reg, _ = make_client({}, pis=[make_pi("A", "10.0.0.1"), make_pi("B", "10.0.0.2")])
    with c:
        assert c.put("/api/order", json={"ids": ["B", "A"]}).status_code == 204
        assert [p["id"] for p in c.get("/api/pis").json()] == ["B", "A"]
        assert c.put("/api/order", json={"ids": ["A", "B"]}, headers={"Origin": "http://evil.example"}).status_code == 403
    reg2 = type(reg)(reg.path); reg2.load()
    assert [p.id for p in reg2.all()] == ["B", "A"]


def test_overview_survives_registry_save_failure(make_client, monkeypatch):
    c, reg, _ = make_client({"10.0.0.1": "ok"}, pis=[make_pi("A", "10.0.0.1")])
    monkeypatch.setattr(reg, "save", lambda: (_ for _ in ()).throw(OSError("read-only file system")))
    with c:
        assert c.get("/api/pis").status_code == 200
        assert c.get("/api/pis").status_code == 200


def test_last_seen_saved_only_when_status_changes(make_client, monkeypatch):
    c, reg, fake = make_client({"10.0.0.1": "ok"}, pis=[make_pi("A", "10.0.0.1")])
    saves = []
    real = reg.save
    monkeypatch.setattr(reg, "save", lambda: (saves.append(1), real()))
    with c:
        c.get("/api/pis")          # first poll: status becomes known -> save
        n = len(saves)
        for _ in range(3):
            c.get("/api/pis")      # nothing changed -> no saves
        assert len(saves) == n and n >= 1
        fake.behaviours["10.0.0.1"] = "down"
        c.get("/api/pis")          # online -> offline -> save
        assert len(saves) == n + 1


def test_agent_called_on_registered_port(make_client):
    pi = make_pi("A", "10.0.0.1")
    pi.port = 9999
    c, _, fake = make_client({"10.0.0.1": "ok"}, pis=[pi])
    with c:
        assert c.get("/api/pis").json()[0]["status"] == "online"
    assert {r.url.port for r in fake.requests} == {9999}


def test_shutdown_proxy(make_client):
    c, _, fake = make_client({"10.0.0.1": "ok"}, pis=[make_pi("A", "10.0.0.1")])
    with c:
        assert c.post("/api/pis/A/shutdown").status_code == 200
        assert c.post("/api/pis/A/shutdown", headers={"Origin": "http://evil.example"}).status_code == 403
    assert [r.url.path for r in fake.requests if r.method == "POST"] == ["/shutdown"]


def test_shutdown_refused_when_address_now_belongs_to_another_pi(make_client):
    c, _, fake = make_client({"10.0.0.1": "ok"}, pis=[make_pi("A", "10.0.0.1")], ids={"10.0.0.1": "B"})
    with c:
        assert c.post("/api/pis/A/shutdown").status_code == 409
    assert not [r for r in fake.requests if r.method == "POST"]


def test_bench_round_collects_results(make_client):
    c, reg, fake = make_client({"10.0.0.1": "ok", "10.0.0.2": "ok"},
                               pis=[make_pi("A", "10.0.0.1"), make_pi("B", "10.0.0.2")])
    with c:
        r = c.post("/api/bench", json={"ids": ["A"], "tests": ["cpu", "net"]})
        assert r.status_code == 200 and r.json() == {"A": {"started": True}}
        assert fake.posted[-1] == ("/bench", {"tests": ["cpu", "net"]})
        fake.bench_status["10.0.0.1"] = {"state": "done", "job": 1, "results": {"cpu": {"int_1": 760.0}},
                                         "errors": {"net": "timed out"}, "current": None}
        table = c.get("/api/bench").json()
    rows = {row["id"]: row for row in table["rows"]}
    assert rows["A"]["results"]["cpu"]["int_1"] == 760.0 and rows["A"]["results"]["cpu"]["measured"]
    assert rows["A"]["results"]["net"]["error"] == "timed out"
    assert rows["B"]["results"] == {} and table["running"] == {}
    store = json.loads((reg.path.parent / "bench.json").read_text())
    assert store["pis"]["A"]["cpu"]["int_1"] == 760.0


def test_bench_round_shows_running(make_client):
    c, _, fake = make_client({"10.0.0.1": "ok"}, pis=[make_pi("A", "10.0.0.1")])
    with c:
        c.post("/api/bench", json={"ids": ["A"], "tests": ["disk"]})
        fake.bench_status["10.0.0.1"] = {"state": "running", "job": 1, "current": "disk", "results": {}, "errors": {}}
        assert c.get("/api/bench").json()["running"] == {"A": {"current": "disk", "tests": None}}


def test_bench_round_rejects_bad_input_and_busy_pi(make_client):
    c, _, fake = make_client({"10.0.0.1": "ok"}, pis=[make_pi("A", "10.0.0.1")])
    with c:
        assert c.post("/api/bench", json={"ids": [], "tests": ["cpu"]}).status_code == 400
        assert c.post("/api/bench", json={"ids": ["A"], "tests": ["gpu"]}).status_code == 400
        assert c.post("/api/bench", json={"ids": ["Z"], "tests": ["cpu"]}).json() == {"Z": {"error": "unknown Pi"}}
        fake.bench_busy = True
        assert "already running" in c.post("/api/bench", json={"ids": ["A"], "tests": ["cpu"]}).json()["A"]["error"]


def test_stress_proxy(make_client):
    c, _, fake = make_client({"10.0.0.1": "ok"}, pis=[make_pi("A", "10.0.0.1")])
    with c:
        r = c.post("/api/pis/A/stress", json={"cpu": True, "ram": False, "net": False, "minutes": 5})
        assert r.status_code == 200
        assert fake.posted[-1] == ("/stress", {"cpu": True, "ram": False, "net": False, "minutes": 5})
        assert c.post("/api/pis/A/stress/stop").status_code == 200
        assert fake.posted[-1][0] == "/stress/stop"
        assert c.post("/api/pis/A/stress", json={"cpu": True}, headers={"Origin": "http://evil.example"}).status_code == 403


def test_busy_pi_message_is_not_called_unreachable(make_client):
    c, _, fake = make_client({"10.0.0.1": "ok"}, pis=[make_pi("A", "10.0.0.1")])
    fake.bench_busy = True
    with c:
        err = c.post("/api/bench", json={"ids": ["A"], "tests": ["cpu"]}).json()["A"]["error"]
    assert err == "a benchmark is already running on this Pi"


def test_now_reads_ticked_pis_and_tolerates_failures(make_client):
    c, _, _ = make_client({"10.0.0.1": "ok", "10.0.0.2": "hang", "10.0.0.3": "ok"},
                          pis=[make_pi("A", "10.0.0.1"), make_pi("B", "10.0.0.2"), make_pi("C", "10.0.0.3")],
                          ids={"10.0.0.3": "someone-else"})
    with c:
        t0 = time.monotonic()
        r = c.get("/api/now", params={"ids": "A,B,C,Z"}).json()
        assert time.monotonic() - t0 < 2
    assert r["A"]["cpu_pct"] == 5.0
    assert r["B"] is None and r["C"] is None and "Z" not in r


def test_now_requires_ids(make_client):
    c, _, _ = make_client({})
    with c:
        assert c.get("/api/now").status_code == 422
