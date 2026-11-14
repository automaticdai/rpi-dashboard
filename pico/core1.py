"""Runs the application (app.py) on core 1, apart from the agent on core 0.

The runner calls app.setup(values) once, then app.step(values) every app.PERIOD_MS, catches a
crash instead of letting it end the thread, and restarts the app when asked (inside the same
thread, between two steps). The thread only ends when main.py stops (close()), so that core 1 is
idle before the REPL or a soft reset takes over.

Nothing here prints: core 1 writing to USB serial (owned by core 0) can hang the Pico.
"""
import _thread
import io
import sys
import time

STUCK_MS = 10000  # a step (or setup) running this long without returning counts as stuck


def _error_text(e):
    buf = io.StringIO()
    if hasattr(sys, "print_exception"):
        sys.print_exception(e, buf)
    else:  # CPython
        import traceback
        traceback.print_exception(type(e), e, e.__traceback__, file=buf)
    lines = buf.getvalue().strip().split("\n")
    return "\n".join(lines[-6:])  # the last frames and the message are what matter


class Runner:
    def __init__(self, app):
        self.app = app
        self.name = getattr(app, "NAME", "app")
        self.period_ms = max(1, int(getattr(app, "PERIOD_MS", 1000)))
        self.state = "starting"   # starting | running | crashed | restarting | stopped
        self.error = None
        self.restarts = 0
        self.steps = 0
        self.values = {}          # a snapshot of what the app publishes, refreshed at most once a second
        self._live = {}           # the dict the app writes into (only core 1 touches it)
        self._restart = False
        self._closed = False
        self._started_ms = time.ticks_ms()
        self._progress_ms = time.ticks_ms()  # last time setup or a step returned
        self._busy_us = 0
        self._window_ms = time.ticks_ms()

    # ---- called from core 0 ----
    def start(self):
        _thread.start_new_thread(self._run, ())

    def stuck(self):
        return self.state in ("starting", "running", "restarting") and \
            time.ticks_diff(time.ticks_ms(), self._progress_ms) > max(STUCK_MS, 3 * self.period_ms)

    def request_restart(self):
        """Ask core 1 to restart the app at the next step boundary (or straight away after a crash)."""
        if self.stuck():
            return False
        self._restart = True
        return True

    def close(self, wait_ms=1500):
        """Stop the app (calling its stop()) and end the thread; True once core 1 is idle."""
        self._closed = self._restart = True
        t = time.ticks_ms()
        while self.state != "stopped" and time.ticks_diff(time.ticks_ms(), t) < wait_ms:
            time.sleep_ms(10)
        return self.state == "stopped"

    def cpu_pct(self):
        """Share of core 1 spent in setup()/step() since the last call."""
        now = time.ticks_ms()
        span = time.ticks_diff(now, self._window_ms)
        busy, self._busy_us, self._window_ms = self._busy_us, 0, now
        return round(min(100.0, busy / 10 / span), 1) if span > 0 else 0.0

    def status(self):
        state = "stuck" if self.stuck() else self.state
        return {"name": self.name, "state": state, "error": self.error, "restarts": self.restarts,
                "steps": self.steps, "period_ms": self.period_ms,
                "uptime_s": time.ticks_diff(time.ticks_ms(), self._started_ms) // 1000,
                "values": self.values}

    # ---- core 1 ----
    def _timed(self, fn):
        t = time.ticks_us()
        try:
            fn(self._live)
        finally:
            self._busy_us += time.ticks_diff(time.ticks_us(), t)
            self._progress_ms = time.ticks_ms()

    def _run(self):
        while not self._closed:
            self._restart = False
            self._live = {}
            self.values = {}
            self._started_ms = self._progress_ms = time.ticks_ms()
            try:
                self._timed(self.app.setup)
                self.state, self.error = "running", None
                self._loop()
                self.state = "restarting"
                if hasattr(self.app, "stop"):
                    self._timed(self.app.stop)
            except Exception as e:  # the app's bug must not end core 1
                self.state, self.error = "crashed", _error_text(e)
                self.values = dict(self._live)
                while not self._restart:
                    time.sleep_ms(100)
            if not self._closed:
                self.restarts += 1
                self.state = "starting"
        self.state = "stopped"

    def _loop(self):
        step, period = self.app.step, self.period_ms
        next_ms = snap_ms = time.ticks_ms()
        while not self._restart:
            self._timed(step)
            self.steps += 1
            now = time.ticks_ms()
            if time.ticks_diff(now, snap_ms) >= 1000:  # publish a copy: core 0 reads it while core 1 writes
                self.values, snap_ms = dict(self._live), now
            next_ms = time.ticks_add(next_ms, period)
            wait = time.ticks_diff(next_ms, now)
            if wait > 0:
                time.sleep_ms(wait)
            else:
                next_ms = now  # overran: don't try to catch up
        self.values = dict(self._live)
