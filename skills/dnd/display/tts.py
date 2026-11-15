"""Narrator voice: local Pocket TTS (Kyutai) only. No remote service.

Server-side wrapper called by /tts in dnd-display-app.py. The model runs in
its own process (local_tts/pocket_server.py, in the Pocket TTS venv under
~/.cache/claude-dnd/tts-pocket/venv) that starts on demand and stays loaded.
Dutch and English are picked from the text itself.

Returns PCM bytes (16-bit mono, 24 kHz) or raises TtsError, so the caller can
say plainly that narration is unavailable instead of silently switching to a
different voice.

Synthesized clips are cached on disk keyed by a hash of the text, so replaying
a block plays the saved clip instead of synthesizing it again. The cache is
bounded (DND_TTS_CACHE_MB, default 64) and evicts least-recently-played clips.
DND_TTS_CACHE_DIR moves it; DND_TTS_CACHE_MB=0 turns it off.
"""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path
from typing import Optional

#: Text longer than this is cut; the narration path chunks at block flush.
MAX_TEXT_CHARS = 2000

#: Changing the voice or the model must not replay old clips.
VOICE_ID = "pocket-alba"

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "local_tts"))


class TtsError(Exception):
    pass


# ── Clip cache ──────────────────────────────────────────────────────────────

DEFAULT_CACHE_MB = 64


def cache_dir() -> Path:
    raw = os.environ.get("DND_TTS_CACHE_DIR", "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".cache" / "claude-dnd" / "tts"


def _cache_limit_bytes() -> int:
    try:
        mb = float(os.environ.get("DND_TTS_CACHE_MB", DEFAULT_CACHE_MB))
    except ValueError:
        mb = DEFAULT_CACHE_MB
    return max(0, int(mb * 1024 * 1024))


LANGUAGES = ("auto", "en", "nl")


def clean_lang(lang) -> str:
    """'en', 'nl', or 'auto' (the voice server picks from the text)."""
    lang = str(lang or "auto").lower()
    return lang if lang in LANGUAGES else "auto"


def cache_key(text: str, voice: str = VOICE_ID, lang: str = "auto") -> str:
    """Hash of everything that changes the audio. The text is never a filename.

    'auto' keeps the key the clips were stored under before languages could be
    chosen, so existing cached clips still play."""
    h = hashlib.sha256()
    parts = (voice, text) if lang in ("", "auto") else (voice, text, lang)
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def cache_get(key: str) -> Optional[bytes]:
    if _cache_limit_bytes() <= 0:
        return None
    p = cache_dir() / f"{key}.pcm"
    try:
        data = p.read_bytes()
    except OSError:
        return None
    if not data:
        return None
    try:
        os.utime(p, None)   # mark as recently played, for eviction order
    except OSError:
        pass
    return data


def cache_put(key: str, pcm: bytes) -> None:
    """Store a clip, then evict least-recently-played clips over the bound.

    Best effort: a cache that cannot be written costs a re-synthesis later,
    never the narration now.
    """
    limit = _cache_limit_bytes()
    if limit <= 0 or not pcm or len(pcm) > limit:
        return
    d = cache_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f"{key}.tmp"
        tmp.write_bytes(pcm)
        os.replace(tmp, d / f"{key}.pcm")
        clips = sorted(d.glob("*.pcm"), key=lambda f: f.stat().st_mtime)
        total = sum(f.stat().st_size for f in clips)
        for f in clips:
            if total <= limit:
                break
            total -= f.stat().st_size
            f.unlink()
    except OSError:
        pass


# ── Synthesis ───────────────────────────────────────────────────────────────

def available() -> bool:
    """True when the Pocket TTS environment is installed and not switched off."""
    try:
        import local_fallback
        return local_fallback.enabled()
    except Exception:
        return False


def synthesize_strict(text: str, lang: str = "auto") -> bytes:
    """PCM for `text`, from the cache or the local model. Raises TtsError."""
    text = (text or "").strip()[:MAX_TEXT_CHARS]
    if not text:
        raise TtsError("empty text")
    lang = clean_lang(lang)
    key = cache_key(text, lang=lang)
    hit = cache_get(key)
    if hit:
        return hit
    try:
        import local_fallback
        pcm = local_fallback.synthesize(text, lang)
    except Exception as e:
        raise TtsError(str(e)) from e
    cache_put(key, pcm)
    return pcm


def stop_voice() -> None:
    """Ask the local voice server to exit, if it is running. Never raises."""
    try:
        import local_fallback
        local_fallback.shutdown()
    except Exception:
        pass


def warm_up() -> None:
    """Load the English and Dutch models ahead of the first narration block.

    Runs in a background thread at display start. Silent on any failure: the
    first real request reports a problem if there is one."""
    if not available():
        return
    for phrase in ("Ready.", "Klaar."):
        synthesize(phrase)


def synthesize(text: str, lang: str = "auto") -> Optional[bytes]:
    """Like synthesize_strict, but returns None instead of raising."""
    try:
        return synthesize_strict(text, lang)
    except TtsError:
        return None
