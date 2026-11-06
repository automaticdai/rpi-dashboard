"""Allowlisted systemd service control. Commands are argument lists, never a shell."""
from __future__ import annotations

import subprocess
from typing import Callable

ACTIONS = frozenset({"start", "stop", "restart"})

Runner = Callable[[list[str]], subprocess.CompletedProcess]


def default_runner(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


class Services:
    def __init__(self, allowlist: list[str], user: str = "yfrl", runner: Runner = default_runner) -> None:
        self.allowlist = list(allowlist)
        self.user = user
        self.runner = runner

    def allowed(self, entry: str) -> bool:
        return entry in self.allowlist

    def _base(self, entry: str, verb: str) -> list[str]:
        if entry.startswith("user:"):
            return ["systemctl", "--user", "-M", f"{self.user}@", verb, entry[len("user:"):]]
        return ["systemctl", verb, entry]

    def command(self, entry: str, action: str) -> list[str]:
        if not self.allowed(entry):
            raise PermissionError(f"service not allowlisted: {entry!r}")
        if action not in ACTIONS:
            raise PermissionError(f"action not allowed: {action!r}")
        return self._base(entry, action)

    def run(self, entry: str, action: str) -> tuple[bool, str]:
        cmd = self.command(entry, action)
        try:
            r = self.runner(cmd)
        except (OSError, subprocess.SubprocessError) as e:
            return False, str(e)
        return r.returncode == 0, ((r.stdout or "") + (r.stderr or "")).strip()

    def states(self) -> list[dict]:
        out = []
        for entry in self.allowlist:
            try:
                r = self.runner(self._base(entry, "is-active"))
                state = (r.stdout or "").strip() or "unknown"
            except (OSError, subprocess.SubprocessError):
                state = "unknown"
            out.append({"name": entry, "state": state})
        return out

    def reboot(self) -> None:
        self.runner(["systemctl", "reboot"])
