"""Client for the local TTS server (used by display/tts.py).

synthesize(text) -> raw 16-bit mono 24 kHz PCM bytes, or raises RuntimeError.
Starts the engine's server (in its own venv) on demand and waits for it.
Engine: kokoro when installed (default), else pocket; DND_TTS_ENGINE=pocket|kokoro forces one. Disable with DND_LOCAL_TTS=off.
"""
import json, os, subprocess, sys, time, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = Path.home() / ".cache" / "claude-dnd"
START_TIMEOUT = 120.0   # first start loads the model (~25 s)
SYNTH_TIMEOUT = 180.0

#: engine -> (venv folder, server script, default port)
ENGINES = {
    "pocket": ("tts-pocket", "pocket_server.py", 8766),
    "kokoro": ("tts-kokoro", "kokoro_server.py", 8767),
}


def engine() -> str:
    """The chosen engine: DND_TTS_ENGINE if set, else kokoro when it is
    installed, else pocket. Anything unknown means that same default."""
    e = os.environ.get("DND_TTS_ENGINE", "").strip().lower()
    if e in ENGINES:
        return e
    return "kokoro" if venv_python("kokoro").exists() else "pocket"


def venv_python(eng: str = "") -> Path:
    folder = ENGINES[eng or engine()][0]
    return CACHE / folder / "venv" / "Scripts" / "python.exe"


def port(eng: str = "") -> int:
    # DND_LOCAL_TTS_PORT overrides the engine's own port.
    return int(os.environ.get("DND_LOCAL_TTS_PORT") or ENGINES[eng or engine()][2])


def _base() -> str:
    return f"http://127.0.0.1:{port()}"


def enabled() -> bool:
    return os.environ.get("DND_LOCAL_TTS", "on").lower() != "off" and venv_python().exists()


def _up() -> bool:
    try:
        with urllib.request.urlopen(_base() + "/health", timeout=2) as r:
            return r.read() == b"ok"
    except Exception:
        return False


def _start() -> None:
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    env = dict(os.environ, DND_LOCAL_TTS_PORT=str(port()))
    subprocess.Popen([str(venv_python()), str(HERE / ENGINES[engine()][1])],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True, env=env)


def shutdown() -> None:
    """Ask a running voice server to exit. Does nothing if none is running."""
    if not _up():
        return
    try:
        req = urllib.request.Request(_base() + "/shutdown", data=b"", method="POST")
        urllib.request.urlopen(req, timeout=3).read()
    except Exception:
        pass


def synthesize(text: str, lang: str = "auto", speed: float = 1.0, voice: str = "") -> bytes:
    if not enabled():
        raise RuntimeError("local TTS disabled or not installed")
    if not _up():
        _start()
        end = time.time() + START_TIMEOUT
        while time.time() < end and not _up():
            time.sleep(1)
        if not _up():
            raise RuntimeError("local TTS server did not start")
    body = {"text": text, "lang": lang, "speed": speed}
    if voice:
        body["voice"] = voice
    req = urllib.request.Request(
        _base() + "/synth", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=SYNTH_TIMEOUT) as r:
            pcm = r.read()
    except Exception as e:
        raise RuntimeError(f"local TTS failed: {e}") from e
    if not pcm:
        raise RuntimeError("local TTS returned no audio")
    return pcm
