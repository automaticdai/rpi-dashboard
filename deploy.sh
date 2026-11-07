#!/usr/bin/env bash
# Install or upgrade the rpi-dashboard agent on Pis, then check each one runs this version.
#
#   ./deploy.sh                                   every Pi in pis.json (hostnames = ssh aliases)
#   ./deploy.sh rpi-cm4 rpi-5                     just these
#   ./deploy.sh --services 'cpu-performance,user:oled-stats' rpi-cm4
#                                                 allowlist for a FIRST install (kept on upgrades)
#   ./deploy.sh --rotate-token [Pis...]           give the Pis a new token; once every Pi in the
#                                                 run has it, switch the desktop token and
#                                                 dashboard.toml over (old one kept as token.old)
#
# Needs: ssh key login to each Pi (aliases in ~/.ssh/config); sudo asks for a password where
# the Pi requires one. On a first install the token (~/.config/rpidash/token) is copied as a
# file, never put on a command line. One Pi failing doesn't stop the others.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOKEN_FILE="${RPIDASH_TOKEN_FILE:-$HOME/.config/rpidash/token}"
REGISTRY="${RPIDASH_REGISTRY:-$REPO/pis.json}"
EXPECTED="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$REPO/agent/rpidash_agent/__init__.py")"

DASH_CONFIG="${RPIDASH_DASHBOARD_CONFIG:-$REPO/dashboard.toml}"

usage() { sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

services=""
rotate=0
hosts=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage 0 ;;
    --services) [[ $# -ge 2 ]] || { echo "--services needs a value" >&2; exit 2; }; services="$2"; shift 2 ;;
    --rotate-token) rotate=1; shift ;;
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

NEW_TOKEN="$TOKEN_FILE.new"
if [[ $rotate == 1 && ! -f $NEW_TOKEN ]]; then
  # A pending new token is reused, so retrying the Pis that failed doesn't make a third token.
  (umask 077; python3 -c "import secrets; print(secrets.token_urlsafe(32))" > "$NEW_TOKEN")
  echo "new token created: $NEW_TOKEN"
fi

SSH_OPTS=(-o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=3)
RETRY_DELAY="${RPIDASH_RETRY_DELAY:-2}"
# Host names may come from pis.json (reported by the Pis themselves), so only plain names pass.
valid_host() { [[ $1 =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; }

# Send a token file over ssh into a private file (never on a command line, never world-readable).
send_token() { ssh "${SSH_OPTS[@]}" -- "$1" 'umask 077; cat > ~/rpidash-agent/token' < "$2"; }

declare -A result
failed=0
for h in "${hosts[@]}"; do
  echo "=== $h"
  fail() { result[$h]="FAILED  $1"; failed=1; }
  if ! valid_host "$h"; then fail "invalid host name (letters, digits, . _ - only)"; continue; fi
  if ! ssh "${SSH_OPTS[@]}" -o BatchMode=yes -- "$h" true; then fail "unreachable"; continue; fi
  if ! rsync -a --delete --exclude tests --exclude build --exclude '*.egg-info' --exclude __pycache__ \
       -e "ssh ${SSH_OPTS[*]}" "$REPO/agent/" "$h:~/rpidash-agent/"; then fail "copy failed"; continue; fi
  rotate_env=""
  if [[ $rotate == 1 ]]; then
    send_token "$h" "$NEW_TOKEN" || { fail "could not send the new token"; continue; }
    rotate_env="RPIDASH_ROTATE=1 "
  else
    ssh "${SSH_OPTS[@]}" -- "$h" 'test -f /etc/rpidash-agent.toml'
    rc=$?
    if [[ $rc == 255 ]]; then fail "connection lost"; continue; fi
    if [[ $rc != 0 ]]; then
      echo "first install on $h: sending the token"
      [[ -f $TOKEN_FILE ]] || { fail "first install needs $TOKEN_FILE"; continue; }
      send_token "$h" "$TOKEN_FILE" || { fail "could not send the token"; continue; }
    fi
  fi
  if ! ssh "${SSH_OPTS[@]}" -t -- "$h" "cd ~/rpidash-agent && sudo ${rotate_env}RPIDASH_SERVICES='$services' ./install.sh"; then
    ssh "${SSH_OPTS[@]}" -- "$h" 'rm -f ~/rpidash-agent/token' || true  # don't leave the token behind
    fail "install.sh failed"; continue
  fi
  ver=""
  for _ in 1 2 3 4 5 6; do  # the agent may take a few seconds to start
    ver="$(ssh "${SSH_OPTS[@]}" -- "$h" "python3 -c 'import json,urllib.request; print(json.load(urllib.request.urlopen(\"http://127.0.0.1:8765/health\", timeout=10))[\"version\"])'" 2>/dev/null)" && [[ -n $ver ]] && break
    sleep "$RETRY_DELAY"
  done
  if [[ $ver == "$EXPECTED" ]]; then result[$h]="ok      $ver"; else fail "running ${ver:-nothing}, expected $EXPECTED"; fi
done

echo
printf '%-14s %s\n' "Pi" "Result"
for h in "${hosts[@]}"; do printf '%-14s %s\n' "$h" "${result[$h]}"; done

if [[ $rotate == 1 ]]; then
  echo
  if [[ $failed == 0 ]]; then
    [[ -f $TOKEN_FILE ]] && cp -p "$TOKEN_FILE" "$TOKEN_FILE.old"
    mv "$NEW_TOKEN" "$TOKEN_FILE"
    if [[ -f $DASH_CONFIG ]]; then
      python3 - "$DASH_CONFIG" "$TOKEN_FILE" <<'PY'
import re, sys
path, token = sys.argv[1], open(sys.argv[2]).read().strip()
text = open(path).read()
text, n = re.subn(r'(?m)^token\s*=\s*".*"\s*$', f'token = "{token}"', text)
open(path, "w").write(text if n else f'token = "{token}"\n' + text)
PY
      echo "Token switched: $TOKEN_FILE and $DASH_CONFIG updated (old token in $TOKEN_FILE.old)."
    else
      echo "Token switched: $TOKEN_FILE updated (old token in $TOKEN_FILE.old). Update your dashboard config's token."
    fi
    echo "Restart the dashboard so it uses the new token."
  else
    echo "Not every Pi has the new token yet; it is waiting in $NEW_TOKEN."
    echo "Fix the failed Pis and run: ./deploy.sh --rotate-token <those Pis>"
  fi
fi
exit "$failed"
