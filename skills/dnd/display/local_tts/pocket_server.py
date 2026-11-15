"""Local Pocket TTS server (Kyutai): the narrator voice.

Run with the venv at ~/.cache/claude-dnd/tts-pocket/venv. Binds 127.0.0.1 only.
POST /synth  {"text": "...", "lang": "nl"|"en"|"auto"}  ->  raw 16-bit mono PCM at 24 kHz
GET  /health -> "ok"
Models load lazily on first use and stay in memory.
"""
import json, os, re, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import torch
from pocket_tts import TTSModel

PORT = int(os.environ.get("DND_LOCAL_TTS_PORT", "8766"))
VOICE = os.environ.get("DND_LOCAL_TTS_VOICE", "alba")
LANGS = {"nl": "dutch", "en": "english"}
TARGET_SR = 24000
MAX_CHARS = 2000

torch.set_num_threads(max(1, (os.cpu_count() or 8)))
_models = {}
_lock = threading.Lock()

_NL_WORDS = {"de", "het", "een", "en", "van", "dat", "is", "niet", "op", "te", "zijn", "met",
             "voor", "je", "ik", "jij", "hij", "zij", "ze", "maar", "als", "naar", "ook",
             "door", "die", "dit", "was", "aan", "om", "bij", "uit", "over", "nog", "wel"}
_EN_WORDS = {"the", "and", "of", "to", "is", "in", "that", "it", "you", "with", "for", "was",
             "on", "as", "his", "her", "they", "this", "are", "but", "not", "from", "or", "be"}


def detect_lang(text: str) -> str:
    words = re.findall(r"[a-zà-ÿ']+", text.lower())
    nl = sum(w in _NL_WORDS for w in words)
    en = sum(w in _EN_WORDS for w in words)
    return "nl" if nl > en else "en"


def get(lang: str):
    if lang not in _models:
        m = TTSModel.load_model(language=LANGS[lang])
        _models[lang] = (m, m.get_state_for_audio_prompt(VOICE))
    return _models[lang]


def synth(text: str, lang: str) -> bytes:
    if lang not in LANGS:
        lang = detect_lang(text)
    with _lock:
        m, voice = get(lang)
        wav = m.generate_audio(voice, text).squeeze().cpu().numpy().astype(np.float32)
        sr = int(m.sample_rate)
    if sr != TARGET_SR:
        n = int(len(wav) * TARGET_SR / sr)
        wav = np.interp(np.linspace(0, len(wav) - 1, n), np.arange(len(wav)), wav)
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
            pcm = synth(text, (data.get("lang") or "auto").lower())
            self._send(200, pcm, "application/octet-stream")
        except Exception as e:  # never crash the server on one bad request
            self._send(500, f"{type(e).__name__}: {e}".encode()[:300])


SERVER = None

if __name__ == "__main__":
    SERVER = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print(f"pocket server on 127.0.0.1:{PORT}", flush=True)
    SERVER.serve_forever()
