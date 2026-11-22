#!/usr/bin/env bash
# dev-restart.sh — run the display from this working copy on port 5001.
#
# Stops whatever display is running (the installed plugin's or an earlier run of
# this script), starts the one in this repo in LAN mode on the Main Menu, and
# waits until it answers. Run it after every change you want to try.
#
#   bash scripts/dev-restart.sh
#
# Note: /dm:dnd's own launcher also replaces the running display, with the
# installed release; run this again afterwards to get the working copy back.

REPO="$(cd "$(dirname "$0")/.." && pwd)"
DISPLAY_DIR="$REPO/skills/dnd/display"
export PYTHONUTF8=1

# Find a Python that actually runs (on Windows "python3" can be a Store placeholder)
PY=""
for cand in python3 python py; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c "import sys; sys.exit(0 if sys.version_info[0] == 3 else 1)" >/dev/null 2>&1; then
    PY="$cand"; break
  fi
done
[ -z "$PY" ] && { echo "Python 3 not found"; exit 1; }

# Stop every display copy. On Windows pkill does not reach python.exe, so ask PowerShell.
if command -v powershell.exe >/dev/null 2>&1; then
  powershell.exe -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { \$_.CommandLine -like '*dnd-display-app*' } | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force }" >/dev/null 2>&1
else
  pkill -f "dnd-display-app" 2>/dev/null
fi
sleep 1

cd "$DISPLAY_DIR" || exit 1
nohup "$PY" dnd-display-app.py --lan --menu > "$DISPLAY_DIR/app.log" 2>&1 &
echo $! > "$DISPLAY_DIR/app.pid"

for i in $(seq 1 30); do
  sleep 0.5
  if curl -s -m 2 http://127.0.0.1:5001/ping >/dev/null 2>&1; then
    IP=$("$PY" -c "import socket;s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.connect(('8.8.8.8',80));print(s.getsockname()[0])" 2>/dev/null)
    echo "Display from this working copy is up: http://localhost:5001   (TV: http://${IP}:5001)"
    exit 0
  fi
done
echo "The display did not start; see $DISPLAY_DIR/app.log"
exit 1
