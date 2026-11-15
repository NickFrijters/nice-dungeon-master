"""Client for the local Pocket TTS server (used by display/tts.py).

synthesize(text) -> raw 16-bit mono 24 kHz PCM bytes, or raises RuntimeError.
Starts pocket_server.py (in the Pocket TTS venv) on demand and waits for it.
Disable with DND_LOCAL_TTS=off.
"""
import json, os, subprocess, sys, time, urllib.request
from pathlib import Path

PORT = int(os.environ.get("DND_LOCAL_TTS_PORT", "8766"))
BASE = f"http://127.0.0.1:{PORT}"
HERE = Path(__file__).resolve().parent
VENV_PY = Path.home() / ".cache" / "claude-dnd" / "tts-pocket" / "venv" / "Scripts" / "python.exe"
START_TIMEOUT = 120.0   # first start loads the model (~25 s)
SYNTH_TIMEOUT = 180.0


def enabled() -> bool:
    return os.environ.get("DND_LOCAL_TTS", "on").lower() != "off" and VENV_PY.exists()


def _up() -> bool:
    try:
        with urllib.request.urlopen(BASE + "/health", timeout=2) as r:
            return r.read() == b"ok"
    except Exception:
        return False


def _start() -> None:
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    subprocess.Popen([str(VENV_PY), str(HERE / "pocket_server.py")],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True)


def shutdown() -> None:
    """Ask a running voice server to exit. Does nothing if none is running."""
    if not _up():
        return
    try:
        req = urllib.request.Request(BASE + "/shutdown", data=b"", method="POST")
        urllib.request.urlopen(req, timeout=3).read()
    except Exception:
        pass


def synthesize(text: str, lang: str = "auto", speed: float = 1.0) -> bytes:
    if not enabled():
        raise RuntimeError("local TTS disabled or not installed")
    if not _up():
        _start()
        end = time.time() + START_TIMEOUT
        while time.time() < end and not _up():
            time.sleep(1)
        if not _up():
            raise RuntimeError("local TTS server did not start")
    req = urllib.request.Request(
        BASE + "/synth", data=json.dumps({"text": text, "lang": lang, "speed": speed}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=SYNTH_TIMEOUT) as r:
            pcm = r.read()
    except Exception as e:
        raise RuntimeError(f"local TTS failed: {e}") from e
    if not pcm:
        raise RuntimeError("local TTS returned no audio")
    return pcm
