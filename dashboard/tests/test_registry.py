import json
import pytest

from rpidash_dashboard.registry import Pi, Registry


def pi(id_, hostname, host="192.168.0.5"):
    return Pi(id=id_, hostname=hostname, host=host, port=8765, spec={"model": "Pi"},
              added="2026-11-06T00:00:00+00:00", last_seen=None)


def test_registry_roundtrip(tmp_path):
    path = tmp_path / "pis.json"
    r = Registry(path)
    r.load()
    r.upsert(pi("B", "rpi-zero-2"))
    r.upsert(pi("A", "rpi-3"))
    r.save()
    r2 = Registry(path)
    r2.load()
    assert [p.hostname for p in r2.all()] == ["rpi-zero-2", "rpi-3"]  # added order, not name order
    assert r2.all() == r.all()


def test_registry_missing_file(tmp_path):
    r = Registry(tmp_path / "pis.json")
    r.load()
    assert r.all() == []


def test_registry_corrupt(tmp_path):
    path = tmp_path / "pis.json"
    path.write_text("{not json")
    r = Registry(path)
    r.load()
    assert r.all() == []
    assert (tmp_path / "pis.json.bak").read_text() == "{not json"
    r.upsert(pi("A", "a"))
    r.save()
    r2 = Registry(path); r2.load()
    assert [p.id for p in r2.all()] == ["A"]


def test_registry_bad_shape(tmp_path):
    path = tmp_path / "pis.json"
    path.write_text('[{"id": 1}]')
    r = Registry(path)
    r.load()
    assert r.all() == []
    assert (tmp_path / "pis.json.bak").exists()


def test_upsert_updates_host(tmp_path):
    r = Registry(tmp_path / "pis.json")
    r.upsert(pi("A", "a", host="192.168.0.5"))
    r.upsert(pi("A", "a", host="192.168.0.6"))
    assert [p.host for p in r.all()] == ["192.168.0.6"]
    assert r.get("A").host == "192.168.0.6"
    assert r.get("Z") is None


def test_remove(tmp_path):
    r = Registry(tmp_path / "pis.json")
    r.upsert(pi("A", "a"))
    assert r.remove("A") is True
    assert r.remove("A") is False


def test_new_pis_go_last_and_reorder_persists(tmp_path):
    path = tmp_path / "pis.json"
    r = Registry(path)
    for id_, name in [("A", "a"), ("B", "b"), ("C", "c")]:
        r.upsert(pi(id_, name))
    assert [p.id for p in r.all()] == ["A", "B", "C"]
    r.reorder(["C", "A", "B"])
    r.save()
    r.upsert(pi("D", "0-sorts-first-by-name"))
    assert [p.id for p in r.all()] == ["C", "A", "B", "D"]
    r2 = Registry(path); r2.load()
    assert [p.id for p in r2.all()] == ["C", "A", "B"]


def test_reorder_ignores_unknown_and_keeps_unlisted_after(tmp_path):
    r = Registry(tmp_path / "pis.json")
    for id_ in "ABC":
        r.upsert(pi(id_, id_.lower()))
    r.reorder(["C", "Z"])
    assert [p.id for p in r.all()] == ["C", "A", "B"]


def test_upsert_existing_keeps_position(tmp_path):
    r = Registry(tmp_path / "pis.json")
    for id_ in "AB":
        r.upsert(pi(id_, id_.lower()))
    r.reorder(["B", "A"])
    r.upsert(pi("B", "b", host="192.168.0.99"))
    assert [p.id for p in r.all()] == ["B", "A"]


def test_load_file_without_order_field(tmp_path):
    path = tmp_path / "pis.json"
    old = {"id": "A", "hostname": "a", "host": "h", "port": 8765, "spec": None, "added": "x", "last_seen": None}
    path.write_text(json.dumps({"pis": [old]}))
    r = Registry(path); r.load()
    assert [p.id for p in r.all()] == ["A"]


@pytest.mark.parametrize("bad_field", [{"hostname": None}, {"port": "x"}, {"spec": "nope"}])
def test_registry_bad_field_types(tmp_path, bad_field):
    path = tmp_path / "pis.json"
    good = {"id": "A", "hostname": "a", "host": "h", "port": 8765, "spec": None, "added": "x", "last_seen": None}
    path.write_text(json.dumps({"pis": [good | bad_field]}))
    r = Registry(path); r.load()
    assert r.all() == []
    assert (tmp_path / "pis.json.bak").exists()


def test_registry_unreadable_corrupt_file_does_not_crash(tmp_path, monkeypatch):
    import os
    path = tmp_path / "pis.json"
    path.write_text("{not json")
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(PermissionError("read-only")))
    r = Registry(path); r.load()
    assert r.all() == []
