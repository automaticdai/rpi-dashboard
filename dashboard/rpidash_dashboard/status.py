"""Warning flags for a Pi's latest sample."""
from __future__ import annotations

from .config import DashboardConfig


def health_warnings(sample: dict | None, cfg: DashboardConfig) -> list[str]:
    if not sample:
        return []
    out = []
    temp = sample.get("temp_c")
    if temp is not None and temp >= cfg.warn_temp_c:
        out.append("hot")
    disk = sample.get("disk_pct")
    if disk is not None and disk >= cfg.warn_disk_pct:
        out.append("disk")
    used, total = sample.get("mem_used"), sample.get("mem_total")
    if used is not None and total and used / total * 100 >= cfg.warn_mem_pct:
        out.append("mem")
    if sample.get("throttled"):
        out.append("throttled")
    return out
