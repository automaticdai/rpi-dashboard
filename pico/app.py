"""Your application. It runs next to the rpidash agent (Wi-Fi, web server).

    setup(values)   runs once at start and after each restart from the dashboard
    step(values)    runs every PERIOD_MS; keep it short and return (don't loop forever in it)
    stop(values)    optional: runs before a restart, e.g. to switch outputs off

`values` is a dict you fill with numbers or short strings; the dashboard shows them on the
Pico's page (refreshed about once a second). Crashes are caught and shown there too.

Which core:
  CORE = 0 (default, recommended): step() takes turns with the agent on core 0. Reliable. A slow
      step() delays the web server, and steps can start tens of ms late while it answers
      requests, so keep each step to a few ms.
  CORE = 1 (experimental): step() runs on its own core, with ~1 ms timing. But with MicroPython
      1.29, a busy thread on core 1 plus Wi-Fi traffic has hard-locked the Pico (it needed
      unplugging), and even a light one makes the agent use about twice the CPU. Only for
      light, timing-critical code. Rules on core 1: no Wi-Fi or sockets, no print(), don't touch
      Pin("LED") (it's on the Wi-Fi chip), and avoid creating objects every step.

Either way: use GPIO pins, I2C, SPI, UART, PWM and ADC 0-2 freely.

This placeholder measures its own timing: how late each step starts.
"""
import time

NAME = "Timing demo"
CORE = 0
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
