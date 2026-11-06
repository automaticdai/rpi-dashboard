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
    assert [p.hostname for p in r2.all()] == ["rpi-3", "rpi-zero-2"]
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
