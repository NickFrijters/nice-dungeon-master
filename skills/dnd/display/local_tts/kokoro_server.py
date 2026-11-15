"""Local Kokoro-82M TTS server (ONNX): optional English narrator voice.

Run with the venv at ~/.cache/claude-dnd/tts-kokoro/venv. Binds 127.0.0.1 only.
Same API as pocket_server.py:
POST /synth  {"text": "...", "lang": "en", "speed": 1.0, "voice": "bm_george"}
             -> raw 16-bit mono PCM at 24 kHz
GET  /health -> "ok"
POST /shutdown
English only. The model loads lazily on first use and stays in memory.
Default port 8767 (DND_LOCAL_TTS_PORT overrides), so it never collides with
the Pocket server on 8766.
"""
import json, os, re, sys, threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("DND_LOCAL_TTS_PORT", "8767"))
VOICE = os.environ.get("DND_KOKORO_VOICE", "am_adam")
MODEL_DIR = os.path.join(os.path.expanduser("~"), ".cache", "claude-dnd", "tts-kokoro", "models")
#: The int8 file is the default (3.5x smaller, ~25% faster); the fp32 file is used if int8 is absent.
_INT8 = os.path.join(MODEL_DIR, "kokoro-v1.0.int8.onnx")
_FP32 = os.path.join(MODEL_DIR, "kokoro-v1.0.onnx")
MODEL = os.environ.get("DND_KOKORO_MODEL") or (_INT8 if os.path.exists(_INT8) else _FP32)
VOICES = os.environ.get("DND_KOKORO_VOICES", os.path.join(MODEL_DIR, "voices-v1.0.bin"))
MAX_CHARS = 2000
#: Sentences are synthesized one by one and joined, so a long block never
#: exceeds the model's context and a short pause falls between sentences.
PAUSE_S = 0.18

_kokoro = None
_lock = threading.Lock()


def _load():
    global _kokoro
    if _kokoro is None:
        from kokoro_onnx import Kokoro
        _kokoro = Kokoro(MODEL, VOICES)
    return _kokoro


def split_sentences(text: str):
    parts = re.split(r"(?<=[.!?…])\s+|\n+", text)
    return [p.strip() for p in parts if p and p.strip()]


def synth(text: str, speed: float = 1.0, voice: str = "") -> bytes:
    import numpy as np
    voice = voice or VOICE
    with _lock:
        k = _load()
        chunks = []
        sr = 24000
        for s in split_sentences(text):
            wav, sr = k.create(s, voice=voice, speed=speed, lang="en-gb" if voice.startswith("b") else "en-us")
            chunks.append(np.asarray(wav, dtype=np.float32))
            chunks.append(np.zeros(int(sr * PAUSE_S), dtype=np.float32))
    if not chunks:
        return b""
    wav = np.concatenate(chunks[:-1])
    return (np.clip(wav, -1, 1) * 32767).astype("<i2").tobytes()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body=b"", ctype="text/plain"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send(200, b"ok") if self.path == "/health" else self._send(404)

    def do_POST(self):
        if self.path == "/shutdown":
            self._send(200, b"bye")
            threading.Thread(target=SERVER.shutdown, daemon=True).start()
            return
        if self.path != "/synth":
            return self._send(404)
        try:
            n = min(int(self.headers.get("Content-Length", "0")), 20000)
            data = json.loads(self.rfile.read(n) or b"{}")
            text = (data.get("text") or "").strip()[:MAX_CHARS]
            if not text:
                return self._send(400, b"empty text")
            try:
                speed = float(data.get("speed", 1.0))
            except (TypeError, ValueError):
                speed = 1.0
            voice = re.sub(r"[^a-z_]", "", str(data.get("voice") or "").lower())
            pcm = synth(text, min(2.0, max(0.5, speed)), voice)
            self._send(200, pcm, "application/octet-stream")
        except Exception as e:  # never crash the server on one bad request
            self._send(500, f"{type(e).__name__}: {e}".encode()[:300])


SERVER = None

if __name__ == "__main__":
    SERVER = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print(f"kokoro server on 127.0.0.1:{PORT}", flush=True)
    SERVER.serve_forever()
