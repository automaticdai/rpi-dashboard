"""Dashboard configuration, read from a TOML file."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
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
    # Host names the browser may use to reach the dashboard (blocks DNS-rebinding attacks).
    allowed_hosts: list[str] = field(default_factory=lambda: ["localhost", "127.0.0.1"])


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
        allowed_hosts=[str(h) for h in data.get("allowed_hosts", ["localhost", "127.0.0.1"])],
    )
