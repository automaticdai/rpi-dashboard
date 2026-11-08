import time

from conftest import make_pi


def events(c, **params):
    return c.get("/api/events", params=params).json()


def test_dropout_vs_reboot(make_client):
    c, _, fake = make_client({"10.0.0.1": "ok"}, pis=[make_pi("A", "10.0.0.1")])
    with c:
        c.get("/api/pis")                                 # first sight: no event
        fake.behaviours["10.0.0.1"] = "down"; c.get("/api/pis")
        fake.behaviours["10.0.0.1"] = "ok"; fake.uptime = 99_999.0
        c.get("/api/pis")                                 # back, long uptime: a network dropout
        fake.behaviours["10.0.0.1"] = "down"; c.get("/api/pis")
        fake.behaviours["10.0.0.1"] = "ok"; fake.uptime = 3.0
        c.get("/api/pis")                                 # back, fresh uptime: it rebooted
        ev = events(c)
    kinds = [(e["kind"], e.get("cause")) for e in ev["events"] if e["pi"] == "A"]
    assert kinds == [("online", "reboot"), ("offline", None), ("online", "network"), ("offline", None)]  # newest first
    assert ev["summary"]["A"]["dropouts"] == 1 and ev["summary"]["A"]["reboots"] == 1
    assert ev["summary"]["A"]["longest_outage_s"] >= 0


def test_actions_are_logged(make_client):
    c, _, _ = make_client({"10.0.0.1": "ok"}, pis=[make_pi("A", "10.0.0.1")])
    with c:
        c.post("/api/pis/A/reboot")
        c.post("/api/actions", json={"ids": ["A"], "action": "shutdown"})
        ev = events(c, pi="A")
    acts = [(e["kind"], e["what"], e["ok"]) for e in ev["events"]]
    assert ("action", "shutdown", True) in acts and ("action", "reboot", True) in acts


def test_events_survive_restart(make_client):
    c, _, fake = make_client({"10.0.0.1": "ok"}, pis=[make_pi("A", "10.0.0.1")])
    with c:
        c.get("/api/pis")
        fake.behaviours["10.0.0.1"] = "down"; c.get("/api/pis")
    c2, _, fake2 = make_client({"10.0.0.1": "ok"}, pis=[make_pi("A", "10.0.0.1")])
    fake2.uptime = 99_999.0
    with c2:
        c2.get("/api/pis")                                # still remembers it went offline
        ev = events(c2)
    assert [e["kind"] for e in ev["events"]][:2] == ["online", "offline"]
