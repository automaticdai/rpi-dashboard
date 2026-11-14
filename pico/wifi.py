"""Connect the Pico 2 W to Wi-Fi and keep it connected."""
import network
import time

_REASONS = {
    network.STAT_WRONG_PASSWORD: "wrong password",
    network.STAT_NO_AP_FOUND: "network not found",
    network.STAT_CONNECT_FAIL: "connection failed",
}


def connect(ssid, password, *, country="GB", hostname="pico2w", timeout_s=20):
    """Connect (or confirm we're connected). Returns the IP address, raises OSError on failure."""
    network.country(country)
    network.hostname(hostname)
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.config(pm=network.WLAN.PM_NONE)  # power saving makes it slow to answer requests
    if wlan.isconnected():
        return wlan.ifconfig()[0]
    wlan.connect(ssid, password)
    deadline = time.ticks_add(time.ticks_ms(), timeout_s * 1000)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        st = wlan.status()
        if st == network.STAT_GOT_IP:
            return wlan.ifconfig()[0]
        if st in _REASONS:
            break
        time.sleep_ms(200)
    st = wlan.status()
    wlan.disconnect()
    raise OSError("Wi-Fi: " + _REASONS.get(st, "timed out (status %d)" % st))


def is_connected():
    return network.WLAN(network.STA_IF).isconnected()


def ip():
    return network.WLAN(network.STA_IF).ifconfig()[0]
