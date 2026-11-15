"""Narrator voice: local TTS only (Pocket TTS by default, optional Kokoro-82M
with DND_TTS_ENGINE=kokoro). No remote service.

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

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "local_tts"))

#: Text longer than this is cut; the narration path chunks at block flush.
MAX_TEXT_CHARS = 2000

def _speed() -> float:
    try:
        s = float(os.environ.get("DND_TTS_SPEED", "1.0"))
    except ValueError:
        s = 1.0
    return min(1.5, max(0.5, s))


#: How fast the narrator talks, as a fraction of the model's natural pace (1.0 = 100%).
#: Pitch is kept when it is changed. DND_TTS_SPEED overrides it.
SPEED = _speed()

#: Changing the voice, the model or the speed must not replay old clips.
def _engine() -> str:
    import local_fallback
    return local_fallback.engine()


#: Narrator voices the Settings menu offers (Kokoro). The first is the default.
KOKORO_VOICES = ("am_adam", "am_fenrir", "am_onyx", "bm_george")


def _kokoro_voice() -> str:
    """DND_KOKORO_VOICE, else the voice chosen in Settings, else am_adam."""
    raw = os.environ.get("DND_KOKORO_VOICE", "").strip().lower()
    if not raw:
        try:
            import settings
            raw = str(settings.load().get("narrator_voice", ""))
        except Exception:
            raw = ""
    raw = "".join(c for c in raw if c.isalpha() or c == "_")
    return raw or KOKORO_VOICES[0]


def _voice_id(speed: float, engine: str = "", voice: str = "") -> str:
    """Names engine, voice and speed. The pocket id is unchanged from before
    engines existed, so its cached clips still play."""
    if (engine or _engine()) == "kokoro":
        base = f"kokoro-{voice or _kokoro_voice()}"
    else:
        base = "pocket-alba"
    return base if speed == 1.0 else f"{base}-{round(speed * 100)}"


VOICE_ID = _voice_id(SPEED)


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


#: Only English is spoken for now. The voice server can also do Dutch; to switch
#: it on again, add "nl" here and give the Dutch summary block its Play button back.
SUPPORTED_LANGUAGES = ("en",)


def clean_lang(lang) -> str:
    """The language the voice speaks. Anything asked for maps to a supported one."""
    lang = str(lang or "en").lower()
    return lang if lang in SUPPORTED_LANGUAGES else SUPPORTED_LANGUAGES[0]


def cache_key(text: str, voice: str = "", lang: str = "en") -> str:
    """Hash of everything that changes the audio. The text is never a filename.

    English keeps the key the clips were stored under before languages could be
    chosen, so existing cached clips still play."""
    h = hashlib.sha256()
    voice = voice or _voice_id(SPEED)
    lang = clean_lang(lang)
    parts = (voice, text) if lang == "en" else (voice, text, lang)
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


def synthesize_strict(text: str, lang: str = "en", voice: str = "") -> bytes:
    """PCM for `text`, from the cache or the local model. Raises TtsError."""
    text = (text or "").strip()[:MAX_TEXT_CHARS]
    if not text:
        raise TtsError("empty text")
    lang = clean_lang(lang)
    kokoro = _engine() == "kokoro"
    voice = (voice if voice in KOKORO_VOICES else "") or _kokoro_voice()
    key = cache_key(text, voice=_voice_id(SPEED, voice=voice), lang=lang)
    hit = cache_get(key)
    if hit:
        return hit
    try:
        import local_fallback
        if kokoro:
            pcm = local_fallback.synthesize(text, lang, SPEED, voice)
        else:
            pcm = local_fallback.synthesize(text, lang, SPEED)
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
    """Load the English model ahead of the first narration block.

    Runs in a background thread at display start. Silent on any failure: the
    first real request reports a problem if there is one."""
    if not available():
        return
    synthesize("Ready.")


def synthesize(text: str, lang: str = "en") -> Optional[bytes]:
    """Like synthesize_strict, but returns None instead of raising."""
    try:
        return synthesize_strict(text, lang)
    except TtsError:
        return None
