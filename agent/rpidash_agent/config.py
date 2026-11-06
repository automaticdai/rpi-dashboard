"""Agent configuration, read from a TOML file."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AgentConfig:
    token: str
    port: int = 8765
    interval: float = 10.0
    services_allowlist: list[str] = field(default_factory=list)
    service_user: str = "yfrl"


def load_config(path: Path) -> AgentConfig:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    token = data.get("token")
    if not isinstance(token, str) or not token.strip():
        raise ValueError(f"{path}: 'token' must be a non-empty string")
    return AgentConfig(
        token=token,
        port=int(data.get("port", 8765)),
        interval=float(data.get("interval", 10.0)),
        services_allowlist=[str(s) for s in data.get("services_allowlist", [])],
        service_user=str(data.get("service_user", "yfrl")),
    )
