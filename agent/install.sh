#!/usr/bin/env bash
# Install or upgrade the rpi-dashboard agent. Run as root from the agent/ directory.
# First install: copy the token into this directory as a file named `token` (it is deleted
# afterwards), then:
#   sudo RPIDASH_SERVICES="cpu-performance,user:oled-stats" ./install.sh
# The token is read from that file, never from the command line, so it stays out of `ps`
# and the sudo log. Safe to re-run: an existing /etc/rpidash-agent.toml is kept.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX=/opt/rpidash-agent
CONF=/etc/rpidash-agent.toml
UNIT=/etc/systemd/system/rpidash-agent.service

if [[ $EUID -ne 0 ]]; then
  echo "install.sh must run as root (use sudo)" >&2
  exit 1
fi

if [[ ! -x $PREFIX/venv/bin/python ]]; then
  mkdir -p "$PREFIX"
  if ! python3 -m venv "$PREFIX/venv" 2>/dev/null; then
    echo "python3-venv missing; installing it" >&2
    apt-get install -y python3-venv
    python3 -m venv "$PREFIX/venv"
  fi
fi
# Build from clean sources: a stale root-owned build/ dir could install old code.
rm -rf "$SRC/build" "$SRC"/*.egg-info
"$PREFIX/venv/bin/pip" install --quiet --upgrade "$SRC"
rm -rf "$SRC/build" "$SRC"/*.egg-info

# Build the CPU benchmark with the same flags on every Pi (see rpibench/rpibench.c).
mkdir -p "$PREFIX/bin"
if command -v cc >/dev/null; then
  march=""; [[ $(uname -m) == aarch64 ]] && march="-march=armv8-a"
  cc -O2 $march -pthread -o "$PREFIX/bin/rpibench" "$SRC/rpibench/rpibench.c" -lm
  cc --version | head -1 > "$PREFIX/bin/rpibench.compiler"
else
  echo "no C compiler: the CPU benchmark and CPU stress will be unavailable" >&2
fi

TOKEN_FILE="${RPIDASH_TOKEN_FILE:-$SRC/token}"
if [[ ! -f $CONF ]]; then
  if [[ ! -f $TOKEN_FILE ]]; then
    echo "first install needs the token: copy it to $TOKEN_FILE (or set RPIDASH_TOKEN_FILE)" >&2
    exit 1
  fi
  "$PREFIX/venv/bin/python" -m rpidash_agent.write_config \
    --token-file "$TOKEN_FILE" --services "${RPIDASH_SERVICES:-}" \
    --user "${RPIDASH_SERVICE_USER:-${SUDO_USER:-yfrl}}" --out "$CONF"
else
  echo "keeping existing $CONF"
fi
# Never leave a copy of the token lying around in the source directory.
[[ $TOKEN_FILE == "$SRC/token" ]] && rm -f "$TOKEN_FILE"

install -m 644 "$SRC/rpidash-agent.service" "$UNIT"
systemctl daemon-reload
systemctl enable --now rpidash-agent
systemctl restart rpidash-agent
sleep 2
systemctl --no-pager --lines=0 status rpidash-agent || true
