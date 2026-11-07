import json

from rpidash_dashboard.bench_store import BenchStore


def test_record_and_reload(tmp_path):
    p = tmp_path / "bench.json"
    s = BenchStore(p)
    s.record("A", "cpu", {"int_1": 760.0}, "2026-11-07T10:00:00+00:00")
    s.save()
    s2 = BenchStore(p); s2.load()
    assert s2.results("A") == {"cpu": {"int_1": 760.0, "measured": "2026-11-07T10:00:00+00:00"}}


def test_new_round_keeps_other_tests(tmp_path):
    s = BenchStore(tmp_path / "bench.json")
    s.record("A", "cpu", {"int_1": 1.0}, "t1")
    s.record("A", "disk", {"seq_read_MBps": 20.0}, "t1")
    s.record("A", "cpu", {"int_1": 2.0}, "t2")
    assert s.results("A")["cpu"] == {"int_1": 2.0, "measured": "t2"}
    assert s.results("A")["disk"]["seq_read_MBps"] == 20.0


def test_error_recorded_without_losing_last_good_result(tmp_path):
    s = BenchStore(tmp_path / "bench.json")
    s.record("A", "disk", {"seq_read_MBps": 50.0}, "t1")
    s.record_error("A", "disk", "not enough free space", "t2")
    r = s.results("A")["disk"]
    assert r["seq_read_MBps"] == 50.0 and r["measured"] == "t1"
    assert r["error"] == "not enough free space" and r["error_at"] == "t2"
    s.record("A", "disk", {"seq_read_MBps": 60.0}, "t3")
    assert "error" not in s.results("A")["disk"]


def test_missing_or_corrupt_file(tmp_path):
    p = tmp_path / "bench.json"
    s = BenchStore(p); s.load()
    assert s.results("A") == {}
    p.write_text("{broken")
    s = BenchStore(p); s.load()
    assert s.results("A") == {} and (tmp_path / "bench.json.bak").exists()


def test_forget(tmp_path):
    s = BenchStore(tmp_path / "bench.json")
    s.record("A", "cpu", {}, "t")
    s.forget("A")
    assert s.results("A") == {}
