"""Run the Pico's history code under CPython, with stand-ins for the Pico-only modules."""
import importlib
import sys
import types
from pathlib import Path

import pytest

PICO = Path(__file__).parents[1]


@pytest.fixture
def metrics(monkeypatch):
    machine = types.ModuleType("machine")
    machine.ADC = lambda n: types.SimpleNamespace(read_u16=lambda: 14000)
    machine.freq = lambda: 150_000_000
    wifi = types.ModuleType("wifi")
    wifi.rssi, wifi.ip, wifi.is_connected, wifi.mac = (lambda: -40), (lambda: "10.0.0.9"), (lambda: True), (lambda: "aa:bb")
    gc = types.ModuleType("gc")
    gc.mem_free, gc.mem_alloc, gc.collect = (lambda: 400_000), (lambda: 46_000), (lambda: None)
    for name, mod in (("machine", machine), ("wifi", wifi), ("gc", gc)):
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setattr("os.statvfs", lambda p: (4096, 4096, 640, 635), raising=False)
    monkeypatch.syspath_prepend(str(PICO))
    sys.modules.pop("metrics", None)
    m = importlib.import_module("metrics")
    yield m
    sys.modules.pop("metrics", None)


def fill(m, h, start, seconds):
    for t in range(start, start + seconds, m.INTERVAL_S):
        h.add(m.sample(t))
    return start + seconds - m.INTERVAL_S


@pytest.mark.parametrize("rng", ["1h", "6h", "24h", "7d"])
def test_every_range_after_a_week(metrics, rng):
    h = metrics.History()
    now = fill(metrics, h, 1_794_600_000, 8 * 86400)
    q = h.query(rng, now)
    n = len(q["ts"])
    assert 0 < n <= metrics.POINTS
    assert all(len(q[k]) == n for k in q if k != "since")
    assert q["ts"] == sorted(q["ts"]) and q["ts"][-1] == now  # newest reading included
    assert q["ts"][0] >= now - metrics.RANGES[rng]
    assert q["since"] < now - 6 * 86400  # a week kept; older readings dropped


def test_fresh_boot_shows_readings_in_every_range_and_since_is_first_reading(metrics):
    h = metrics.History()
    start = 1_794_600_123
    now = fill(metrics, h, start, 120)
    for rng in metrics.RANGES:
        q = h.query(rng, now)
        assert q["ts"] and q["ts"][-1] == now, rng
        assert q["since"] == start, rng


def test_empty_history(metrics):
    q = metrics.History().query("1h", 1_794_600_000)
    assert q["since"] is None and q["ts"] == []


def test_sample_has_every_agent_field(metrics):
    from rpidash_agent.collect import FIELDS  # the Linux agent's sample, which the dashboard reads
    assert set(metrics.sample(1)) == set(FIELDS)
