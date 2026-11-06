"""Fixed-size in-memory history of samples, stored compactly (one array per field)."""
from __future__ import annotations

import math
from array import array
from bisect import bisect_left

from .collect import FIELDS, Sample

RANGES = {"1h": 3600, "6h": 21600, "24h": 86400, "7d": 604800}

_TYPECODES = {
    "ts": "d",
    "mem_used": "q",
    "mem_total": "q",
    "swap_used": "q",
    "throttled": "i",
}
_NAN = float("nan")


def _encode(name: str, value):
    if name == "temp_c":
        return _NAN if value is None else value
    if name == "throttled":
        return -1 if value is None else value
    return value


def _decode(name: str, value):
    if name == "temp_c":
        return None if math.isnan(value) else value
    if name == "throttled":
        return None if value < 0 else value
    return value


class History:
    def __init__(self, capacity: int = 60480) -> None:
        self.capacity = capacity
        self._idx = 0  # next write position
        self._count = 0
        self._arrays = {n: array(_TYPECODES.get(n, "f"), [0]) * capacity for n in FIELDS}

    def append(self, s: Sample) -> None:
        for n in FIELDS:
            self._arrays[n][self._idx] = _encode(n, getattr(s, n))
        self._idx = (self._idx + 1) % self.capacity
        self._count = min(self._count + 1, self.capacity)

    def __len__(self) -> int:
        return self._count

    def nbytes(self) -> int:
        return sum(a.itemsize * len(a) for a in self._arrays.values())

    def _ordered(self, name: str) -> array:
        a = self._arrays[name]
        if self._count < self.capacity:
            return a[: self._count]
        return a[self._idx:] + a[: self._idx]

    def latest(self) -> Sample | None:
        if not self._count:
            return None
        i = (self._idx - 1) % self.capacity
        return Sample(**{n: _decode(n, self._arrays[n][i]) for n in FIELDS})

    def oldest_ts(self) -> float | None:
        if not self._count:
            return None
        i = 0 if self._count < self.capacity else self._idx
        return self._arrays["ts"][i]

    def query(self, range_s: int, now: float, points: int = 300) -> dict:
        out: dict = {"since": self.oldest_ts(), **{n: [] for n in FIELDS}}
        if not self._count:
            return out
        ts = self._ordered("ts")
        start = bisect_left(ts, now - range_s)
        if start >= len(ts):
            return out
        cols = {n: self._ordered(n) for n in FIELDS}
        width = range_s / points
        lo = start
        t0 = now - range_s
        for b in range(1, points + 1):
            hi = bisect_left(ts, t0 + b * width, lo) if b < points else len(ts)
            if hi <= lo:
                continue
            k = hi - lo
            for n in FIELDS:
                sl = cols[n][lo:hi]
                if n == "temp_c":
                    vals = [v for v in sl if not math.isnan(v)]
                    out[n].append(max(vals) if vals else None)
                elif n == "throttled":
                    acc, known = 0, False
                    for v in sl:
                        if v >= 0:
                            acc |= v
                            known = True
                    out[n].append(acc if known else None)
                else:
                    out[n].append(sum(sl) / k)
            lo = hi
        return out
