"""Your application. It runs on core 1; the rpidash agent (Wi-Fi, web server) runs on core 0.

    setup(values)   runs once at start and after each restart from the dashboard
    step(values)    runs every PERIOD_MS; keep it short and return (don't loop forever in it)
    stop(values)    optional: runs before a restart, e.g. to switch outputs off

`values` is a dict you fill with numbers or short strings; the dashboard shows them on the
Pico's page (refreshed about once a second). Crashes are caught and shown there too.

Rules for core 1:
  - Don't use Wi-Fi or sockets here, and don't touch Pin("LED"): on the Pico W the LED is wired
    to the Wi-Fi chip. Use GPIO pins, I2C, SPI, UART, PWM and ADC 0-2 freely.
  - The heap is shared with core 0: avoid creating objects every step in fast loops (reuse
    buffers), or memory clean-ups will pause the loop now and then.

This placeholder measures its own timing: how late each step starts, which shows how steady
core 1 is while core 0 serves the dashboard.
"""
import time

NAME = "Timing demo"
PERIOD_MS = 10

_last = 0
_worst = 0
_window = 0


def setup(values):
    global _last, _worst, _window
    _last = _window = time.ticks_ms()
    _worst = 0
    values["late_ms_max"] = 0


def step(values):
    global _last, _worst, _window
    now = time.ticks_ms()
    late = time.ticks_diff(now, _last) - PERIOD_MS
    _last = now
    if late > _worst:
        _worst = late
    if time.ticks_diff(now, _window) >= 1000:  # worst lateness over the last second
        values["late_ms_max"] = _worst
        _worst, _window = 0, now
