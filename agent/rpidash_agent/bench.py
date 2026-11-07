"""Benchmarks run by the agent: CPU (rpibench), disk (O_DIRECT, like ~/disk_bench.py) and
internet speed (Cloudflare). One job at a time, in a background thread."""
from __future__ import annotations

import http.client
import itertools
import json
import mmap
import os
import random
import shutil
import statistics
import subprocess
import threading
import time
from pathlib import Path

from . import __version__

TESTS = ("cpu", "disk", "net")
MiB = 1024 * 1024
BLK4K = 4096
NET_HOST = "speed.cloudflare.com"


class BenchError(Exception):
    pass


def _default_cpu_prefix() -> list[str]:
    # Run outside the agent's cgroup and nice level so the CPU gets a fair, full-speed run.
    if hasattr(os, "geteuid") and os.geteuid() == 0 and shutil.which("systemd-run"):
        return ["systemd-run", "--wait", "--pipe", "--quiet", "--collect", "--property=Nice=0", "--"]
    return []


# ---------- disk (same method as the owner's ~/disk_bench.py) ----------
def _aligned(n: int) -> mmap.mmap:
    return mmap.mmap(-1, n)  # page-aligned, as O_DIRECT needs


def _seq_write(path: str, size: int) -> float:
    buf = _aligned(MiB)
    buf.write(os.urandom(MiB))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_DIRECT, 0o600)
    t = time.perf_counter()
    for _ in range(size // MiB):
        os.write(fd, buf)
    os.fsync(fd)
    dt = time.perf_counter() - t
    os.close(fd)
    return size / MiB / dt


def _seq_read(path: str, size: int) -> float:
    buf = _aligned(MiB)
    fd = os.open(path, os.O_RDONLY | os.O_DIRECT)
    t = time.perf_counter()
    done = 0
    while done < size:
        n = os.readv(fd, [buf])
        if n <= 0:
            break
        done += n
    dt = time.perf_counter() - t
    os.close(fd)
    return done / MiB / dt


def _rand_io(path: str, size: int, secs: float, write: bool) -> float:
    buf = _aligned(BLK4K)
    if write:
        buf.write(os.urandom(BLK4K))
    nblk = size // BLK4K
    fd = os.open(path, (os.O_WRONLY if write else os.O_RDONLY) | os.O_DIRECT)
    rnd = random.Random(42)
    ops = 0
    t = time.perf_counter()
    end = t + secs
    while True:
        os.lseek(fd, rnd.randrange(nblk) * BLK4K, os.SEEK_SET)
        if write:
            os.write(fd, buf)
        else:
            os.readv(fd, [buf])
        ops += 1
        if (ops & 63) == 0 and time.perf_counter() >= end:
            break
    if write:
        os.fsync(fd)
    dt = time.perf_counter() - t
    os.close(fd)
    return ops / dt


# ---------- network ----------
class CloudflareNet:
    """Download/upload against Cloudflare's speed-test endpoints over one HTTPS connection."""

    def __init__(self, host: str = NET_HOST, timeout: float = 20.0) -> None:
        self.host, self.timeout = host, timeout
        self.headers = {"User-Agent": f"rpidash-agent/{__version__}"}

    def _conn(self) -> http.client.HTTPSConnection:
        return http.client.HTTPSConnection(self.host, timeout=self.timeout)

    def latency_ms(self) -> list[float]:
        c = self._conn()
        try:
            c.request("GET", "/__down?bytes=0", headers=self.headers)
            c.getresponse().read()  # warm-up: TCP + TLS handshake
            out = []
            for _ in range(5):
                t = time.perf_counter()
                c.request("GET", "/__down?bytes=0", headers=self.headers)
                c.getresponse().read()
                out.append((time.perf_counter() - t) * 1000)
            return out
        finally:
            c.close()

    def download(self, nbytes: int) -> float:
        c = self._conn()
        try:
            t = time.perf_counter()
            c.request("GET", f"/__down?bytes={nbytes}", headers=self.headers)
            r = c.getresponse()
            while r.read(65536):
                pass
            return time.perf_counter() - t
        finally:
            c.close()

    def upload(self, nbytes: int) -> float:
        c = self._conn()
        body = bytes(nbytes)
        try:
            t = time.perf_counter()
            c.request("POST", "/__up", body=body,
                      headers={**self.headers, "Content-Type": "application/octet-stream"})
            c.getresponse().read()
            return time.perf_counter() - t
        finally:
            c.close()


def _throughput(op, seconds: float, start: int, cap: int) -> float:
    """Repeat transfers (growing chunk size) until `seconds` have been spent; return Mbit/s."""
    total_bytes, total_t, n = 0, 0.0, start
    while total_t < seconds:
        total_t += op(n)
        total_bytes += n
        n = min(n * 2, cap)
    return total_bytes * 8 / total_t / 1e6


def net_bench(net, seconds: float = 8.0) -> dict:
    return {
        "server": NET_HOST,
        "latency_ms": round(statistics.median(net.latency_ms()), 1),
        "download_Mbps": _throughput(net.download, seconds, 1 * MiB, 50 * MiB),
        "upload_Mbps": _throughput(net.upload, seconds, 1 * MiB, 20 * MiB),
    }


# ---------- the benchmarks ----------
class Benchmarks:
    def __init__(self, binary: Path, state_dir: Path, run=subprocess.run, cores: int | None = None,
                 cpu_prefix: list[str] | None = None, net_factory=CloudflareNet) -> None:
        self.binary, self.state_dir, self.run = Path(binary), Path(state_dir), run
        self.cores = cores or os.cpu_count() or 1
        self.cpu_prefix = _default_cpu_prefix() if cpu_prefix is None else cpu_prefix
        self.net_factory = net_factory

    def _rpibench(self, test: str, threads: int, seconds: float) -> float:
        cmd = [*self.cpu_prefix, str(self.binary), test, str(threads), f"{seconds:g}"]
        r = self.run(cmd, capture_output=True, text=True, timeout=seconds * 4 + 30)
        if r.returncode != 0:
            raise BenchError(f"rpibench {test} failed: {(r.stderr or '').strip()[:200]}")
        return float(json.loads(r.stdout.strip().splitlines()[-1])["score"])

    def cpu(self, seconds: float = 5) -> dict:
        if not self.binary.exists():
            raise BenchError("rpibench not built (re-run install.sh on a Pi with a C compiler)")
        out = {}
        for test in ("int", "fp"):
            out[f"{test}_1"] = self._rpibench(test, 1, seconds)
            out[f"{test}_all"] = self._rpibench(test, self.cores, seconds)
        out["cores"] = self.cores
        cc = self.binary.with_name(self.binary.name + ".compiler")
        out["compiler"] = cc.read_text().strip() if cc.exists() else None
        return out

    def disk(self, size_mb: int = 256, secs: float = 10) -> dict:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        size = size_mb * MiB
        st = os.statvfs(self.state_dir)
        if st.f_bavail * st.f_frsize < size * 2:
            raise BenchError(f"not enough free space for a {size_mb} MB test file")
        path = str(self.state_dir / ".disk_bench.tmp")
        try:
            return {
                "size_mb": size_mb,
                "seq_write_MBps": round(_seq_write(path, size), 1),
                "seq_read_MBps": round(_seq_read(path, size), 1),
                "rand4k_read_iops": round(_rand_io(path, size, secs, False)),
                "rand4k_write_iops": round(_rand_io(path, size, secs, True)),
            }
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def net(self) -> dict:
        r = net_bench(self.net_factory())
        r["download_Mbps"] = round(r["download_Mbps"], 1)
        r["upload_Mbps"] = round(r["upload_Mbps"], 1)
        return r


class BenchJobs:
    """Runs one benchmark job at a time in a background thread."""

    def __init__(self, bench) -> None:
        self.bench = bench
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._status: dict = {"state": "idle", "job": None, "current": None, "tests": [],
                              "results": {}, "errors": {}, "started": None, "finished": None}

    def status(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._status))  # deep copy

    def start(self, tests: list[str]) -> int:
        wanted = [t for t in TESTS if t in set(tests)]
        if not tests or len(wanted) != len(set(tests)):
            raise ValueError(f"tests must be a non-empty subset of {list(TESTS)}")
        with self._lock:
            if self._status["state"] == "running":
                raise BenchError("a benchmark is already running on this Pi")
            job = next(self._ids)
            self._status = {"state": "running", "job": job, "current": wanted[0], "tests": wanted,
                            "results": {}, "errors": {}, "started": time.time(), "finished": None}
        threading.Thread(target=self._run, args=(wanted,), daemon=True).start()
        return job

    def _run(self, tests: list[str]) -> None:
        for t in tests:
            with self._lock:
                self._status["current"] = t
            try:
                res, err = getattr(self.bench, t)(), None
            except Exception as e:  # report per test; keep going with the rest
                res, err = None, str(e) or type(e).__name__
            with self._lock:
                if err is None:
                    self._status["results"][t] = res
                else:
                    self._status["errors"][t] = err
        with self._lock:
            self._status.update(state="done", current=None, finished=time.time())
