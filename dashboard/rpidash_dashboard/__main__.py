"""Entrypoint: python -m rpidash_dashboard --config dashboard.toml"""
from __future__ import annotations

import argparse
import platform
from pathlib import Path

import uvicorn

from .app import create_app
from .config import load_config


def is_wsl() -> bool:
    """True under WSL2 (the kernel release carries "microsoft" / "WSL")."""
    release = platform.release().lower()
    return "microsoft" in release or "wsl" in release


def resolve_host(host: str) -> str:
    """Widen the loopback default to 0.0.0.0 under WSL2.

    WSL2's localhost forwarding cannot reach a 127.0.0.1 bind inside the VM, so a
    Windows browser would get nothing; binding 0.0.0.0 makes http://localhost work
    from Windows. WSL2 (NAT mode) is not reachable from the LAN, so this stays
    host-local, and TrustedHostMiddleware still checks the Host header.
    """
    if is_wsl() and host == "127.0.0.1":
        return "0.0.0.0"
    return host


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="rpidash_dashboard")
    p.add_argument("--config", type=Path, default=Path("dashboard.toml"))
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    args = p.parse_args(argv)
    uvicorn.run(create_app(load_config(args.config)), host=resolve_host(args.host), port=args.port)


if __name__ == "__main__":
    main()
