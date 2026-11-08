import time

import pytest

from rpidash_agent.collect import Sample
from rpidash_agent.history import History


def s(ts, cpu=1.0, temp=40.0, throttled=0, fan=None, power=None):
    return Sample(ts=float(ts), cpu_pct=cpu, load1=0.1, mem_used=100, mem_total=1000, swap_used=0,
                  disk_pct=50.0, net_rx_bps=0.0, net_tx_bps=0.0, temp_c=temp, throttled=throttled, fan_rpm=fan,
                  power_w=power)


def test_empty():
    q = History(10).query(3600, now=1000)
    assert q["since"] is None
    assert q["ts"] == []
    assert History(10).latest() is None


def test_wraps():
    h = History(capacity=5)
    for t in range(1, 9):
        h.append(s(t))
    assert len(h) == 5
    assert h.oldest_ts() == 4.0
    assert h.latest().ts == 8.0


def test_query_window_and_since():
    h = History()
    for i in range(400):
        h.append(s(i * 10))
    q = h.query(3600, now=3990, points=300)
    assert 0 < len(q["ts"]) <= 300
    assert min(q["ts"]) >= 390
    assert q["since"] == 0.0
    assert set(q) >= {"since", "ts", "cpu_pct", "temp_c", "throttled", "mem_used", "net_rx_bps"}


def test_downsample_mean_and_max():
    h = History()
    h.append(s(0, cpu=10, temp=50))
    h.append(s(1, cpu=30, temp=60))
    q = h.query(3600, now=10, points=300)  # bucket width 12 s -> one bucket
    assert q["cpu_pct"] == [20.0]
    assert q["temp_c"] == [60.0]


def test_unknown_values():
    h = History()
    h.append(s(0, temp=None, throttled=None))
    assert h.latest().temp_c is None and h.latest().throttled is None
    q = h.query(3600, now=5)
    assert q["temp_c"] == [None] and q["throttled"] == [None]
    h2 = History()
    h2.append(s(0, throttled=0x1))
    h2.append(s(1, throttled=0x4))
    h2.append(s(2, throttled=None))
    assert h2.query(3600, now=5)["throttled"] == [0x5]


def test_full_week_memory():
    h = History()
    for i in range(60480):
        h.append(s(i * 10))
    assert len(h) == 60480
    assert h.nbytes() < 4_500_000  # ~68 bytes/sample; the binding limit is agent RSS < 50 MB


def test_query_speed():
    h = History()
    for i in range(60480):
        h.append(s(i * 10))
    t0 = time.perf_counter()
    q = h.query(604800, now=604790)
    assert time.perf_counter() - t0 < 1.0
    assert 250 <= len(q["ts"]) <= 300


def test_fan_rpm_mean_and_unknown():
    h = History()
    h.append(s(0, fan=1500))
    h.append(s(1, fan=1700))
    h.append(s(2, fan=None))
    assert h.query(3600, now=5)["fan_rpm"] == [1600.0]
    h2 = History()
    h2.append(s(0, fan=None))
    assert h2.query(3600, now=5)["fan_rpm"] == [None]
    assert h2.latest().fan_rpm is None


def test_query_during_append_sees_consistent_data(monkeypatch):
    """Force a query to run in the middle of an append (after the new ts is written)."""
    import threading
    from rpidash_agent import history as hmod
    h = History(capacity=5)
    for t in range(1, 8):  # wrapped buffer
        h.append(s(t))
    result = {}
    real_encode = hmod._encode
    started = threading.Event()

    def reader():
        result["q"] = h.query(300, now=10, points=300)  # 1 s buckets: one point per sample

    def encode(name, value):
        if name == "cpu_pct" and not started.is_set():  # ts already written for this sample
            started.set()
            th = threading.Thread(target=reader)
            th.start()
            th.join(0.3)  # with a lock the reader waits for us; without one it reads half-written data
            result["thread"] = th
        return real_encode(name, value)

    monkeypatch.setattr(hmod, "_encode", encode)
    h.append(s(8))
    result["thread"].join(2)
    q = result["q"]
    assert q["ts"] in ([3.0, 4.0, 5.0, 6.0, 7.0], [4.0, 5.0, 6.0, 7.0, 8.0])  # before or after, never a mix
    assert len({len(v) for k, v in q.items() if k != "since"}) == 1


def test_power_mean_and_unknown():
    h = History()
    h.append(s(0, power=1.5)); h.append(s(1, power=2.5)); h.append(s(2, power=None))
    assert h.query(3600, now=5)["power_w"] == [pytest.approx(2.0)]
    h2 = History(); h2.append(s(0))
    assert h2.query(3600, now=5)["power_w"] == [None] and h2.latest().power_w is None


def test_wifi_mean_and_unknown():
    from dataclasses import replace
    h = History()
    h.append(replace(s(0), wifi_dbm=-70)); h.append(replace(s(1), wifi_dbm=-60)); h.append(s(2))
    assert h.query(3600, now=5)["wifi_dbm"] == [pytest.approx(-65.0)]
    h2 = History(); h2.append(s(0))
    assert h2.latest().wifi_dbm is None
