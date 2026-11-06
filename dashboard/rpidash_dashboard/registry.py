"""Known-Pis registry, persisted as pis.json."""
from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class Pi:
    id: str
    hostname: str
    host: str
    port: int
    spec: dict | None
    added: str
    last_seen: str | None
    order: int = 0  # position on the overview; set by drag-to-reorder


class Registry:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._pis: dict[str, Pi] = {}

    def load(self) -> None:
        self._pis = {}
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text())
            pis = [Pi(**d) for d in raw["pis"]]
            if not all(isinstance(p.id, str) and isinstance(p.host, str) for p in pis):
                raise ValueError("bad field types")
        except (OSError, ValueError, KeyError, TypeError) as e:
            bak = self.path.with_name(self.path.name + ".bak")
            os.replace(self.path, bak)
            log.warning("could not read %s (%s); moved it to %s and started empty", self.path, e, bak)
            return
        self._pis = {p.id: p for p in pis}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps({"pis": [asdict(p) for p in self.all()]}, indent=2)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(data)
            os.replace(tmp, self.path)
        except BaseException:
            os.unlink(tmp)
            raise

    def all(self) -> list[Pi]:
        return sorted(self._pis.values(), key=lambda p: (p.order, p.hostname, p.id))

    def get(self, id: str) -> Pi | None:
        return self._pis.get(id)

    def upsert(self, pi: Pi) -> None:
        """Add or replace a Pi. An existing Pi keeps its position; a new one goes last."""
        old = self._pis.get(pi.id)
        if old is not None:
            pi.order = old.order
        else:
            pi.order = max((p.order for p in self._pis.values()), default=-1) + 1
        self._pis[pi.id] = pi

    def reorder(self, ids: list[str]) -> None:
        """Put the given Pis first, in that order; unknown ids are ignored, unlisted Pis follow."""
        first = [self._pis[i] for i in dict.fromkeys(ids) if i in self._pis]
        rest = [p for p in self.all() if p not in first]
        for n, p in enumerate(first + rest):
            p.order = n

    def remove(self, id: str) -> bool:
        return self._pis.pop(id, None) is not None
