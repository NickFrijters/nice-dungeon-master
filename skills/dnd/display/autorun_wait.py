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
# What the last wait handed to the DM, until the DM answers on screen (the display deletes it then). If a wait
# hands input over and the DM never acts on it (a wait that ran in the background, a crash), the next wait gives
# it back once instead of the players' turn or a Main Menu request being lost.
HANDED_FILE = os.path.join(RT, ".input_handed")


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

content = ""
_redelivered = False
try:
    _h = json.load(open(HANDED_FILE, encoding="utf-8"))
    if _h.get("content", "").strip() and not _h.get("again") and time.time() - float(_h.get("ts", 0)) < 900:
        content = _h["content"]
        _redelivered = True
        _h["again"] = True                  # give it back only once, so a request can never loop
        with open(HANDED_FILE, "w", encoding="utf-8") as f:
            json.dump(_h, f)
    else:
        os.remove(HANDED_FILE)
except (OSError, ValueError):
    pass

# Poll loop — exit when queue appears, session changes, or 9 minutes pass
for _i in (range(0) if _redelivered else range(5400)):  # 0.1 s * 5400 = 9 min: a GO is picked up within a tenth of a second
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

if content.strip() and not _redelivered:
    try:
        with open(HANDED_FILE, "w", encoding="utf-8") as f:
            json.dump({"content": content, "ts": time.time()}, f)
    except OSError:
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
if _redelivered:
    sys.stdout.write("[[This input was handed to you before but never answered on screen (the earlier wait may have run in "
                     "the background). Act on it now.]]\n")
sys.stdout.write(content)
