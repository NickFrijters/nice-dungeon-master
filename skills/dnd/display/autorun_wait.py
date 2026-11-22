#!/usr/bin/env python3
"""Pure-python autorun wait — TCC-safe replacement for autorun-wait.sh.

macOS TCC blocks shell-level file creation in ~/Documents, but python file
writes are permitted. autorun-wait.sh fails on its `echo > .autorun-session`
redirect; this script does the identical job (session-invalidation, countdown
broadcast, input-queue poll, /queue/consumed POST) using python I/O only.

Prints the queued player action(s) to stdout, or nothing on timeout (9 min).
"""
import os
import sys
import ssl
import time
import json
import secrets
import subprocess
import urllib.request

DISPLAY_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(DISPLAY_DIR, "..", "scripts"))
PUSH = os.path.join(DISPLAY_DIR, "push_stats.py")

try:
    from paths import runtime_dir, find_campaign
    RT = str(runtime_dir())
except Exception:
    RT = os.path.join(os.environ.get("DND_CAMPAIGN_ROOT", os.path.expanduser("~/.claude/dnd")), ".runtime")
os.makedirs(RT, exist_ok=True)

QFILE = os.path.join(RT, ".input_queue")
SESSION_FILE = os.path.join(RT, ".autorun-session")
# Touched every few seconds while a Claude session is waiting for input, so the
# Main Menu can tell whether anyone is there to answer it.
HEARTBEAT_FILE = os.path.join(RT, ".dm-heartbeat")


def _beat():
    try:
        with open(HEARTBEAT_FILE, "w", encoding="utf-8") as f:
            f.write(str(time.time()))
    except OSError:
        pass


_beat()

# Invalidate any previous wait loop by writing a new session id (python write — TCC-ok)
my_session = secrets.token_hex(8)
with open(SESSION_FILE, "w", encoding="utf-8") as f:
    f.write(my_session)

# Resolve autorun interval from the active campaign's state.md (default 60s)
interval = 60
try:
    import re
    camp = open(os.path.join(RT, ".campaign"), encoding="utf-8").read().strip()
    txt = (find_campaign(camp) / "state.md").read_text(encoding="utf-8", errors="replace")
    m = re.search(r"autorun_interval:\s*(\d+)", txt)
    if m:
        interval = int(m.group(1))
except Exception:
    pass

def _post(path, payload):
    """Tell the display directly (one quick HTTP call, no extra Python process: that cost ~0.2-2 s per call)."""
    try:
        scheme_file = os.path.join(DISPLAY_DIR, ".scheme")
        scheme = open(scheme_file, encoding="utf-8").read().strip() if os.path.exists(scheme_file) else "http"
        token = ""
        try:
            token = open(os.path.join(RT, ".token"), encoding="utf-8").read().strip()
        except OSError:
            pass
        ctx = None
        if scheme == "https":
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(f"{scheme}://127.0.0.1:{os.environ.get('DND_DISPLAY_PORT', '5001')}{path}",
                                     data=json.dumps(payload).encode() if payload is not None else b"", method="POST",
                                     headers={"X-DND-Token": token, "Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=2, context=ctx)
    except Exception:
        pass


_post("/stats", {"autorun_waiting": True, "autorun_cycle": {"interval": interval, "ts": time.time()}})

# Poll loop — exit when queue appears, session changes, or 9 minutes pass
content = ""
for _i in range(5400):  # 0.1s * 5400 = 9 min: a player's GO is picked up within a tenth of a second
    if _i % 30 == 0:
        _beat()
    if os.path.exists(QFILE):
        try:
            # Claim the file first (atomic rename), then read: a line the display appends
            # in between goes to a fresh .input_queue instead of being deleted unread.
            _taken = QFILE + ".taking"
            os.replace(QFILE, _taken)
            content = open(_taken, encoding="utf-8").read()
            os.unlink(_taken)
        except Exception:
            # The display is still writing it (on Windows an open file cannot be renamed): try again in a moment
            # instead of giving the DM an empty turn.
            content = ""
            time.sleep(0.02)
            continue
        if content.strip():
            break
    try:
        if open(SESSION_FILE, encoding="utf-8").read().strip() != my_session:
            break
    except Exception:
        break
    time.sleep(0.1)

# Clean up our session file if it's still ours
try:
    if open(SESSION_FILE, encoding="utf-8").read().strip() == my_session:
        os.unlink(SESSION_FILE)
except Exception:
    pass

_post("/stats", {"autorun_waiting": False})
if content:
    _post("/queue/consumed", None)          # clear the display's queue indicator

if content.strip():
    try:
        import settings
        camp = None
        try:
            camp = open(os.path.join(RT, ".campaign"), encoding="utf-8").read().strip() or None
        except OSError:
            pass
        sys.stdout.write("\n".join(settings.directive_lines(camp)) + "\n")
    except Exception:
        pass
sys.stdout.write(content)
