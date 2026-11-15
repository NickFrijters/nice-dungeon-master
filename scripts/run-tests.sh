#!/usr/bin/env bash
# run-tests.sh — run the whole suite against a throwaway data folder.
#
# Some tests start the display and post fake data to it. Left alone, they write
# to the real campaigns folder and the live display's state (this is how a test
# party called "Aldric" once replaced a real one). This script points the
# campaign root, the runtime folder and the voice cache at a temporary folder
# for the whole run, then deletes it.
#
#   bash scripts/run-tests.sh            # everything
#   bash scripts/run-tests.sh -v         # extra arguments go to unittest

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY=""
for cand in python3 python py; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c "import sys; sys.exit(0 if sys.version_info[0] == 3 else 1)" >/dev/null 2>&1; then
    PY="$cand"; break
  fi
done
[ -z "$PY" ] && { echo "Python 3 not found"; exit 1; }

SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT
mkdir -p "$SANDBOX/campaigns" "$SANDBOX/.runtime" "$SANDBOX/tts"
export DND_CAMPAIGN_ROOT="$SANDBOX" DND_RUNTIME_DIR="$SANDBOX/.runtime" DND_TTS_CACHE_DIR="$SANDBOX/tts"
export PYTHONUTF8=1

cd "$REPO" && "$PY" -m unittest discover -s tests "$@"
