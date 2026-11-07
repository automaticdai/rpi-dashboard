# Benchmarks and stress — Design

Date: 2026-11-07. Status: approved in chat by owner.

## Intent
Compare the Pis on CPU, disk and internet speed, and load them on demand to watch the
dashboard react. Results are kept so the comparison table can be revisited without re-running.

## Owner decisions
- A **Benchmarks** page: tick which Pis run this round and which tests; ticked Pis run
  **in parallel**. Results land in one big table that is saved and shown again later; a round
  only updates the Pis (and tests) it ran.
- CPU: our **own C benchmark** (no sysbench, no numpy): integer and floating point, single
  core and all cores. No AI benchmark.
- Disk: same method as the owner's `~/disk_bench.py` (O_DIRECT, 256 MB file, seq write/read
  MB/s, 4K random read/write IOPS for 10 s each).
- Network benchmark: a **public speed test** (Cloudflare speed.cloudflare.com: download,
  upload, latency).
- Stress: CPU, RAM and network loads that can be **started and stopped**. **No Pi-to-Pi
  traffic.**

## Claude's calls
- `rpibench.c` is built on each Pi by `install.sh` with the same flags
  (`-O2 -march=armv8-a -pthread`); the gcc version is recorded with results. If no compiler is
  present the CPU test reports "rpibench not built".
- INT score in Mops/s (checksum, integer matrix, prime trial division mix); FP score in MFLOPS
  (double matrix multiply + sqrt work). Each run ~5 s; working set fits in cache.
- Network stress downloads from Cloudflare in a loop. Every stress run auto-stops after its
  duration (default 5 min, max 30 min), so nothing runs forever if the dashboard goes away.
- Stress and CPU benchmarks run as transient systemd units (`systemd-run`), outside the agent's
  80 MB memory cap and nice level; Stop = `systemctl stop`. RAM stress holds 40 % of
  MemAvailable. Stress runs at nice 19 so the agent keeps answering.
- One benchmark job per Pi at a time, in an agent background thread; results are held in the
  agent until the dashboard collects them. The dashboard stores them in `bench.json` next to
  `pis.json`.
- Disk benchmark writes under the agent's `StateDirectory` (`/var/lib/rpidash-agent`, on the
  root filesystem — `/tmp` is tmpfs on Debian 13).
- Overview cards show a "Stress running" chip while a stress run is active.
