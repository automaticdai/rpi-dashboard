"""Entrypoint: python -m rpidash_agent --config /etc/rpidash-agent.toml"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import logging
import os
import sys
import threading
import time
from pathlib import Path

from .bench import BenchJobs, Benchmarks
from .collect import Collector
from .config import load_config
from .history import History
from .server import make_server
from .services import Services
from .stress import Stress

log = logging.getLogger("rpidash_agent")


def _sampler(collector: Collector, history: History, interval: float, stop: threading.Event) -> None:
    next_t = time.monotonic()
    while not stop.is_set():
        try:
            history.append(collector.sample(time.time()))
        except Exception:  # keep sampling no matter what
            log.exception("sample failed")
        next_t += interval
        stop.wait(max(0.0, next_t - time.monotonic()))


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="rpidash_agent")
    p.add_argument("--config", type=Path, default=Path("/etc/rpidash-agent.toml"))
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = load_config(args.config)
    history = History()
    services = Services(cfg.services_allowlist, user=cfg.service_user)
    stop = threading.Event()
    threading.Thread(target=_sampler, args=(Collector(), history, cfg.interval, stop), daemon=True).start()
    jobs = BenchJobs(Benchmarks(binary=cfg.bench_binary, state_dir=cfg.state_dir))
    stress = Stress(binary=str(cfg.bench_binary), python=sys.executable, cores=os.cpu_count() or 1)
    # A second collector for on-demand readings (the Stress tab's live charts), so its
    # network-rate bookkeeping doesn't disturb the 10 s history samples.
    live_collector, live_lock = Collector(), threading.Lock()

    def now_sample() -> dict:
        with live_lock:
            return asdict(live_collector.sample(time.time()))

    srv = make_server(cfg, history, services, jobs=jobs, stress=stress, now_fn=now_sample)
    log.info("rpidash-agent listening on :%d", cfg.port)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        srv.server_close()


if __name__ == "__main__":
    main()
