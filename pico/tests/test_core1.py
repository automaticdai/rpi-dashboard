"""Run the core-1 app runner under CPython, in a real thread, with MicroPython's ticks functions."""
import importlib
import sys
import time
import types
from pathlib import Path

import pytest

PICO = Path(__file__).parents[1]


@pytest.fixture
def core1(monkeypatch):
    t0 = time.monotonic()
    ms = lambda: int((time.monotonic() - t0) * 1000)  # noqa: E731
    us = lambda: int((time.monotonic() - t0) * 1_000_000)  # noqa: E731
    for name, fn in (("ticks_ms", ms), ("ticks_us", us), ("ticks_diff", lambda a, b: a - b),
                     ("ticks_add", lambda a, b: a + b), ("sleep_ms", lambda n: time.sleep(n / 1000))):
        monkeypatch.setattr(time, name, fn, raising=False)
    monkeypatch.syspath_prepend(str(PICO))
    sys.modules.pop("core1", None)
    yield importlib.import_module("core1")
    sys.modules.pop("core1", None)


def wait_for(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def runners(core1):
    made = []

    def make(app):
        r = core1.Runner(app)
        made.append(r)
        return r
    yield make
    for r in made:
        r.close()
    time.sleep(0.4)  # let the threads see it before time's ticks functions go away


def make_app(**fns):
    return types.SimpleNamespace(**{"NAME": "test", "PERIOD_MS": 5, "setup": lambda v: None, "step": lambda v: None, **fns})


def test_runs_steps_and_publishes_values(core1, runners):
    def step(v):
        v["n"] = v.get("n", 0) + 1
    r = runners(make_app(step=step))
    r.start()
    assert wait_for(lambda: r.values.get("n", 0) > 10)
    st = r.status()
    assert st["state"] == "running" and st["name"] == "test" and st["error"] is None and st["steps"] > 10


def test_a_crash_is_caught_and_restart_runs_setup_again(core1, runners):
    calls = {"setup": 0}

    def setup(v):
        calls["setup"] += 1

    def step(v):
        if calls["setup"] == 1:
            raise ValueError("sensor unplugged")
    r = runners(make_app(setup=setup, step=step))
    r.start()
    assert wait_for(lambda: r.state == "crashed")
    assert "ValueError: sensor unplugged" in r.status()["error"]
    assert r.request_restart()
    assert wait_for(lambda: r.state == "running" and r.steps > 3)
    assert calls["setup"] == 2 and r.restarts == 1 and r.error is None


def test_restart_while_running_calls_stop_then_setup(core1, runners):
    log = []
    r = runners(make_app(setup=lambda v: log.append("setup"), stop=lambda v: log.append("stop")))
    r.start()
    assert wait_for(lambda: r.steps > 3)
    r.request_restart()
    assert wait_for(lambda: r.restarts == 1 and r.state == "running")
    assert log == ["setup", "stop", "setup"]


def test_a_step_that_never_returns_is_stuck_and_cant_be_restarted(core1, runners, monkeypatch):
    monkeypatch.setattr(core1, "STUCK_MS", 100)
    r = runners(make_app(step=lambda v: time.sleep(0.3)))
    r.start()
    assert wait_for(lambda: r.status()["state"] == "stuck")
    assert r.request_restart() is False


def test_cpu_share_of_a_busy_step(core1, runners):
    def busy(v):
        end = time.monotonic() + 0.004  # 4 ms of every 5 ms
        while time.monotonic() < end:
            pass
    r = runners(make_app(step=busy))
    r.cpu_pct()
    r.start()
    time.sleep(0.5)
    assert 50 <= r.cpu_pct() <= 100
