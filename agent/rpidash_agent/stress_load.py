"""Stress workloads, run as their own systemd units by rpidash_agent.stress:
  python -m rpidash_agent.stress_load ram    fill 70% of available RAM and keep copying through it
  python -m rpidash_agent.stress_load disk   sequential and random 4K reads/writes that bypass the page cache
  python -m rpidash_agent.stress_load net    download large files from Linux mirrors in a loop
All run until systemd stops them (RuntimeMaxSec or the dashboard's Stop button)."""
from __future__ import annotations

import os
import random
import signal
import sys
import time
import urllib.request

from . import __version__
from .bench import MiB, _aligned

RAM_FRACTION = 0.70
CHUNK = 16 * MiB
MAX_RAM_WORKERS = 4  # a Pi's memory bus is saturated by 2-4 copying cores

DISK_FILE_MAX = 512 * MiB
DISK_FREE_FRACTION = 0.10  # never take more than this share of the free space
DISK_MIN = 16 * MiB
RAND_SECONDS = 10

# Large files with stable names on package mirrors, which exist to serve big downloads.
# Rotated round-robin so no single mirror takes all the traffic.
MIRROR_URLS = [
    "https://deb.debian.org/debian/dists/stable/main/Contents-all.gz",        # ~38 MB, Debian CDN
    "https://mirrors.kernel.org/debian/dists/stable/main/Contents-all.gz",    # ~38 MB, kernel.org
    "https://ports.ubuntu.com/ubuntu-ports/dists/noble/Contents-arm64.gz",    # ~51 MB, Ubuntu ports
]
ALL_FAILED_BACKOFF_S = 30


# ---------- RAM ----------
def ram_plan(avail: int, cores: int) -> tuple[int, int]:
    """(workers, 16 MiB blocks per worker) for holding RAM_FRACTION of `avail` bytes."""
    blocks = int(avail * RAM_FRACTION) // CHUNK
    workers = max(1, min(cores, MAX_RAM_WORKERS, blocks // 2 or 1))
    return workers, max(2, blocks // workers)


def sweep(blocks: list[bytearray], rounds: int | None = None) -> None:
    """Copy each block over the next, round and round: every pass reads and writes all of them,
    so the memory stays both resident and busy (not just allocated and idle)."""
    n, r = len(blocks), 0
    while rounds is None or r < rounds:
        for i in range(n):
            blocks[(i + 1) % n][:] = blocks[i]  # a 16 MiB memcpy, RAM to RAM
        blocks[0][0] = (blocks[0][0] + 1) & 0xFF  # change the data so no pass is a no-op
        r += 1


def hold_ram() -> None:
    avail = next(int(line.split()[1]) * 1024 for line in open("/proc/meminfo") if line.startswith("MemAvailable"))
    workers, per = ram_plan(avail, os.cpu_count() or 1)
    print(f"{workers} workers x {per * 16} MiB, copying continuously", flush=True)
    for _ in range(workers - 1):
        if os.fork() == 0:
            break  # children fall through and work too; systemd stops the whole unit
    sweep([bytearray(b"\x01" * CHUNK) for _ in range(per)])


# ---------- disk ----------
def disk_file_size(free: int) -> int:
    size = min(DISK_FILE_MAX, int(free * DISK_FREE_FRACTION)) // MiB * MiB
    if size < DISK_MIN:
        raise SystemExit(f"not enough free disk space for a stress file ({free // MiB} MiB free)")
    return size


def disk_round(path: str, size: int, rnd: random.Random, rand_seconds: float = RAND_SECONDS) -> None:
    """One round: write the file sequentially, read it back, then random 4K reads and writes.
    O_DIRECT throughout, so the device does the work rather than the page cache."""
    big = _aligned(MiB)
    big.write(os.urandom(MiB))
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_DIRECT, 0o600)
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        for _ in range(size // MiB):
            os.write(fd, big)
        os.fsync(fd)
        os.lseek(fd, 0, os.SEEK_SET)
        for _ in range(size // MiB):
            os.readv(fd, [big])
        small = _aligned(4096)
        small.write(os.urandom(4096))
        nblk, ops, end = size // 4096, 0, time.monotonic() + rand_seconds
        while time.monotonic() < end:
            os.lseek(fd, rnd.randrange(nblk) * 4096, os.SEEK_SET)
            os.write(fd, small) if ops % 4 == 0 else os.readv(fd, [small])  # 3 reads : 1 write
            ops += 1
        os.fsync(fd)
    finally:
        os.close(fd)


def churn_disk() -> None:
    d = os.environ.get("CACHE_DIRECTORY") or "/var/tmp"  # systemd's CacheDirectory= sets this
    path = os.path.join(d, "stress.tmp")
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # so the finally below removes the file
    try:
        st = os.statvfs(d)
        size = disk_file_size(st.f_bavail * st.f_frsize)
        print(f"churning a {size // MiB} MiB file in {d}", flush=True)
        rnd = random.Random()
        while True:
            disk_round(path, size, rnd)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# ---------- network ----------
def fetch(url: str) -> None:
    """Download `url` and throw the bytes away."""
    req = urllib.request.Request(url, headers={"User-Agent": f"rpidash-agent/{__version__} (stress test)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        while r.read(1 << 16):
            pass


def pull_net() -> None:
    failures_in_a_row = 0
    i = 0
    while True:
        url = MIRROR_URLS[i % len(MIRROR_URLS)]
        i += 1
        try:
            fetch(url)
            failures_in_a_row = 0
        except Exception as e:  # move on to the next mirror
            failures_in_a_row += 1
            print(f"{url}: {e}", file=sys.stderr, flush=True)
            if failures_in_a_row >= len(MIRROR_URLS):  # every mirror failed: probably offline
                time.sleep(ALL_FAILED_BACKOFF_S)
                failures_in_a_row = 0


if __name__ == "__main__":
    {"ram": hold_ram, "disk": churn_disk, "net": pull_net}[sys.argv[1]]()
