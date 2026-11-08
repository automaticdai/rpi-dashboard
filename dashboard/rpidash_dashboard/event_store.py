"""Event log (events.json, next to pis.json): Pis going offline and coming back, and actions
taken from the dashboard. Only recorded while the dashboard runs."""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)
MAX_EVENTS = 5000


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class EventStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.events: list[dict] = []  # oldest first

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text())
            evs = data["events"]
            self.events = [e for e in evs if isinstance(e, dict) and "pi" in e and "kind" in e and "at" in e]
        except FileNotFoundError:
            self.events = []
        except (OSError, ValueError, KeyError, TypeError) as e:
            log.warning("could not read %s (%s); starting a new event log", self.path, e)
            self.events = []

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name, suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                json.dump({"events": self.events[-MAX_EVENTS:]}, f)
            os.replace(tmp, self.path)
        except OSError as e:
            log.warning("could not save %s: %s", self.path, e)

    def add(self, pi: str, kind: str, **details) -> dict:
        e = {"pi": pi, "kind": kind, "at": now_iso(), **details}
        self.events.append(e)
        del self.events[:-MAX_EVENTS]
        self.save()
        return e

    def last_status(self, pi: str) -> dict | None:
        """The latest offline/online event for a Pi (survives dashboard restarts)."""
        return next((e for e in reversed(self.events) if e["pi"] == pi and e["kind"] in ("offline", "online")), None)

    def query(self, pi: str | None = None, days: float = 7) -> list[dict]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        return [e for e in reversed(self.events) if e["at"] >= cutoff and (pi is None or e["pi"] == pi)]

    @staticmethod
    def summary(events: list[dict]) -> dict:
        out: dict[str, dict] = {}
        for e in events:
            s = out.setdefault(e["pi"], {"dropouts": 0, "reboots": 0, "longest_outage_s": 0, "downtime_s": 0})
            if e["kind"] == "online":
                s["dropouts" if e.get("cause") == "network" else "reboots"] += 1
                s["longest_outage_s"] = max(s["longest_outage_s"], e.get("down_s") or 0)
                s["downtime_s"] += e.get("down_s") or 0
        return out
