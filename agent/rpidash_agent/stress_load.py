"""Stress workloads, run as their own systemd units by rpidash_agent.stress:
  python -m rpidash_agent.stress_load ram   hold 40% of available RAM (every page touched)
  python -m rpidash_agent.stress_load net   download large files from Linux mirrors in a loop
Both run until systemd stops them (RuntimeMaxSec or the dashboard's Stop button)."""
from __future__ import annotations

import sys
import time
import urllib.request

from . import __version__

RAM_FRACTION = 0.40
CHUNK = 16 * 1024 * 1024

# Large files with stable names on package mirrors, which exist to serve big downloads.
# Rotated round-robin so no single mirror takes all the traffic.
MIRROR_URLS = [
    "https://deb.debian.org/debian/dists/stable/main/Contents-all.gz",        # ~38 MB, Debian CDN
    "https://mirrors.kernel.org/debian/dists/stable/main/Contents-all.gz",    # ~38 MB, kernel.org
    "https://ports.ubuntu.com/ubuntu-ports/dists/noble/Contents-arm64.gz",    # ~51 MB, Ubuntu ports
]
ALL_FAILED_BACKOFF_S = 30


def hold_ram() -> None:
    avail = next(int(line.split()[1]) * 1024 for line in open("/proc/meminfo") if line.startswith("MemAvailable"))
    blocks = [bytearray(b"\x01" * CHUNK) for _ in range(int(avail * RAM_FRACTION) // CHUNK)]
    print(f"holding {len(blocks) * 16} MiB", flush=True)
    while True:
        time.sleep(3600)


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
    {"ram": hold_ram, "net": pull_net}[sys.argv[1]]()
