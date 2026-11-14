"""CPU use of core 0 (the agent), measured as the time asyncio's loop is NOT waiting for work.

MicroPython's asyncio does all its idle waiting in one call, IOQueue.wait_io_event(); wrapping
it counts the idle time, and busy = elapsed - idle.
"""
import time

from asyncio import core

_idle_us = 0
_window_ms = time.ticks_ms()


def install():
    q = core._io_queue
    wait = q.wait_io_event

    def timed_wait(dt):
        global _idle_us
        t = time.ticks_us()
        try:
            wait(dt)
        finally:
            _idle_us += time.ticks_diff(time.ticks_us(), t)

    q.wait_io_event = timed_wait


def core0_pct():
    """Share of core 0 in use since the last call."""
    global _idle_us, _window_ms
    now = time.ticks_ms()
    span_us = time.ticks_diff(now, _window_ms) * 1000
    idle, _idle_us, _window_ms = _idle_us, 0, now
    return round(max(0.0, min(100.0, 100 - idle * 100 / span_us)), 1) if span_us > 0 else 0.0
