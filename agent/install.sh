#!/usr/bin/env bash
# Install or upgrade the rpi-dashboard agent. Run as root from the agent/ directory:
#   sudo RPIDASH_TOKEN=... RPIDASH_SERVICES="cpu-performance,user:oled-stats" ./install.sh
# Safe to re-run: the config is only written if it does not exist yet.
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

if [[ ! -f $CONF ]]; then
  : "${RPIDASH_TOKEN:?set RPIDASH_TOKEN for the first install}"
  services=""
  IFS=',' read -ra items <<< "${RPIDASH_SERVICES:-}"
  for s in "${items[@]}"; do
    s="$(echo "$s" | xargs)"
    [[ -n $s ]] && services+="${services:+, }\"$s\""
  done
  umask 077
  cat > "$CONF" <<CONF
token = "$RPIDASH_TOKEN"
port = 8765
interval = 10
services_allowlist = [$services]
service_user = "${RPIDASH_SERVICE_USER:-${SUDO_USER:-yfrl}}"
CONF
  chmod 600 "$CONF"
  echo "wrote $CONF"
else
  echo "keeping existing $CONF"
fi

install -m 644 "$SRC/rpidash-agent.service" "$UNIT"
systemctl daemon-reload
systemctl enable --now rpidash-agent
systemctl restart rpidash-agent
sleep 2
systemctl --no-pager --lines=0 status rpidash-agent || true
