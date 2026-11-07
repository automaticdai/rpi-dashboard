#!/usr/bin/env bash
# Install or upgrade the rpi-dashboard agent on Pis, then check each one runs this version.
#
#   ./deploy.sh                                   every Pi in pis.json (hostnames = ssh aliases)
#   ./deploy.sh rpi-cm4 rpi-5                     just these
#   ./deploy.sh --services 'cpu-performance,user:oled-stats' rpi-cm4
#                                                 allowlist for a FIRST install (kept on upgrades)
#
# Needs: ssh key login to each Pi (aliases in ~/.ssh/config); sudo asks for a password where
# the Pi requires one. On a first install the token (~/.config/rpidash/token) is copied as a
# file, never put on a command line. One Pi failing doesn't stop the others.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOKEN_FILE="${RPIDASH_TOKEN_FILE:-$HOME/.config/rpidash/token}"
REGISTRY="${RPIDASH_REGISTRY:-$REPO/pis.json}"
EXPECTED="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$REPO/agent/rpidash_agent/__init__.py")"

usage() { sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

services=""
hosts=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage 0 ;;
    --services) services="${2-}"; shift 2 ;;
    -*) echo "unknown option: $1" >&2; usage 2 ;;
    *) hosts+=("$1"); shift ;;
  esac
done

if [[ ! $services =~ ^[A-Za-z0-9@._:,-]*$ ]]; then
  echo "--services: only unit names, user:<unit> and commas are allowed" >&2
  exit 2
fi
if [[ ${#hosts[@]} -eq 0 ]]; then
  if [[ ! -f $REGISTRY ]]; then echo "no Pis given and no $REGISTRY to read them from" >&2; exit 2; fi
  mapfile -t hosts < <(python3 -c 'import json,sys; [print(p["hostname"]) for p in json.load(open(sys.argv[1]))["pis"]]' "$REGISTRY")
  [[ ${#hosts[@]} -gt 0 ]] || { echo "$REGISTRY lists no Pis" >&2; exit 2; }
fi

declare -A result
failed=0
for h in "${hosts[@]}"; do
  echo "=== $h"
  fail() { result[$h]="FAILED  $1"; failed=1; }
  if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "$h" true; then fail "unreachable"; continue; fi
  if ! rsync -a --delete --exclude tests --exclude build --exclude '*.egg-info' --exclude __pycache__ \
       "$REPO/agent/" "$h:~/rpidash-agent/"; then fail "copy failed"; continue; fi
  if ! ssh "$h" 'test -f /etc/rpidash-agent.toml'; then
    echo "first install on $h: sending the token"
    [[ -f $TOKEN_FILE ]] || { fail "first install needs $TOKEN_FILE"; continue; }
    scp -p "$TOKEN_FILE" "$h:~/rpidash-agent/token" || { fail "could not send the token"; continue; }
  fi
  if ! ssh -t "$h" "cd ~/rpidash-agent && sudo RPIDASH_SERVICES='$services' ./install.sh"; then
    fail "install.sh failed"; continue
  fi
  ver="$(ssh "$h" "python3 -c 'import json,urllib.request; print(json.load(urllib.request.urlopen(\"http://127.0.0.1:8765/health\", timeout=10))[\"version\"])'" 2>/dev/null)"
  if [[ $ver == "$EXPECTED" ]]; then result[$h]="ok      $ver"; else fail "running ${ver:-nothing}, expected $EXPECTED"; fi
done

echo
printf '%-14s %s\n' "Pi" "Result"
for h in "${hosts[@]}"; do printf '%-14s %s\n' "$h" "${result[$h]}"; done
exit "$failed"
