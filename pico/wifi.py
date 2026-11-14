"""Connect the Pico 2 W to Wi-Fi without blocking other asyncio tasks."""
import asyncio
import network
import time

_REASONS = {
    network.STAT_WRONG_PASSWORD: "wrong password",
    network.STAT_NO_AP_FOUND: "network not found",
    network.STAT_CONNECT_FAIL: "connection failed",
}

_wlan = network.WLAN(network.STA_IF)


async def connect(ssid, password, *, country="GB", hostname="pico2w", timeout_s=20):
    """Connect (or confirm we're connected). Returns the IP address, raises OSError on failure."""
    network.country(country)
    network.hostname(hostname)
    _wlan.active(True)
    _wlan.config(pm=network.WLAN.PM_NONE)  # power saving makes it slow to answer requests
    if _wlan.isconnected():
        return ip()
    _wlan.connect(ssid, password)
    deadline = time.ticks_add(time.ticks_ms(), timeout_s * 1000)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        st = _wlan.status()
        if st == network.STAT_GOT_IP:
            return ip()
        if st in _REASONS:
            break
        await asyncio.sleep_ms(200)  # other tasks keep running while we wait
    st = _wlan.status()
    _wlan.disconnect()
    raise OSError("Wi-Fi: " + _REASONS.get(st, "timed out (status %d)" % st))


def is_connected():
    return _wlan.isconnected()


def ip():
    return _wlan.ifconfig()[0]


def rssi():
    """Signal strength in dBm, or None when not connected."""
    try:
        return _wlan.status("rssi") if _wlan.isconnected() else None
    except OSError:
        return None


def mac():
    return _wlan.config("mac").hex(":")
