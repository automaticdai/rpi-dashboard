"""Hardware watchdog: resets the Pico if core 0 stops running the agent for TIMEOUT_MS.

An asyncio task feeds it every second, so it fires when core 0 locks up (as a busy thread on
core 1 plus Wi-Fi traffic has done) or when anything blocks the agent's loop for 8 s.

MicroPython can't switch the watchdog off again, but the chip can: disarm() clears its enable
bit, so that stopping main.py on purpose (Ctrl-C, mpremote, dev.sh) doesn't reset the board 8 s
later in the middle of an upload.

To tell a hang from other resets afterwards, scratch register 1 holds ARMED while the watchdog
is armed (it survives a watchdog reset, not a power cycle), and the reset reason register says
TIMER for a timeout but FORCE for machine.reset() (which mpremote and /reboot use).
"""
import sys

import machine

TIMEOUT_MS = 8000  # the RP2350 allows at most ~8.3 s
_BASE = 0x400D8000 if "RP2350" in sys.implementation._machine else 0x40058000  # WATCHDOG_BASE
_REASON = _BASE + 0x08
_SCRATCH1 = _BASE + 0x10
_CLEAR = _BASE + 0x3000  # atomic bit-clear alias of CTRL
_ENABLE = 1 << 30
_TIMER = 1
_ARMED = 0x5AFEC0DE

_wdt = None


def was_hang():
    """True if the last reset was this watchdog firing. Call once at boot, before arm()."""
    hang = bool(machine.mem32[_REASON] & _TIMER) and machine.mem32[_SCRATCH1] == _ARMED
    machine.mem32[_SCRATCH1] = 0
    return hang


def arm():
    global _wdt
    machine.mem32[_SCRATCH1] = _ARMED
    _wdt = machine.WDT(timeout=TIMEOUT_MS)


async def keep_fed():
    import asyncio
    while True:
        _wdt.feed()
        await asyncio.sleep(1)


def disarm():
    if _wdt is not None:
        machine.mem32[_CLEAR] = _ENABLE
    machine.mem32[_SCRATCH1] = 0
