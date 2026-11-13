"""Start/stop stress loads as transient systemd units (outside the agent's memory cap),
each auto-stopped by RuntimeMaxSec so nothing runs forever."""
from __future__ import annotations

import subprocess

from .services import Runner, default_runner

UNITS = {"cpu": "rpidash-stress-cpu", "ram": "rpidash-stress-ram", "disk": "rpidash-stress-disk",
         "net": "rpidash-stress-net"}
MAX_MINUTES = 30
# The loads need no privileges: run them as a throwaway user in a read-only system.
SANDBOX = ["--property=DynamicUser=yes", "--property=ProtectSystem=strict", "--property=ProtectHome=yes",
           "--property=NoNewPrivileges=yes", "--property=PrivateTmp=yes"]
EXTRA = {
    # If memory runs out, the kernel kills this load before anything that matters.
    "ram": ["--property=OOMScoreAdjust=1000"],
    # The one writable place in the read-only sandbox: /var/cache/rpidash-stress (CACHE_DIRECTORY).
    "disk": ["--property=CacheDirectory=rpidash-stress"],
}


class StressError(Exception):
    """A load could not be started (message says which and why)."""


class Stress:
    def __init__(self, binary: str, python: str, cores: int, runner: Runner = default_runner) -> None:
        self.binary, self.python, self.cores, self.runner = binary, python, cores, runner

    def _command(self, kind: str) -> list[str]:
        if kind == "cpu":
            return [self.binary, "burn", str(self.cores)]
        return [self.python, "-m", "rpidash_agent.stress_load", kind]

    def start(self, cpu: bool, ram: bool, net: bool, minutes: int, disk: bool = False) -> dict:
        kinds = [k for k, on in (("cpu", cpu), ("ram", ram), ("disk", disk), ("net", net)) if on]
        if not kinds:
            raise ValueError("choose at least one of cpu, ram, disk, net")
        if not isinstance(minutes, int) or not 1 <= minutes <= MAX_MINUTES:
            raise ValueError(f"minutes must be between 1 and {MAX_MINUTES}")
        errors = []
        for k in kinds:
            try:
                self.runner(["systemctl", "stop", UNITS[k]])  # restart cleanly if already running
                r = self.runner(["systemd-run", f"--unit={UNITS[k]}", "--collect", "--nice=19",
                                 f"--property=RuntimeMaxSec={minutes * 60}", *SANDBOX, *EXTRA.get(k, []), "--", *self._command(k)])
            except (OSError, subprocess.SubprocessError) as e:
                errors.append(f"{k}: {e}")
                continue
            if r.returncode != 0:
                errors.append(f"{k}: {((r.stderr or '') + (r.stdout or '')).strip()[:200] or 'systemd-run failed'}")
        if errors:
            raise StressError("; ".join(errors))
        return self.status()

    def stop(self) -> dict:
        for unit in UNITS.values():
            try:
                self.runner(["systemctl", "stop", unit])
            except (OSError, subprocess.SubprocessError):
                pass
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
