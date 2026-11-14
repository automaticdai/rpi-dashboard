"""Readings, hardware details and in-memory history, in the shapes the rpidash dashboard expects.

Times are whole Unix seconds (ints): MicroPython floats can't hold them exactly.
"""
import gc
import os
import sys
import time
from array import array

import machine

import wifi

INTERVAL_S = 10       # one reading every 10 s, like the Linux agent
COARSE_S = 300        # 5-minute summaries for the longer ranges
RANGES = {"1h": 3600, "6h": 21600, "24h": 86400, "7d": 604800}
POINTS = 300          # at most this many points per chart, like the Linux agent

_temp_adc = machine.ADC(4)  # the chip's built-in temperature sensor
_NAN = float("nan")


def _isnan(v):
    return v != v


def temperature():
    # One ADC step is ~0.47 °C; averaging 16 reads gives a smooth line instead of a staircase.
    volts = sum(_temp_adc.read_u16() for _ in range(16)) / 16 * 3.3 / 65535
    return round(27 - (volts - 0.706) / 0.001721, 1)


def heap():
    """(used, total) bytes of the MicroPython heap: the Pico's usable RAM."""
    free, used = gc.mem_free(), gc.mem_alloc()
    return used, used + free


def flash():
    """(percent used, total bytes) of the flash filesystem."""
    s = os.statvfs("/")
    total = s[0] * s[2]
    return (100 * (total - s[0] * s[3]) / total if total else 0.0), total


def sample(now, cores=None):
    """cores: (core 0 %, core 1 %) or None. cpu_pct is their average, like a Pi's overall CPU %."""
    used, total = heap()
    return {
        "ts": now, "temp_c": temperature(), "mem_used": used, "mem_total": total,
        "disk_pct": round(flash()[0], 1), "wifi_dbm": wifi.rssi(),
        "cpu_pct": round((cores[0] + cores[1]) / 2, 1) if cores else None,
        "cpu_cores": list(cores) if cores else None,  # extra to the Linux agent's fields
        # Not measured on a microcontroller: no OS, no network counters, no fan, no throttling.
        "load1": None, "swap_used": None, "net_rx_bps": None, "net_tx_bps": None,
        "throttled": None, "fan_rpm": None, "power_w": None, "disk_read_bps": None, "disk_write_bps": None,
    }


def spec(device_id, hostname, version, boot_time):
    return {
        "kind": "mcu",
        "model": sys.implementation._machine.replace(" with RP2350", ""),
        "serial": device_id,
        "cpu": "RP2350, Arm Cortex-M33 at %d MHz" % (machine.freq() // 1000000),
        "cores": 2,
        "ram_total": heap()[1],
        "root_device": None,
        "storage": "Flash",
        "root_size": flash()[1],
        "os": "MicroPython %s" % ".".join(str(n) for n in sys.implementation.version[:3]),
        "kernel": None,
        "arch": "Armv8-M",
        "hostname": hostname,
        "interfaces": [{"name": "wlan0", "ipv4": wifi.ip() if wifi.is_connected() else None, "mac": wifi.mac()}],
        "agent_version": version,
        "boot_time": boot_time,
    }


class _Ring:
    """Fixed-size history: one compact array per field."""
    FIELDS = ("ts", "temp_c", "mem_used", "disk_pct", "wifi_dbm", "cpu0", "cpu1")

    def __init__(self, capacity):
        self.capacity, self.idx, self.count = capacity, 0, 0
        # MicroPython's array has no `* n`, and array(code, bytearray(n)) would read raw bytes
        self.a = {f: array("I" if f in ("ts", "mem_used") else "f", (0 for _ in range(capacity)))
                  for f in self.FIELDS}

    def add(self, row):
        for f in self.FIELDS:
            v = row.get(f)
            self.a[f][self.idx] = _NAN if v is None else v
        self.idx = (self.idx + 1) % self.capacity
        self.count = min(self.count + 1, self.capacity)

    def rows(self):
        """Indexes from oldest to newest."""
        start = 0 if self.count < self.capacity else self.idx
        for k in range(self.count):
            yield (start + k) % self.capacity

    def oldest(self):
        return self.a["ts"][next(self.rows())] if self.count else None


class History:
    def __init__(self):
        self.fine = _Ring(3600 // INTERVAL_S + 6)       # a bit over an hour of 10 s readings
        self.coarse = _Ring(604800 // COARSE_S + 12)    # a bit over a week of 5-minute summaries
        self.mem_total = heap()[1]
        self.first_ts = None  # first reading since boot (summaries are stamped with their slot's start)
        self._acc = None  # the 5-minute summary being built

    def add(self, s):
        if self.first_ts is None:
            self.first_ts = s["ts"]
        cores = s.get("cpu_cores") or (None, None)
        self.fine.add({"ts": s["ts"], "temp_c": s["temp_c"], "mem_used": s["mem_used"], "disk_pct": s["disk_pct"],
                       "wifi_dbm": s["wifi_dbm"], "cpu0": cores[0], "cpu1": cores[1]})
        self.mem_total = s["mem_total"]
        slot = s["ts"] // COARSE_S * COARSE_S
        acc = self._acc
        if acc and acc["ts"] != slot:
            self._flush()
            acc = None
        if acc is None:
            acc = self._acc = {"ts": slot, "n": 0, "temp_c": None, "mem_used": 0, "disk_pct": 0.0,
                               "wifi_sum": 0, "wifi_n": 0, "cpu0": 0.0, "cpu1": 0.0, "cpu_n": 0}
        acc["n"] += 1
        if s["temp_c"] is not None:  # hottest reading, like the Linux agent's charts
            acc["temp_c"] = s["temp_c"] if acc["temp_c"] is None else max(acc["temp_c"], s["temp_c"])
        acc["mem_used"] += s["mem_used"]
        acc["disk_pct"] = s["disk_pct"]
        if s["wifi_dbm"] is not None:
            acc["wifi_sum"] += s["wifi_dbm"]
            acc["wifi_n"] += 1
        if cores[0] is not None:
            acc["cpu0"] += cores[0]
            acc["cpu1"] += cores[1]
            acc["cpu_n"] += 1

    def _flush(self):
        a = self._acc
        self.coarse.add({"ts": a["ts"], "temp_c": a["temp_c"], "mem_used": a["mem_used"] // a["n"],
                         "disk_pct": a["disk_pct"],
                         "wifi_dbm": a["wifi_sum"] / a["wifi_n"] if a["wifi_n"] else None,
                         "cpu0": a["cpu0"] / a["cpu_n"] if a["cpu_n"] else None,
                         "cpu1": a["cpu1"] / a["cpu_n"] if a["cpu_n"] else None})

    def _points(self, span):
        """(ts, temp, mem_used, disk_pct, wifi, cpu0, cpu1) from oldest to newest: 10 s readings for the last
        hour, otherwise 5-minute summaries followed by the readings not yet summarised."""
        rings = [self.fine] if span <= 3600 else [self.coarse, self.fine]
        after = -1
        for ring in rings:
            a = ring.a
            for i in ring.rows():
                ts = a["ts"][i]
                if ts > after:
                    yield (ts, a["temp_c"][i], a["mem_used"][i], a["disk_pct"][i], a["wifi_dbm"][i],
                           a["cpu0"][i], a["cpu1"][i])
            if ring is self.coarse and ring.count:
                after = a["ts"][(ring.idx - 1) % ring.capacity] + COARSE_S - 1

    def query(self, range_name, now):
        """Charts for one range: {since, ts: [...], temp_c: [...], ...} with at most POINTS points."""
        span = RANGES[range_name]
        olds = [t for t in (self.coarse.oldest(), self.fine.oldest()) if t is not None]
        out = {"since": max(min(olds), self.first_ts) if olds else None, "ts": [], "temp_c": [], "mem_used": [], "mem_total": [],
               "disk_pct": [], "wifi_dbm": [], "cpu_pct": [], "cpu0_pct": [], "cpu1_pct": []}
        t0, width = now - span, span / POINTS
        bucket, rows = None, []

        def emit():
            temps = [r[1] for r in rows if not _isnan(r[1])]
            wifis = [r[4] for r in rows if not _isnan(r[4])]
            out["ts"].append(rows[-1][0])
            out["temp_c"].append(round(max(temps), 1) if temps else None)
            out["mem_used"].append(sum(r[2] for r in rows) // len(rows))
            out["mem_total"].append(self.mem_total)
            out["disk_pct"].append(round(sum(r[3] for r in rows) / len(rows), 1))
            out["wifi_dbm"].append(round(sum(wifis) / len(wifis), 1) if wifis else None)
            c = [r for r in rows if not _isnan(r[5])]
            c0 = round(sum(r[5] for r in c) / len(c), 1) if c else None
            c1 = round(sum(r[6] for r in c) / len(c), 1) if c else None
            out["cpu0_pct"].append(c0)
            out["cpu1_pct"].append(c1)
            out["cpu_pct"].append(round((c0 + c1) / 2, 1) if c else None)

        for r in self._points(span):
            if r[0] < t0:
                continue
            b = min(int((r[0] - t0) / width), POINTS - 1)
            if b != bucket and rows:
                emit()
                rows = []
            bucket = b
            rows.append(r)
        if rows:
            emit()
        return out


def epoch_ok():
    """True once NTP has set the clock (it starts in 2021 after power-up)."""
    return time.time() > 1700000000
