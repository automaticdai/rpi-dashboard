"""Write /etc/rpidash-agent.toml safely (used by install.sh).

The token is read from a file, never from the command line, so it doesn't show up in `ps`
or the sudo log. Every value is validated, so nothing can break or inject into the TOML.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import tomllib
from pathlib import Path

TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-.~+/=]{16,}$")
SERVICE_RE = re.compile(r"^(user:)?[A-Za-z0-9@._-]+$")
USER_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")


def _fail(msg: str) -> None:
    sys.exit(f"write_config: {msg}")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="rpidash_agent.write_config")
    p.add_argument("--token-file", type=Path, required=True)
    p.add_argument("--services", default="", help="comma-separated allowlist")
    p.add_argument("--user", default="yfrl", help="owner of user:<unit> services")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--update-token", action="store_true",
                   help="replace only the token in an existing config, keeping everything else")
    args = p.parse_args(argv)

    token = args.token_file.read_text().strip()
    if not TOKEN_RE.match(token):
        _fail("token must be at least 16 characters of letters, digits or -_.~+/=")
    if args.update_token:
        _update_token(args.out, token)
        return
    services = [s.strip() for s in args.services.split(",") if s.strip()]
    for s in services:
        if not SERVICE_RE.match(s):
            _fail(f"invalid service name {s!r} (use a unit name or user:<unit>)")
    if not USER_RE.match(args.user):
        _fail(f"invalid user name {args.user!r}")
    if args.out.exists():
        _fail(f"{args.out} already exists; not overwriting it")

    # json.dumps of these validated values is also a valid TOML basic string / array.
    text = (f"token = {json.dumps(token)}\nport = 8765\ninterval = 10\n"
            f"services_allowlist = {json.dumps(services)}\nservice_user = {json.dumps(args.user)}\n")
    fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    print(f"wrote {args.out}")


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str) or (isinstance(v, list) and all(isinstance(x, str) for x in v)):
        return json.dumps(v)  # a valid TOML basic string / array of strings
    _fail(f"can't rewrite config value {v!r}")


def _update_token(out: Path, token: str) -> None:
    if not out.exists():
        _fail(f"{out} doesn't exist; nothing to update")
    with open(out, "rb") as f:
        data = tomllib.load(f)
    data["token"] = token
    text = "".join(f"{k} = {_toml_value(v)}\n" for k, v in data.items())
    fd, tmp = tempfile.mkstemp(dir=out.parent, prefix=".rpidash-agent.", suffix=".toml")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, out)
    except BaseException:
        os.unlink(tmp)
        raise
    print(f"updated the token in {out}")


if __name__ == "__main__":
    main()
