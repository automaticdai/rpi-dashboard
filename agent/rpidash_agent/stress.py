"""Start/stop stress loads as transient systemd units (outside the agent's memory cap),
each auto-stopped by RuntimeMaxSec so nothing runs forever."""
from __future__ import annotations

import subprocess

from .services import Runner, default_runner

UNITS = {"cpu": "rpidash-stress-cpu", "ram": "rpidash-stress-ram", "net": "rpidash-stress-net"}
MAX_MINUTES = 30


class Stress:
    def __init__(self, binary: str, python: str, cores: int, runner: Runner = default_runner) -> None:
        self.binary, self.python, self.cores, self.runner = binary, python, cores, runner

    def _command(self, kind: str) -> list[str]:
        if kind == "cpu":
            return [self.binary, "burn", str(self.cores)]
        return [self.python, "-m", "rpidash_agent.stress_load", kind]

    def start(self, cpu: bool, ram: bool, net: bool, minutes: int) -> dict:
        kinds = [k for k, on in (("cpu", cpu), ("ram", ram), ("net", net)) if on]
        if not kinds:
            raise ValueError("choose at least one of cpu, ram, net")
        if not isinstance(minutes, int) or not 1 <= minutes <= MAX_MINUTES:
            raise ValueError(f"minutes must be between 1 and {MAX_MINUTES}")
        for k in kinds:
            self.runner(["systemctl", "stop", UNITS[k]])  # restart cleanly if already running
            self.runner(["systemd-run", f"--unit={UNITS[k]}", "--collect", "--nice=19",
                         f"--property=RuntimeMaxSec={minutes * 60}", "--", *self._command(k)])
        return self.status()

    def stop(self) -> dict:
        for unit in UNITS.values():
            self.runner(["systemctl", "stop", unit])
        return self.status()

    def status(self) -> dict:
        out = {}
        for k, unit in UNITS.items():
            try:
                r = self.runner(["systemctl", "is-active", unit])
                out[k] = (r.stdout or "").strip() == "active"
            except (OSError, subprocess.SubprocessError):
                out[k] = False
        return out
