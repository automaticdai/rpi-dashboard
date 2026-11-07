"""Saved benchmark results (bench.json, next to pis.json): the latest result of each test per Pi."""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)


class BenchStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._pis: dict[str, dict] = {}
        # Benchmark jobs started but not yet collected: Pi id -> {"job": int, "tests": [...]}.
        # Saved with the results so a dashboard restart doesn't lose a round in progress.
        self.round: dict[str, dict] = {}

    def load(self) -> None:
        self._pis, self.round = {}, {}
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
            pis = data["pis"]
            if not isinstance(pis, dict) or not all(isinstance(v, dict) for v in pis.values()):
                raise ValueError("bad shape")
            self._pis = pis
            rnd = data.get("round", {})
            if isinstance(rnd, dict):
                self.round = {k: v for k, v in rnd.items()
                              if isinstance(v, dict) and isinstance(v.get("tests"), list)}
        except (OSError, ValueError, KeyError, TypeError) as e:
            try:
                os.replace(self.path, self.path.with_name(self.path.name + ".bak"))
            except OSError:
                pass
            log.warning("could not read %s (%s); starting with no saved results", self.path, e)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump({"pis": self._pis, "round": self.round}, f, indent=2)
            os.replace(tmp, self.path)
        except BaseException:
            os.unlink(tmp)
            raise

    def results(self, pi_id: str) -> dict:
        return self._pis.get(pi_id, {})

    def record(self, pi_id: str, test: str, result: dict, at: str) -> bool:
        if not isinstance(result, dict):
            return False  # malformed answer from an agent: ignore rather than break the page
        self._pis.setdefault(pi_id, {})[test] = {**result, "measured": at}
        return True

    def record_error(self, pi_id: str, test: str, message: str, at: str) -> None:
        entry = self._pis.setdefault(pi_id, {}).setdefault(test, {})
        entry.update(error=message, error_at=at)  # keep the last good numbers, if any

    def forget(self, pi_id: str) -> None:
        self._pis.pop(pi_id, None)
