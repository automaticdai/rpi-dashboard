"""Stress workloads, run as their own systemd units by rpidash_agent.stress:
  python -m rpidash_agent.stress_load ram   hold 40% of available RAM (every page touched)
  python -m rpidash_agent.stress_load net   download from Cloudflare's speed test in a loop
Both run until systemd stops them (RuntimeMaxSec or the dashboard's Stop button)."""
from __future__ import annotations

import sys
import time

from .bench import NET_MAX_BYTES, CloudflareNet

RAM_FRACTION = 0.40
CHUNK = 16 * 1024 * 1024


def hold_ram() -> None:
    avail = next(int(line.split()[1]) * 1024 for line in open("/proc/meminfo") if line.startswith("MemAvailable"))
    blocks = [bytearray(b"\x01" * CHUNK) for _ in range(int(avail * RAM_FRACTION) // CHUNK)]
    print(f"holding {len(blocks) * 16} MiB", flush=True)
    while True:
        time.sleep(3600)


def pull_net() -> None:
    net = CloudflareNet()
    while True:
        try:
            net.download(NET_MAX_BYTES)
        except Exception:  # network error or refused request: back off instead of spinning
            time.sleep(5)


if __name__ == "__main__":
    {"ram": hold_ram, "net": pull_net}[sys.argv[1]]()
