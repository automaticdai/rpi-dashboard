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
        return sorted(self._pis.values(), key=lambda p: (p.hostname, p.id))

    def get(self, id: str) -> Pi | None:
        return self._pis.get(id)

    def upsert(self, pi: Pi) -> None:
        self._pis[pi.id] = pi

    def remove(self, id: str) -> bool:
        return self._pis.pop(id, None) is not None
