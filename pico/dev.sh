#!/usr/bin/env bash
# Upload the Pico code via the Pi it's plugged into, then reset it and show its output.
#   ./pico/dev.sh          upload + reset + follow output (Ctrl-] to leave)
#   ./pico/dev.sh repl     just open the REPL
# Note: connecting with mpremote stops the program running on the Pico; reset to start it again.
set -euo pipefail
PI=${PICO_HOST:-rpi-5}
TOKEN=${RPIDASH_TOKEN_FILE:-$HOME/.config/rpidash/token}
MPR='~/pico/.venv/bin/mpremote'
cd "$(dirname "$0")"
if [[ ${1:-} == repl ]]; then exec ssh -t "$PI" "$MPR"; fi
[[ -f wifi_config.py ]] || { echo "Create pico/wifi_config.py from wifi_config.example.py first" >&2; exit 1; }
[[ -f $TOKEN ]] || { echo "No dashboard token at $TOKEN" >&2; exit 1; }
FILES=()
for f in *.py; do [[ $f == *.example.py ]] || FILES+=("$f"); done
ssh "$PI" 'mkdir -p ~/pico/src && rm -f ~/pico/src/*.py ~/pico/src/token.txt && umask 077 && touch ~/pico/src/token.txt'
scp -q "${FILES[@]}" "$PI:pico/src/"
scp -q "$TOKEN" "$PI:pico/src/token.txt"
ssh "$PI" 'chmod 600 ~/pico/src/wifi_config.py ~/pico/src/token.txt'
ssh -t "$PI" "cd ~/pico/src && $MPR \$(for f in *.py token.txt; do printf 'cp %s : + ' \"\$f\"; done) reset + repl"
