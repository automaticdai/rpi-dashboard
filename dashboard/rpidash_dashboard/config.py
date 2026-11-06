"""Dashboard configuration, read from a TOML file."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass
class DashboardConfig:
    token: str
    registry_path: Path
    subnet: str = "192.168.0.0/24"
    agent_port: int = 8765
    warn_temp_c: float = 70
    warn_disk_pct: float = 90
    warn_mem_pct: float = 90


def load_config(path: Path) -> DashboardConfig:
    path = Path(path)
    with open(path, "rb") as f:
        data = tomllib.load(f)
    token = data.get("token")
    if not isinstance(token, str) or not token.strip():
        raise ValueError(f"{path}: 'token' must be a non-empty string")
    reg = data.get("registry_path")
    return DashboardConfig(
        token=token,
        registry_path=Path(reg) if reg else path.parent / "pis.json",
        subnet=str(data.get("subnet", "192.168.0.0/24")),
        agent_port=int(data.get("agent_port", 8765)),
        warn_temp_c=float(data.get("warn_temp_c", 70)),
        warn_disk_pct=float(data.get("warn_disk_pct", 90)),
        warn_mem_pct=float(data.get("warn_mem_pct", 90)),
    )
