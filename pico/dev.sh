#!/usr/bin/env bash
# Upload the Pico code via the Pi it's plugged into, then reset it and show its output.
#   ./pico/dev.sh          upload + reset + follow output (Ctrl-] to leave)
#   ./pico/dev.sh repl     just open the REPL
set -euo pipefail
PI=${PICO_HOST:-rpi-5}
MPR='~/pico/.venv/bin/mpremote'
cd "$(dirname "$0")"
if [[ ${1:-} == repl ]]; then exec ssh -t "$PI" "$MPR"; fi
[[ -f wifi_config.py ]] || { echo "Create pico/wifi_config.py from wifi_config.example.py first" >&2; exit 1; }
FILES=(*.py)
FILES=("${FILES[@]/wifi_config.example.py}")
ssh "$PI" 'mkdir -p ~/pico/src && rm -f ~/pico/src/*.py'
scp -q "${FILES[@]}" "$PI:pico/src/"
ssh "$PI" 'chmod 600 ~/pico/src/wifi_config.py'
ssh -t "$PI" "cd ~/pico/src && $MPR \$(for f in *.py; do printf 'cp %s : + ' \"\$f\"; done) reset + repl"
