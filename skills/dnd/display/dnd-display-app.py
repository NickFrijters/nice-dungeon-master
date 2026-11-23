"""
app.py — DnD DM display server

Receives text chunks from wrapper.py, detects scene context from keywords,
and pushes both to the browser via Server-Sent Events.

Endpoints:
    GET  /                   → serves index.html
    POST /chunk              → receives text chunk from wrapper.py
    POST /stats              → receives character/combat stat updates (merged, persisted)
    GET  /stream             → SSE stream to browser (text + scene + stats events)
    GET  /ping               → health check
    POST /clear              → wipe text log and broadcast clear event
    POST /player-input         → legacy queue endpoint (check_input.py compat)
    POST /player-input/drain   → drain legacy queue (check_input.py compat)
    POST /player-input/stage   → stage an action for review before firing
    POST /player-input/ready   → mark a staged action as ready
    POST /player-input/unstage → remove a staged action
    POST /player-input/skip    → skip a character's turn (stages + readies a skip entry)
    GET  /srd-lookup           → look up a spell/item/feature/condition by name
"""

import hmac
import json
import os
import queue
import re
import secrets
import socket
import subprocess
import sys
import threading
import unicodedata
from collections import deque
from typing import Optional
from flask import Flask, Response, request, render_template, jsonify, send_from_directory, redirect
from flask_cors import CORS

# This file lives at <code-root>/display/ — resolve dirs from its location so
# paths work in any install mode (plugin, standalone skill, or dev clone).
# Writable runtime state goes to rt() (the update-safe runtime dir), NOT here.
_HERE         = os.path.dirname(os.path.abspath(__file__))
_ROOT         = os.path.dirname(_HERE)
SCRIPTS_DIR   = os.path.join(_ROOT, "scripts")
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from runtime_paths import rt          # resolves <data-root>/.runtime
LOG_FILE      = rt("text_log.json")

# SRD lookup module — degrades silently if dataset not built
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)
try:
    import lookup as _lookup
    _SRD_AVAILABLE = True
except Exception:
    _lookup = None          # type: ignore
    _SRD_AVAILABLE = False

import functools as _functools
import script_ranges

from paths import find_campaign as _find_campaign
from utf8io import read_text as _read_text

# Audio module — degrades silently if numpy not installed
_AUDIO_DIR = os.path.dirname(os.path.abspath(__file__))
import sys as _sys
if _AUDIO_DIR not in _sys.path:
    _sys.path.insert(0, _AUDIO_DIR)
try:
    import audio as _audio
    _audio.init()
except Exception:
    _audio = None   # type: ignore

# Narrator voice: local Pocket TTS only (no remote service).
try:
    import tts as _tts
except Exception:
    _tts = None   # type: ignore


def _apply_campaign_sfx_languages() -> None:
    """Read sfx_languages from the active campaign's state.md Session Flags.

    state.md line shape:  `sfx_languages: en,zh,es`
    Takes precedence over the DND_SFX_LANGUAGES env var when present; both
    fall back to English-only if neither is set.
    """
    if _audio is None:
        return
    try:
        camp = open(rt(".campaign"), encoding="utf-8").read().strip()
        if not camp:
            return
        state_md = _find_campaign(camp) / "state.md"
        if not state_md.exists():
            return
        text = state_md.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return
    m = re.search(r"^\s*sfx_languages:\s*([\w,\s\-]+)$", text, re.MULTILINE)
    if not m:
        return
    langs = [l.strip() for l in m.group(1).split(",") if l.strip()]
    valid = [l for l in langs if l in _audio.available_languages()]
    if valid:
        _audio.set_sfx_languages(valid)


_apply_campaign_sfx_languages()

HELP_LOCK     = rt(".help-lock")
CAMP_FILE     = rt(".campaign")
STATS_FILE    = rt("stats.json")
TOKEN_FILE    = rt(".token")
INPUT_FILE    = rt("player_input.json")
TRIGGER_FILE  = rt(".input_trigger")
QUEUE_FILE    = rt(".input_queue")
DEVICES_FILE         = rt(".approved_devices.json")
PENDING_DEVICES_FILE = rt(".pending_devices.json")

# ─── LAN / TLS mode ───────────────────────────────────────────────────────────
# Pass --lan to bind on 0.0.0.0 and protect write endpoints with a token.
# Pass --tls (requires --lan) to enable HTTPS with a self-signed cert.
# Without --lan the server binds to localhost only; no token is required.

_LAN_MODE: bool = "--lan" in sys.argv
_TLS_MODE: bool = "--tls" in sys.argv
if _LAN_MODE:
    sys.argv.remove("--lan")   # prevent Flask from seeing an unknown flag
if _TLS_MODE:
    sys.argv.remove("--tls")

# --menu: the address bar opens the Main Menu first. Cleared once a campaign
# has been requested from the menu, after which "/" is the play screen.
_MENU_FIRST: bool = "--menu" in sys.argv
if _MENU_FIRST:
    sys.argv.remove("--menu")


def _get_or_create_token() -> str:
    """Load or generate the LAN token. Upgrades short legacy tokens to 64-char."""
    try:
        token = open(TOKEN_FILE, encoding="utf-8").read().strip()
        if len(token) >= 48:   # 48+ chars = already long enough
            return token
    except FileNotFoundError:
        pass
    token = secrets.token_hex(32)   # 64-char hex — brute force infeasible
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(token)
    os.chmod(TOKEN_FILE, 0o600)
    return token


_lan_token: Optional[str] = _get_or_create_token() if _LAN_MODE else None


# ─── Rate limiting ────────────────────────────────────────────────────────────
# Simple in-process sliding window: max 20 write requests per IP per minute.
# Prevents spam injection and brute-force token guessing on write endpoints.

import time as _time

_rate_buckets: dict[str, list] = {}
_rate_lock = threading.Lock()
_RATE_WINDOW = 60    # seconds
_RATE_MAX    = 20    # requests per window per IP


def _rate_ok(ip: str) -> bool:
    now = _time.time()
    with _rate_lock:
        bucket = [t for t in _rate_buckets.get(ip, []) if now - t < _RATE_WINDOW]
        if len(bucket) >= _RATE_MAX:
            return False
        bucket.append(now)
        _rate_buckets[ip] = bucket
    return True


# ─── Input validation helpers ─────────────────────────────────────────────────

_PRINTABLE    = re.compile(
    "[^"
    "\x20-\x7E"                 # ASCII printable
    " -ɏ"             # Latin-1 + Latin Extended A/B (é ñ ö ć ş ž etc.)
    "Ͱ-Ͽ"             # Greek
    "Ѐ-ӿ"             # Cyrillic (Russian, Ukrainian)
    "֐-׿"             # Hebrew
    "؀-ۿ"             # Arabic
    "ݐ-ݿ"             # Arabic Supplement
    "ऀ-ॿ"             # Devanagari (Hindi, Marathi)
    "ঀ-৿"             # Bengali
    "஀-௿"             # Tamil
    "ఀ-౿"             # Telugu
    "฀-๿"             # Thai
    "Ḁ-ỿ"             # Latin Extended Additional (Vietnamese diacritics)
    "　-〿"             # CJK Symbols and Punctuation
    "぀-ゟ"             # Hiragana
    "゠-ヿ"             # Katakana
    "㐀-䶿"             # CJK Extension A
    "一-鿿"             # CJK Unified Ideographs
    "가-힯"             # Hangul Syllables
    "＀-￯"             # Halfwidth / Fullwidth
    "]"
)
# [ ] could fake a system line such as [Continue]: or [Menu]:; $ ` \ | < > are shell tricks.
# ! ( ) ; & { } are ordinary writing and stay.
_SHELL_CHARS  = re.compile(r'[$`\\|><\[\]]')
# Unicode \w covers letters from all scripts above. Allow space, apostrophe, hyphen
# inside the name; trim 1-2 char names to a separate branch.
_CHAR_NAME_RE = re.compile(r"^\w[\w '\-]{0,48}\w$|^\w{1,2}$", re.UNICODE)


def _sanitize_input(text: str) -> str:
    """Strip control chars and shell metacharacters from player input text."""
    text = _SHELL_CHARS.sub("", text)
    text = _PRINTABLE.sub("", text)
    return text[:500].strip()


def _char_ok(name: str, known: set) -> bool:
    """Return True if character name is syntactically valid and in the party."""
    if not _CHAR_NAME_RE.match(name):
        return False
    if known and name not in known and name != "Everybody":
        return False
    return True


# ─── Device approval system ───────────────────────────────────────────────────
# Each browser generates a UUID device ID (localStorage). On first input attempt
# from an unseen LAN device, the request is held and the DM sees an Approve/Deny
# card on the display. Localhost is auto-approved. Denied devices are blocked for
# the session.

_approved_devices: set[str]       = set()
_denied_devices:   set[str]       = set()
_pending_devices:  dict[str, dict] = {}  # device_id -> {ip, first_seen}
_devices_lock = threading.Lock()


def _persist_approved_devices() -> None:
    """Persist approved devices to disk. Must be called WITHOUT _devices_lock held."""
    try:
        with _devices_lock:
            data = list(_approved_devices)
        with open(DEVICES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.chmod(DEVICES_FILE, 0o600)
    except Exception:
        pass


def _load_approved_devices() -> None:
    try:
        with open(DEVICES_FILE, encoding="utf-8") as f:
            data = json.load(f)
        with _devices_lock:
            for d in data:
                _approved_devices.add(str(d))
    except Exception:
        pass


def _persist_pending_devices() -> None:
    """Persist pending devices to disk so they survive app restarts. Must be called WITHOUT _devices_lock held."""
    try:
        with _devices_lock:
            data = list(_pending_devices.values())
        with open(PENDING_DEVICES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.chmod(PENDING_DEVICES_FILE, 0o600)
    except Exception:
        pass


def _load_pending_devices() -> None:
    try:
        with open(PENDING_DEVICES_FILE, encoding="utf-8") as f:
            data = json.load(f)
        with _devices_lock:
            for d in data:
                if isinstance(d, dict) and d.get("id"):
                    # Skip if already approved/denied during this run
                    if d["id"] not in _approved_devices and d["id"] not in _denied_devices:
                        _pending_devices[d["id"]] = d
    except Exception:
        pass


_load_approved_devices()
_load_pending_devices()


# A casual home-LAN game doesn't need a per-device approval gate — it's friction
# (every device sits on "Awaiting approval" until the DM taps a card). Default:
# trust any device that can already reach the server. Set DND_REQUIRE_APPROVAL=1
# to restore the approve/deny gate (e.g. on an untrusted/shared network).
_REQUIRE_APPROVAL = os.environ.get("DND_REQUIRE_APPROVAL", "").strip().lower() in ("1", "true", "yes", "on")


def _device_ok(device_id: str, ip: str) -> str:
    """Return 'approved', 'pending', or 'denied' for a given device."""
    if not device_id:
        return "denied"
    _need_persist_approved = False
    _need_persist_pending  = False
    with _devices_lock:
        if device_id in _approved_devices:
            return "approved"
        if device_id in _denied_devices:
            return "denied"
        # Auto-approve localhost always, and every reachable device unless the
        # approval gate is explicitly required.
        if not _REQUIRE_APPROVAL or ip in ("127.0.0.1", "::1"):
            _approved_devices.add(device_id)
            _need_persist_approved = True
        # New LAN device with the gate on — hold and notify DM
        elif device_id not in _pending_devices:
            _pending_devices[device_id] = {
                "id":         device_id,
                "ip":         ip,
                "first_seen": _time.time(),
            }
            _need_persist_pending = True
            _broadcast({"device_request": {"id": device_id, "ip": ip}})
    # Persist outside the lock to avoid deadlock (Lock is not reentrant)
    if _need_persist_approved:
        _persist_approved_devices()
        return "approved"
    if _need_persist_pending:
        _persist_pending_devices()
    return "pending"


# ─── Staged input system ──────────────────────────────────────────────────────
# Players stage their actions from the display companion UI. When all expected
# players mark ready, the combined action is written to TRIGGER_FILE for
# wrapper.py to inject into Claude's PTY stdin.

_staged: dict[str, dict] = {}   # {char_name: {text, ready, timestamp}}
# GO mode: staging never fires the DM by itself. Players discuss and edit freely until
# someone presses GO (POST /player-input/go). Set DND_REQUIRE_GO=0 for the old behaviour.
_REQUIRE_GO = os.environ.get("DND_REQUIRE_GO", "1") != "0"
_staged_lock = threading.Lock()
_expected_count = 1             # updated when stats arrive; min 1
_autorun_threshold: Optional[int] = None  # overrides _expected_count when set via push_stats --autorun-threshold

# Tracks which character names are currently sitting in .input_queue waiting
# for the DM to press Enter. Set when queue is written, cleared when wrapper
# POSTs /queue/consumed after injection. Persists through page reloads via SSE
# initial data and is broadcast to all connected clients on change.
_queue_status: list = []
_queue_status_lock = threading.Lock()

# Last autorun cycle broadcast — replayed on SSE reconnect so late-joining
# clients start the countdown from the correct elapsed position.
# Cleared when autorun_waiting=false (turn resolved or autorun disabled).
_autorun_cycle: Optional[dict] = None
_autorun_cycle_lock = threading.Lock()


def _normalize_slot(slot: dict) -> None:
    """Coerce a spell-slot entry to the canonical {used, max} shape in place.

    Tolerates legacy/alt payloads that use `remaining` instead of `used`.
    Without this, _slot_use/_slot_restore raise KeyError on a slot stored
    under the alt schema (e.g. after a long-rest --spell-slots full-replace).
    """
    if "used" in slot:
        return
    mx = slot.get("max", 0)
    if "remaining" in slot:
        slot["used"] = max(mx - int(slot.get("remaining", 0)), 0)
    else:
        slot["used"] = 0


def _staged_snapshot() -> dict:
    """Return a serialisable copy of the staged dict (no IP field)."""
    return {k: {"text": v["text"], "ready": v["ready"]} for k, v in _staged.items()}


def _check_auto_trigger(force: bool = False) -> None:
    """Move staged-and-ready actions into the DM-gated queue file (.input_queue).

    .input_queue is NOT injected immediately — wrapper.py picks it up the next
    time the DM presses Enter (or Claude explicitly triggers via .input_trigger).
    This gives the DM control over when player actions enter Claude's context.
    """
    if _REQUIRE_GO and not force:
        return
    with _staged_lock:
        if not _staged:
            return
        everybody_ready = "Everybody" in _staged and _staged["Everybody"]["ready"]
        all_ready       = all(v["ready"] for v in _staged.values())
        threshold       = _autorun_threshold if _autorun_threshold is not None else _expected_count
        enough          = len(_staged) >= threshold or everybody_ready
        if not force and not (all_ready and enough):
            return
        taken      = {k: dict(v) for k, v in _staged.items()}
        char_names = list(_staged.keys())
        lines      = [f'[{c}]: {e["text"]}' for c, e in _staged.items()]
        content    = "\n".join(lines) + "\n"
        _staged.clear()

    # Appended, never overwritten: a second batch sent before the DM picked up the first
    # must not erase it (the DM's wait loop deletes the file when it reads it).
    try:
        with _staged_lock, open(QUEUE_FILE, "a", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
    except Exception:
        with _staged_lock:               # could not queue: keep the players' text, do not lose it
            for k, v in taken.items():
                _staged.setdefault(k, v)
        char_names = []

    if char_names:
        _turn_clock_start()                  # the players' turn is now waiting for the DM: start the clock
        with _queue_status_lock:
            for c in char_names:
                if c not in _queue_status:
                    _queue_status.append(c)
    _persist_staged()
    _broadcast({"staged_inputs": _staged_snapshot(), "queue_status": list(_queue_status)})


# ─── Input gate: only characters the DM is waiting on may enter input ───────────
# Explicit gate (POST /input-gate {"allowed": [names] | null}) wins. Otherwise, in
# combat (a turn_order with a current actor) only that PC may act; on a monster's
# turn nobody. Outside combat everybody may type. None = no restriction.
_input_gate: Optional[list] = None


def _effective_allowed() -> Optional[list]:
    if _input_gate is not None:
        return list(_input_gate)
    to = _current_stats.get("turn_order") if isinstance(_current_stats, dict) else None
    if isinstance(to, dict) and to.get("current"):
        cur = str(to["current"])
        pcs = [p.get("name", "") for p in _current_stats.get("players", [])]
        match = [n for n in pcs if n.lower() == cur.lower()]
        return match  # [] on a monster's turn
    return None


def _gate_blocks(character: str) -> bool:
    allowed = _effective_allowed()
    if allowed is None:
        return False
    return character.lower() not in [a.lower() for a in allowed]


def _token_ok() -> bool:
    """Return True if the request carries the correct LAN token (or we're in localhost mode)."""
    if _lan_token is None:
        return True   # localhost mode — no token required
    provided = request.headers.get("X-DND-Token", "")
    return hmac.compare_digest(provided, _lan_token)


app = Flask(__name__)

app.config['TEMPLATES_AUTO_RELOAD'] = True
CORS(app)

# Wire audio broadcast after _broadcast is defined (see bottom of file)
# — done lazily via set_broadcast() called after app is created.

# ─── Scene definitions ────────────────────────────────────────────────────────
# Each scene: keywords (weighted — more = higher priority hit),
# gradient colors [top, bottom], accent color, particle type, display label.

SCENES: dict[str, dict] = {
    "tavern": {
        "keywords": [
            "tavern", "inn", "guttered", "common room", "hearth",
            "fireplace", "ale", "mead", "barkeep", "innkeeper",
            "candle", "tallow", "flagon", "stool", "bar",
        ],
        "colors": ["#1a0800", "#2e1400"],
        "accent": "#c8601a",
        "particles": "embers",
        "label": "The Inn",
    },
    "dungeon": {
        "keywords": [
            "dungeon", "corridor", "stone floor", "torch", "iron gate",
            "portcullis", "cell", "shackle", "pit", "dank",
        ],
        "colors": ["#080818", "#12082e"],
        "accent": "#6a3aaa",
        "particles": "dust",
        "label": "The Dungeon",
    },
    "mine": {
        "keywords": [
            "mine", "seam", "shaft", "tunnel", "ore", "pickaxe",
            "foreman", "deep seam", "ashstone", "cart", "vein",
        ],
        "colors": ["#0a0a0a", "#1a1008"],
        "accent": "#806040",
        "particles": "dust",
        "label": "The Mine",
    },
    "cave": {
        "keywords": [
            "cave", "cavern", "stalactite", "stalagmite", "underground",
            "grotto", "dripping", "echo", "subterranean",
        ],
        "colors": ["#0a1520", "#0a1030"],
        "accent": "#2060a0",
        "particles": "mist",
        "label": "The Cavern",
    },
    "forest": {
        "keywords": [
            "forest", "wood", "tree", "branch", "leaves", "undergrowth",
            "hollow wood", "canopy", "root", "bark", "moss", "fern",
            "thicket", "grove",
        ],
        "colors": ["#041008", "#081a04"],
        "accent": "#40a040",
        "particles": "leaves",
        "label": "The Forest",
    },
    "castle": {
        "keywords": [
            "castle", "rampart", "battlement", "keep", "parapet",
            "drawbridge", "moat", "throne", "great hall", "manor",
        ],
        "colors": ["#0e0e1a", "#1a1a2e"],
        "accent": "#8080c0",
        "particles": "dust",
        "label": "The Castle",
    },
    "mountain": {
        "keywords": [
            "mountain", "snow", "peak", "blizzard", "frost", "glacier",
            "avalanche", "ridge", "cliff", "altitude", "wind",
        ],
        "colors": ["#0a1020", "#1a2040"],
        "accent": "#a0c0e0",
        "particles": "snow",
        "label": "The Mountains",
    },
    "ocean": {
        "keywords": [
            "ocean", "sea", "ship", "wave", "sailor", "port", "harbour",
            "dock", "tide", "storm", "mast", "hull", "water",
        ],
        "colors": ["#000d1a", "#001a33"],
        "accent": "#0060a0",
        "particles": "ripples",
        "label": "The Sea",
    },
    "desert": {
        "keywords": [
            "desert", "sand", "dune", "oasis", "scorching", "arid",
            "mirage", "camel", "sphinx",
        ],
        "colors": ["#1a0f00", "#2e1a00"],
        "accent": "#c08030",
        "particles": "sand",
        "label": "The Desert",
    },
    "ruins": {
        "keywords": [
            "ruins", "ruin", "crumble", "crumbling", "rubble", "ancient",
            "overgrown", "collapsed", "forgotten", "desolate", "remnant",
        ],
        "colors": ["#100e04", "#1e1a08"],
        "accent": "#806830",
        "particles": "dust",
        "label": "The Ruins",
    },
    "swamp": {
        "keywords": [
            "swamp", "marsh", "bog", "mud", "murky", "fetid", "reed",
            "mire", "sludge", "stagnant",
        ],
        "colors": ["#080e04", "#0e1808"],
        "accent": "#406020",
        "particles": "mist",
        "label": "The Swamp",
    },
    "crypt": {
        "keywords": [
            "crypt", "tomb", "grave", "coffin", "undead", "bones",
            "skeleton", "lich", "mausoleum", "burial", "sarcophagus",
            "dead", "death",
        ],
        "colors": ["#08000a", "#140014"],
        "accent": "#602060",
        "particles": "smoke",
        "label": "The Crypt",
    },
    "fire": {
        "keywords": [
            "fire", "flame", "burn", "blaze", "inferno", "conflagration",
            "ember", "char", "smoke", "ash cloud",
        ],
        "colors": ["#1a0500", "#2e0800"],
        "accent": "#ff4400",
        "particles": "embers",
        "label": "The Fire",
    },
    "arcane": {
        "keywords": [
            "arcane", "magic", "spell", "enchant", "rune", "glyph",
            "mystical", "ritual", "incantation", "ward", "sigil",
            "thaumaturgy", "sorcery",
        ],
        "colors": ["#080020", "#12003a"],
        "accent": "#8040ff",
        "particles": "sparks",
        "label": "The Arcane",
    },
    "city": {
        "keywords": [
            "city", "market", "street", "crowd", "village", "town",
            "square", "cobble", "district", "quarter", "merchant",
            "ashenveil",
        ],
        "colors": ["#0a0f1a", "#15202e"],
        "accent": "#6080a0",
        "particles": "rain",
        "label": "The Town",
    },
    "night": {
        "keywords": [
            "night", "midnight", "moon", "star", "dark sky",
            "constellation", "celestial", "dusk", "twilight",
        ],
        "colors": ["#000008", "#04000f"],
        "accent": "#4060a0",
        "particles": "stars",
        "label": "The Night",
    },
    "temple": {
        # `lantern` and `cleric` REMOVED. Every other word here names a place
        # or a fixture of one -- a nave, an altar, incense. Those two named a
        # thing a character CARRIES and a thing a character IS, so they scored
        # wherever the party went: measured, three mentions of a lantern in a
        # room with no other keyword present rendered the room as THE TEMPLE,
        # and so did a cleric walking down a road.
        #
        # `prayer` is the weakest survivor and is kept: people pray in
        # foxholes, but it at least describes an act performed AT the place.
        "keywords": [
            "temple", "shrine", "altar", "holy", "sacred", "chapel",
            "prayer", "incense", "pew", "nave",
            "pale flame",
        ],
        "colors": ["#0e0c18", "#1a1428"],
        "accent": "#c0a060",
        "particles": "smoke",
        "label": "The Temple",
    },
}

# Priority order — checked in sequence; first match wins per chunk
SCENE_PRIORITY = [
    "mine", "crypt", "arcane", "fire", "temple", "dungeon", "cave",
    "forest", "swamp", "castle", "ocean", "mountain", "desert", "ruins",
    "tavern", "city", "night",
]

# ─── ANSI / TUI chrome stripping ─────────────────────────────────────────────

class _ANSIState:
    """Character-by-character ANSI escape-sequence state machine.

    Regex approaches fail when the PTY delivers bytes one at a time, splitting
    sequences like \\x1b[4;2m across chunk boundaries.  This state machine
    carries its state across calls so cross-chunk splits are handled correctly.

    States
    ------
    normal   → emitting regular characters
    esc      → saw ESC (0x1B), waiting to see what kind of sequence follows
    csi      → inside CSI sequence (ESC [ … letter)
    osc      → inside OSC sequence (ESC ] … BEL or ST)
    osc_esc  → inside OSC, just saw ESC — might be the ST terminator (ESC \\)
    """

    __slots__ = ("_s",)

    def __init__(self) -> None:
        self._s: str = "normal"

    def feed(self, text: str) -> str:
        out: list[str] = []
        s = self._s
        for ch in text:
            c = ord(ch)
            if s == "normal":
                if c == 0x1B:
                    s = "esc"
                elif c >= 0x20 or c in (0x09, 0x0A):   # printable / tab / newline
                    out.append(ch)
                # else: other control char (bell, etc.) — discard
            elif s == "esc":
                if ch == "[":
                    s = "csi"
                elif ch == "]":
                    s = "osc"
                else:
                    s = "normal"    # 2-char ESC sequence; discard both bytes
            elif s == "csi":
                if 0x40 <= c <= 0x7E:   # final byte of CSI
                    s = "normal"
                elif c == 0x1B:         # unexpected ESC — start fresh
                    s = "esc"
                # else: parameter / intermediate byte, keep consuming
            elif s == "osc":
                if c == 0x07:           # BEL terminates OSC
                    s = "normal"
                elif c == 0x1B:
                    s = "osc_esc"
                # else: OSC payload, keep consuming
            elif s == "osc_esc":
                s = "normal" if ch == "\\" else "osc"
        self._s = s
        return "".join(out)


_ansi = _ANSIState()
_ansi_lock = threading.Lock()

_BOX_CHARS = set("╭╮╰╯│─┌┐└┘├┤┬┴┼━═║╔╗╚╝")
_BOX_CHAR_STRIP = "╭╮╰╯│─┌┐└┘├┤┬┴┼━═║╔╗╚╝"  # same set as string for str.strip()

# Characters used by Claude CLI spinner / prompt / UI
_SPINNER_CHARS = set("✽⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏◐◓◑◒◌◎●")
_PROMPT_STARTS = ("❯", ">", "·", "▸", "ℹ", "✓", "⚠", "✗", "⟳", "↳")


def _handle_cr(text: str) -> str:
    """Handle carriage returns the way a real terminal would.

    Two distinct cases:
      \\r\\n  — a real newline (\\r\\n line ending).  Normalise to \\n first
                so the content is preserved.
      bare \\r — cursor-to-column-0 for in-place token updates.  Claude CLI
                streams each token by rewriting the current line:
                  "The" → \\r"The Gut" → \\r"The Gutte" → …
                Keep only the last segment (= the final written state).
    """
    # Step 1: treat \\r\\n as a real newline — must come before bare-\\r logic
    text = text.replace("\r\n", "\n")

    # Step 2: handle remaining bare \\r (in-place rewrites)
    lines = text.split("\n")
    result = []
    for line in lines:
        if "\r" in line:
            parts = line.split("\r")
            result.append(parts[-1])   # last segment = final state of the line
        else:
            result.append(line)
    return "\n".join(result)


def _strip_ansi(text: str) -> str:
    text = _handle_cr(text)
    with _ansi_lock:
        text = _ansi.feed(text)
    return text


def _is_chrome(line: str) -> bool:
    """Return True for lines that are TUI chrome, not DM narration.

    The Claude CLI wraps responses in a box:
        ╭──────────────────╮
        │ narration text   │
        ╰──────────────────╯
    We strip the border characters from line edges first so that content
    lines like "│ The tavern smells of ale │" are NOT filtered — only pure
    border rows (all box chars, no letters) are treated as chrome.
    """
    stripped = line.strip()

    if not stripped:
        return False   # keep blank lines — they separate paragraphs

    # Strip leading/trailing box-drawing border chars to expose the real content.
    # "│ The tavern smells of ale │" → "The tavern smells of ale"
    content = stripped.strip(_BOX_CHAR_STRIP + " ")

    # If nothing remains, the line was entirely box-drawing chrome (a border row).
    if not content:
        return True

    # All remaining checks operate on content (without box border decoration).
    c = content

    # CLI prompt / spinner lines
    if c[0] in _SPINNER_CHARS:
        return True
    if c.startswith(_PROMPT_STARTS):
        return True

    # Common spinner word patterns (e.g. "Thinking…")
    if re.match(r"^[A-Z][a-z]+ing…?$", c):
        return True

    # Claude branding / metadata
    if "claude.ai" in c.lower():
        return True

    # Session-resume instructions emitted at end of response
    if c.startswith("Resume this session with:") or re.match(r"^claude\s+--resume\s+", c):
        return True

    # Status-bar patterns: cost, token counts, rate-limit bars
    # Note: "Tokens300/0" has no space — use \s* not \s+
    if re.search(r"Tokens\s*\d|5hr:|7d:|Session:|Total:\s*\$", c):
        return True

    # Model/plan header lines ("Sonnet 4.6", "Claude Pro", "Professional", etc.)
    if re.search(r"Sonnet|Haiku|Opus|Claude\s*(Pro|Max|Team|Code)\b|Professional\b|claude-\d", c, re.I):
        return True

    # Tool-use labels emitted by Claude CLI ("Bash command", "Read command", etc.)
    if re.match(r"^(Bash|Read|Write|Edit|Glob|Grep|WebFetch|WebSearch|TaskCreate|TaskUpdate|TaskGet|TaskList|NotebookEdit|Agent|ToolSearch|ExitPlanMode|EnterPlanMode|ScheduleWakeup|Monitor|RemoteTrigger|CronCreate|CronDelete|CronList|AskUserQuestion)(\s+(command|tool|result|call))?$", c, re.I):
        return True

    # Timestamp-prefixed lines ("3ts ago …", "2m ago …") — UI timestamps concatenated with content
    if re.match(r"^\d+\s*[smhdt]+s?\s*(ago\s*)?[A-Z]", c):
        return True

    # Bare numbers (token counts, cursor column positions, etc.)
    if re.match(r"^\d+$", c):
        return True

    # Single stray characters that are ANSI/escape remnants, not real words
    if len(c) == 1 and not c.isalpha():
        return True

    # Very short fragments with no word in them (≤3 chars) are escape-code
    # residue, not narration. "A word" is decided per character with
    # str.isalpha, not [a-zA-Z]: the ASCII test dropped 「好的」, 「はい。」,
    # 「不。」, 「Да.」 and 「Нет」 while keeping "No.". Two letters make a word in
    # any script; in a script without case (Han, Kana, Hangul) one letter is
    # already a word -- 不 is "no".
    if len(c) <= 3:
        letters = [ch for ch in c if ch.isalpha()]
        caseless_word = any(unicodedata.category(ch) == "Lo" for ch in letters)
        if len(letters) < 2 and not caseless_word:
            return True

    return False


def _clean(text: str) -> str:
    text = _strip_ansi(text)
    lines = text.split("\n")
    kept = []
    for line in lines:
        if _is_chrome(line):
            continue
        # Strip box-border chars from edges so content reaches the browser clean.
        s = line.strip().strip(_BOX_CHAR_STRIP + " ")
        # Blank line → preserve as paragraph separator
        kept.append(s if s else "")
    # Collapse runs of more than two consecutive blank lines
    result = re.sub(r"\n{3,}", "\n\n", "\n".join(kept))
    return result


# ─── Scene detection ──────────────────────────────────────────────────────────

_current_scene_name: str = "tavern"   # default — we start in the inn
_scene_buffer: list[str] = []
_BUFFER_WINDOW = 20   # analyse last N cleaned chunks together

# How much evidence a scene change needs. A bare `max()` over keyword counts
# meant ONE stray keyword could move the background, and two plausible scenes
# traded it back and forth on a one-point edge.
#
# MARGIN is the gap a challenger must open over the scene already on screen.
# The INCUMBENT BONUS lets the current scene defend itself even after its own
# keywords have scrolled out of the window, which is the usual case for a long
# scene: a fight in a crypt stops saying "crypt" after the first line.
#
# The bonus is 1 rather than 2 by measurement against this repo's own keyword
# lists, not by feel. A genuine transition narrated over three chunks --
# arriving at a city -- scores 4; incidental noise, a couple of night words
# while at sea, scores 2. With MARGIN 2 an aged-out incumbent needs the
# challenger at 3, which admits the real change and blocks the noise. At 2 the
# bar becomes 4 and a genuine arrival stops registering.
#
# There is deliberately NO separate minimum-hits floor. One was written here
# first and it could never fire: the test below requires
# `best >= scores.get(current, 0) + BONUS + MARGIN`, which is at least 3, so a
# floor of 2 is unreachable for every input. A constant that decides nothing
# reads as protection and is not, so it is gone rather than documented.
_SCENE_SWITCH_MARGIN = 2    # a challenger must beat the scene on screen by this
_SCENE_INCUMBENT_BONUS = 1


def _keyword_pattern(keyword: str) -> "re.Pattern":
    r"""Match `keyword` as a whole word where the script has word delimiters.

    Scored with `window.count(kw)` before this, which is a SUBSTRING count:
    the arcane keyword `ward` matched inside "deputy warden", so a prison
    fight scored as arcane. It also matches "wardrobe", "forward", "steward".

    Anchors are applied only for keywords in a script that separates words. A
    campaign may override a scene's keyword list in its own language (the lists
    shipped here are English), and `\b` is an impossible condition for Han,
    Kana, Hangul or Thai -- see scripts/script_ranges.py.
    """
    return script_ranges.whole_word_pattern(keyword)


@_functools.lru_cache(maxsize=512)
def _cached_keyword_pattern(keyword: str) -> "re.Pattern":
    return _keyword_pattern(keyword)


def _detect_scene(text: str) -> Optional[dict]:
    global _current_scene_name, _scene_buffer

    _scene_buffer.append(text.lower())
    if len(_scene_buffer) > _BUFFER_WINDOW:
        _scene_buffer.pop(0)

    window = " ".join(_scene_buffer)

    scores: dict[str, int] = {}
    for scene_name in SCENE_PRIORITY:
        scene = SCENES[scene_name]
        score = sum(len(_cached_keyword_pattern(kw).findall(window))
                    for kw in scene["keywords"])
        if score > 0:
            scores[scene_name] = score

    if not scores:
        return None

    best = max(scores, key=lambda k: scores[k])
    if best == _current_scene_name:
        return None   # no change
    # `_current_scene_name` may not be in `scores` at all once its keywords
    # have scrolled out of the window -- that is the case the bonus is for.
    held = scores.get(_current_scene_name, 0) + _SCENE_INCUMBENT_BONUS
    if scores[best] - held < _SCENE_SWITCH_MARGIN:
        return None

    _current_scene_name = best
    return SCENES[best] | {"name": best}


# ─── SSE client registry ─────────────────────────────────────────────────────

_clients: list[queue.Queue] = []
_clients_lock = threading.Lock()
# ─── Text replay log ──────────────────────────────────────────────────────────
# Stores cleaned text chunks so late-connecting browsers can catch up.
# Persisted to LOG_FILE so it survives Flask restarts (Chromecast reconnects, new sessions).
# Full body retention (player requirement, 2026-08-18): cap raised 60 → 2000 with
# full reload at startup (_load_log), so cross-session body text stays recoverable
# and archivable in both formats. On plugin updates, re-check these three spots.
_text_log: deque = deque(maxlen=2000)
_text_log_lock = threading.Lock()

# ─── Session tail buffer ──────────────────────────────────────────────────────
# Rolling buffer of the last 30 text events — written to session_tail.json after
# every /chunk POST so it survives crashes. Read at /dnd load for display replay.
# Path is campaign-specific so tails from different campaigns don't overwrite each other.
#
# ROBUSTNESS GUARANTEES (after the 2026-05-01 wipe-bug fix):
#   1. _load_tail is NON-DESTRUCTIVE: it never wipes the in-memory buffer based
#      on an empty/filtered-out load. If the file is empty, missing, or every
#      entry is filtered out by campaign mismatch, the existing buffer stays.
#   2. _persist_tail SKIPS ON EMPTY: it never overwrites an existing non-empty
#      file with an empty buffer. This breaks the "filter zeros buffer →
#      persist writes [] → file lost" failure chain.
#   3. _persist_tail uses ATOMIC WRITES: writes to a tempfile and atomically
#      renames into place, so a partial/crashed write can never produce a
#      truncated or zero-byte file.
#   4. The legacy fallback path is GONE. Tails only ever land in the campaign-
#      specific file. If CAMP_FILE is missing/empty when persist would fire,
#      we keep the buffer in memory and skip the write rather than dropping
#      events into a shared file that bleeds across campaigns.
_tail_buffer: deque = deque(maxlen=30)
_tail_lock   = threading.Lock()


def _get_tail_file() -> "str | None":
    """Return the campaign-specific tail path, or None if no campaign is set.

    Previously this fell back to a process-local path on the skill side. That
    fallback caused tail bleed across campaigns and made the wipe-on-load bug
    much harder to diagnose. The new contract: campaign-specific or nothing.
    """
    try:
        camp = open(CAMP_FILE, encoding="utf-8").read().strip()
        if camp:
            return str(_find_campaign(camp) / "session_tail.json")
    except Exception:
        pass
    return None


def _persist_tail() -> None:
    """Write _tail_buffer to disk. Refuses to overwrite content with empty.

    Atomic-write guarantee: writes to <path>.tmp then renames, so observers
    (the next /dnd load reading the file) never see a partial or zero-byte
    state.
    """
    path = _get_tail_file()
    if not path:
        # No active campaign — keep the buffer in memory, skip disk.
        return
    try:
        with _tail_lock:
            data = list(_tail_buffer)
        # Skip-on-empty guard: never blank a file that currently has content.
        if not data and os.path.exists(path):
            try:
                if os.path.getsize(path) > 2:  # 2 bytes = "[]"
                    print(f"_persist_tail: skipping empty write — {path} has content",
                          file=sys.stderr)
                    return
            except OSError:
                pass
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, path)
    except Exception as e:
        print(f"_persist_tail: write failed: {e}", file=sys.stderr)


def _load_tail() -> None:
    """Load tail from disk into the buffer. NON-DESTRUCTIVE on empty/mismatch.

    Old behavior: cleared the buffer first, then re-appended filtered entries.
    This created the wipe bug: if every entry was filtered out (campaign
    mismatch) the buffer ended up empty and the next persist wrote [] to disk.

    New behavior: build the candidate buffer first, then ONLY swap it into
    place if at least one entry survived filtering. If nothing survives, the
    in-memory buffer is left alone — preserves whatever was already loaded.
    """
    path = _get_tail_file()
    if not path:
        return
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return  # No file yet — keep in-memory state
    except (OSError, json.JSONDecodeError) as e:
        print(f"_load_tail: read failed for {path}: {e}", file=sys.stderr)
        return
    if not isinstance(data, list):
        print(f"_load_tail: file content is not a list — leaving buffer alone",
              file=sys.stderr)
        return

    try:
        current_camp = open(CAMP_FILE, encoding="utf-8").read().strip()
    except Exception:
        current_camp = ""

    candidate: list = []
    for item in data[-30:]:
        if not isinstance(item, dict):
            continue
        item_camp = item.get("_camp", "")
        # If we know the campaign and the entry stamps a different campaign,
        # skip it. Entries with no stamp are kept (legacy data + tolerance).
        if current_camp and item_camp and item_camp != current_camp:
            continue
        candidate.append(item)

    if not candidate:
        # Loaded data filtered down to nothing. DO NOT replace the buffer —
        # this is the wipe-bug guard.
        return

    with _tail_lock:
        _tail_buffer.clear()
        for item in candidate:
            _tail_buffer.append(item)


_load_tail()


_atomic_lock = threading.Lock()


def _atomic_write_json(path: str, data) -> bool:
    """Write JSON to a temp file, then replace the real file. A crash mid-write
    can never leave a half-written (unreadable) file behind. True on success."""
    try:
        with _atomic_lock:
            tmp = f"{path}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
            os.replace(tmp, path)
        return True
    except Exception as e:
        print(f"_atomic_write_json: {path}: {e}", file=sys.stderr)
        return False


def _persist_log() -> None:
    """Write the current text log to disk. Called after every chunk."""
    with _text_log_lock:
        data = list(_text_log)
    _atomic_write_json(LOG_FILE, data)


def _load_log() -> None:
    """Load a previously persisted text log on startup.
    Handles both old string format and new dict format."""
    try:
        with open(LOG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        with _text_log_lock:
            _text_log.clear()
            for item in data[:]:
                # Migrate old plain-string entries to dict format
                if isinstance(item, str):
                    item = {"text": item}
                _text_log.append(item)
    except Exception:
        pass
    # An unreadable or missing log must not leave the screen blank after a restart:
    # fall back to the campaign's session tail (what the browser would replay).
    with _text_log_lock:
        empty = not _text_log
    if empty:
        with _tail_lock:
            seed = [dict(t) for t in _tail_buffer]
        with _text_log_lock:
            _text_log.extend(seed)


_load_log()


# ─── Character / combat stats ─────────────────────────────────────────────────
# Stored as {"players": [...], "turn_order": {...}|null}
# Players are merged by name so partial updates (just HP, just XP) work.

_current_stats: dict = {}
_stats_lock = threading.Lock()


# Per-campaign copy of everything only the display holds (live party values, NPCs
# here, combat board, quests, factions, world time, turn order, staged player
# input). Lives in the campaign folder as display_state.json, so it survives the
# display being restarted, a crash, and Save & exit followed by loading again.
STATE_NAME = "display_state.json"
_DISPLAY_KEYS = ("npcs", "combat", "quests", "factions", "world_time", "turn_order", "place")
_LIVE_PLAYER_KEYS = ("hp", "conditions", "spell_slots", "hit_dice", "effects",
                     "concentration", "inspiration", "milestones", "xp")
_awaiting_restore = False     # set by /clear: do not overwrite the campaign copy until it was read


def _campaign_state_path() -> "str | None":
    try:
        camp = open(CAMP_FILE, encoding="utf-8").read().strip()
        if camp:
            d = _find_campaign(camp)
            if os.path.isdir(d):
                return str(d / STATE_NAME)
    except Exception:
        pass
    return None


def _read_campaign_state(name: str) -> dict:
    try:
        with open(str(_find_campaign(name) / STATE_NAME), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _persist_stats() -> None:
    """Save the display's own state: runtime stats.json plus the campaign copy. Atomic."""
    with _stats_lock:
        data = json.loads(json.dumps(_current_stats))   # deep copy, JSON-safe
    _atomic_write_json(STATS_FILE, data)
    path = _campaign_state_path()
    if not path or _awaiting_restore:
        return
    with _staged_lock:
        staged = {k: dict(v) for k, v in _staged.items()}
    with _queue_status_lock:
        qs = list(_queue_status)
    try:
        camp = open(CAMP_FILE, encoding="utf-8").read().strip()
    except Exception:
        camp = ""
    _atomic_write_json(path, {"campaign": camp, "saved_at": _time.time(),
                              "stats": data, "staged": staged, "queue_status": qs})


def _persist_staged() -> None:
    """Staged player input changed (or the queue indicator did): save it too."""
    _persist_stats()


def _load_stats() -> None:
    global _expected_count
    data = None
    try:
        with open(STATS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = None
    if not isinstance(data, dict) or not data:
        # Missing or unreadable stats.json: fall back to the active campaign's copy.
        try:
            camp = open(CAMP_FILE, encoding="utf-8").read().strip()
            saved = _read_campaign_state(camp) if camp else {}
            if isinstance(saved.get("stats"), dict):
                data = saved["stats"]
        except Exception:
            data = None
    if not isinstance(data, dict):
        return
    with _stats_lock:
        _current_stats.update(data)
    # Initialise expected player count from persisted stats so solo-mode
    # detection is correct immediately after restart, without waiting for
    # the next /stats POST.
    loaded_players = data.get("players", [])
    if loaded_players:
        _expected_count = max(1, len(loaded_players))


_load_stats()


def _restore_staged_at_start() -> None:
    """After a restart: put back the player input that was staged but not yet sent."""
    try:
        camp = open(CAMP_FILE, encoding="utf-8").read().strip()
    except Exception:
        return
    saved = _read_campaign_state(camp) if camp else {}
    if saved.get("campaign") not in (camp, None):
        return
    with _staged_lock:
        if not _staged and isinstance(saved.get("staged"), dict):
            for k, v in saved["staged"].items():
                if isinstance(v, dict) and v.get("text"):
                    _staged[str(k)] = {"text": str(v["text"]), "ready": bool(v.get("ready")),
                                       "timestamp": float(v.get("timestamp") or _time.time())}
    if os.path.exists(QUEUE_FILE) and isinstance(saved.get("queue_status"), list):
        with _queue_status_lock:
            if not _queue_status:
                _queue_status.extend(str(x) for x in saved["queue_status"])


_restore_staged_at_start()


_NO_COMBAT_RE = re.compile(r"^[\s*_()\-–—.]*(none|geen|n/?a|nvt|no active combat|not in combat)?[\s*_()\-–—.]*$", re.I)


def _state_md_combat(name: str):
    """What the campaign's own notes say about a fight in progress: True (an `## Active Combat` section with
    content), False (the section is empty or says none), None (no state.md or no such section: unknown)."""
    try:
        text = (_find_campaign(name) / "state.md").read_text(encoding="utf-8")
    except Exception:
        return None
    m = re.search(r"^##[ \t]+Active Combat[ \t]*\r?\n(.*?)(?=^##[ \t]|\Z)", text, re.M | re.S | re.I)
    if not m:
        return None
    return not _NO_COMBAT_RE.match(m.group(1).strip())


def _restore_campaign_display(name: str, data: dict) -> None:
    """A campaign was (re)loaded: bring back what only the display held.

    Display-only keys (NPCs here, combat board, quests, factions, world time, turn
    order) that this push did not set come from the campaign copy; if the copy has
    none, the previous campaign's value is dropped so nothing bleeds across.
    Live party values (HP, slots, conditions, effects) come back too, but only when
    the copy is newer than every character file (a Save writes the files, and then
    the files are as new as the copy)."""
    saved = _read_campaign_state(name)
    sstats = saved.get("stats") if isinstance(saved.get("stats"), dict) else {}
    _clear_dice_pending()            # roll requests from before the load are not waited on any more
    with _stats_lock:
        for k in _DISPLAY_KEYS:
            if k in data:
                continue
            if k in sstats and sstats[k] is not None:
                _current_stats[k] = sstats[k]
            else:
                _current_stats.pop(k, None)
        # A fight the display remembers but the campaign's notes say is over (a combat that was never cleared) must not
        # open the combat screen on load.
        if _state_md_combat(name) is False:
            for k in ("combat", "turn_order"):
                if k not in data:
                    _current_stats.pop(k, None)
        try:
            files = list((_find_campaign(name) / "characters").glob("*.md"))
            newest = max((f.stat().st_mtime for f in files), default=0.0)
        except Exception:
            newest = 0.0
        if float(saved.get("saved_at") or 0) > newest:
            by_name = {p.get("name"): p for p in sstats.get("players", []) if isinstance(p, dict)}
            for p in _current_stats.get("players", []):
                old = by_name.get(p.get("name"))
                if not old:
                    continue
                for k in _LIVE_PLAYER_KEYS:
                    if k in old:
                        p[k] = old[k]
    with _staged_lock:
        if not _staged and isinstance(saved.get("staged"), dict):
            for k, v in saved["staged"].items():
                if isinstance(v, dict) and v.get("text"):
                    _staged[str(k)] = {"text": str(v["text"]), "ready": bool(v.get("ready")),
                                       "timestamp": float(v.get("timestamp") or _time.time())}


# ─── Player input queue ───────────────────────────────────────────────────────
# Stores actions submitted from the display companion (iPad etc.) until the DM
# triggers the next turn. Drained by check_input.py via /player-input/drain.

_input_queue: list[dict] = []
_input_lock = threading.Lock()

# Pending DM-issued dice requests: request_id → {chars: set[str], meta: {...}, started_at: float}
# A request is "complete" when its chars set is empty (every prescribed player rolled).
# send.py --wait polls GET /dice-request/<id> to know when the DM can move on.
_dice_pending: dict = {}
_dice_pending_lock = threading.Lock()
# What each player rolled for a request, so the DM gets the number in the same call that waited for it.
_dice_last_poll: dict = {}         # request_id -> when the DM last asked how it is going (it is waiting while this is fresh)
_DICE_POLL_FRESH = 20.0
_dice_results: dict = {}          # request_id -> {character: {"total", "natural", "text", "at"}}


_DICE_PENDING_MAX_AGE = 600   # seconds: a roll nobody made in ten minutes is not being waited on any more


def _clear_dice_pending() -> None:
    """The scene moved on (next turn, combat over, campaign loaded): nothing is being waited on any more."""
    with _dice_pending_lock:
        ids = list(_dice_pending.keys())
        _dice_pending.clear()
    if ids:
        for rid in ids:
            _broadcast({"dice_request_cancelled": rid})
        _broadcast({"dice_pending": []})


def _close_answered_rolls(grace: float = 5.0) -> None:
    """The DM narrated on: a roll it asked for more than a few seconds ago has been dealt with (rolled on screen,
    typed in, or rolled by the DM), so its "Waiting on" notice must go. A request sent together with this
    narration (within `grace` seconds) stays open."""
    now = _time.time()
    with _dice_pending_lock:
        done = [rid for rid, e in _dice_pending.items() if now - float(e.get("started_at") or now) > grace]
        for rid in done:
            _dice_pending.pop(rid, None)
    if done:
        for rid in done:
            _broadcast({"dice_request_cancelled": rid})
        _broadcast({"dice_pending": _dice_pending_snapshot()})


def _dice_pending_snapshot() -> list:
    with _dice_pending_lock:
        now = _time.time()
        for rid in [r for r, e in _dice_pending.items() if now - float(e.get("started_at") or now) > _DICE_PENDING_MAX_AGE]:
            _dice_pending.pop(rid, None)
        return [
            {"request_id": rid, "pending": sorted(e["chars"]), "label": e["meta"].get("label", "")}
            for rid, e in _dice_pending.items() if e["chars"]
        ]


def _load_input_queue() -> None:
    global _input_queue
    try:
        with open(INPUT_FILE, encoding="utf-8") as f:
            _input_queue = json.load(f)
    except Exception:
        _input_queue = []


def _persist_input_queue() -> None:
    with _input_lock:
        data = list(_input_queue)
    _atomic_write_json(INPUT_FILE, data)


_load_input_queue()


def _broadcast(payload: dict) -> None:
    with _clients_lock:
        dead = []
        for q in _clients:
            try:
                q.put_nowait(payload)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _clients.remove(q)


# ─── Routes ──────────────────────────────────────────────────────────────────

def _display_settings() -> dict:
    """Theme, style, text size, typing speed, on-screen dice, sound and narration
    audio: the same on every screen, chosen in the Settings menu."""
    try:
        import settings as _settings
        return _settings.display_settings()
    except Exception:
        return {"theme": "dark", "skin": "vellum", "text_size": 0.75, "ui_size": 1.0, "type_speed": "normal",
                "roll_on_screen": False, "sound_effects": True, "narration_audio": False}


@app.route("/")
def index():
    if _MENU_FIRST and not _story_ready():      # the story is up (the DM opened a campaign some other way): show it
        return redirect("/menu")
    # Pass LAN token to template so the browser can authenticate /help-request
    return render_template(
        "index.html",
        lan_token=_lan_token or "",
        display_settings=_display_settings(),
        tts_available=bool(_tts is not None and _tts.available()),
        tts_engine=_tts_engine_label(),
    )


# ─── Control from this computer only ─────────────────────────────────────────
# In LAN mode the page (and its token) can be opened by any device on the
# network. Loading a campaign, changing settings and Quit change the running
# game, so they answer only to the computer the game runs on: loopback, or this
# machine's own network address (browsing to its own LAN IP shows up that way).

def _local_addresses() -> set:
    addrs = {"127.0.0.1", "::1", "localhost"}
    try:
        addrs.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))    # sends nothing: asks which address would be used
        addrs.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return addrs


def _from_this_machine() -> bool:
    return (request.remote_addr or "") in _local_addresses()


_THIS_MACHINE_ONLY = ("Only the computer running the game can do this", 403)


# ─── Main Menu ───────────────────────────────────────────────────────────────
# The web app is the front door: Claude starts the display, then the player
# picks what to do here. Menu actions travel to Claude through the same input
# queue the autorun loop already reads, as a line like "[Menu]: load <name>".

def _list_campaigns() -> list[dict]:
    """Campaigns on disk, most recently played first."""
    from paths import campaigns_dir, campaign_ruleset
    root = campaigns_dir()
    active = _active_campaign_name()
    found = []
    try:
        entries = list(root.iterdir())
    except OSError:
        return []
    for d in entries:
        if not d.is_dir() or d.name.startswith(".") or ".backup-" in d.name:
            continue
        state = d / "state.md"
        marker = state if state.exists() else d
        try:
            played = marker.stat().st_mtime
        except OSError:
            played = 0.0
        found.append({
            "name": d.name,
            "ruleset": campaign_ruleset(d.name),
            "last_played": played,
            "active": d.name == active,
        })
    found.sort(key=lambda c: c["last_played"], reverse=True)
    return found


def _dm_listening() -> bool:
    """True while a Claude session is waiting for input (its wait loop beats every few seconds)."""
    try:
        return _time.time() - os.path.getmtime(rt(".dm-heartbeat")) < 300
    except OSError:
        return False


# Save & exit: when it was asked for. The menu says "Saving..." until Claude is back
# in its wait loop (its next heartbeat is newer than this), or ten minutes pass.
_exit_requested_at = None


def _saving() -> bool:
    if _exit_requested_at is None:
        return False
    now = _time.time()
    if now - _exit_requested_at > 600:
        return False
    try:
        return os.path.getmtime(rt(".dm-heartbeat")) <= _exit_requested_at
    except OSError:
        return True


def _open_campaign_here(name: str) -> None:
    """Show the campaign's party and mark it active, without waiting for Claude.

    The party comes straight from the character files, so the sidebar is right
    the moment a campaign is picked. Claude's own load, which has the full
    sheets, merges over it by name. A campaign that is already open with its
    characters on screen is left alone, so live HP is not reset by 'Continue'.
    """
    try:
        import party as _party
        players = _party.load_party(name)
    except Exception:
        players = []
    with _stats_lock:
        shown = {p.get("name") for p in _current_stats.get("players", [])}
    already = (_active_campaign_name() == name and bool(players)
               and {p["name"] for p in players} <= shown)
    if already:
        return
    # Always replace: a campaign with no character files must not keep the last party on screen.
    body: dict = {"campaign": name, "players": players, "replace_players": True, "_internal": True}
    try:
        with app.test_client() as c:       # the same code path as a push from Claude
            c.post("/stats", json=body, headers={"X-DND-Token": _lan_token or ""})
    except Exception:
        pass


# True once the DM has put story text on screen in THIS run of the display since the last visit to the menu. The story
# log is kept on disk, so without this a restarted display (or one back at the menu after Save & exit) still looked
# "in the story" from last time: the address bar skipped the menu and Continue opened a play screen nobody answered.
_story_live: bool = False


def _story_ready() -> bool:
    """The DM has opened the active campaign and put its story on screen (the newest story block belongs to it)."""
    active = _active_campaign_name()
    with _text_log_lock:
        last = _text_log[-1] if _text_log else None
    return bool(_story_live and active and last and last.get("_camp") == active)


@app.route("/menu")
def menu():
    return render_template("menu.html", lan_token=_lan_token or "")


@app.route("/menu/campaigns")
def menu_campaigns():
    active = _active_campaign_name()
    story_ready = _story_ready()
    return jsonify({"active": active,
                    "dm_listening": _dm_listening(),
                    "saving": _saving(),
                    "story_ready": story_ready,
                    "campaigns": _list_campaigns()})


@app.route("/menu/load", methods=["POST"])
def menu_load():
    """Ask Claude to load a campaign. Only names that exist on disk are accepted."""
    if not _token_ok():
        return "Forbidden", 403
    if not _from_this_machine():
        return _THIS_MACHINE_ONLY
    data = request.get_json(force=True, silent=True) or {}
    name = str(data.get("campaign", ""))
    if name not in {c["name"] for c in _list_campaigns()}:
        return "unknown campaign", 404
    # Straight into the file Claude's wait loop reads (appended, so queued
    # player actions are not lost).
    try:
        with _staged_lock, open(QUEUE_FILE, "a", encoding="utf-8") as f:
            f.write(f"[Menu]: load {name}\n")
    except OSError:
        return "could not queue", 500
    global _MENU_FIRST
    _MENU_FIRST = False
    _open_campaign_here(name)
    return "", 204


# ── Managing campaigns: delete goes to a trash folder first, so a mistake can be undone ──
def _trash_dir():
    from paths import campaigns_dir
    return campaigns_dir().parent / "campaign-trash"


def _list_trash() -> list[dict]:
    out = []
    try:
        entries = list(_trash_dir().iterdir())
    except OSError:
        return []
    for d in entries:
        if not d.is_dir() or "__" not in d.name:
            continue
        name, _, stamp = d.name.rpartition("__")
        try:
            when = d.stat().st_mtime
        except OSError:
            when = 0.0
        out.append({"entry": d.name, "name": name, "deleted": when})
    out.sort(key=lambda e: e["deleted"], reverse=True)
    return out


@app.route("/menu/trash")
def menu_trash():
    return jsonify({"trash": _list_trash()})


@app.route("/menu/delete", methods=["POST"])
def menu_delete():
    """Move a campaign to the trash (it can be restored). Deleting the open campaign also closes it."""
    if not _token_ok():
        return "Forbidden", 403
    if not _from_this_machine():
        return _THIS_MACHINE_ONLY
    data = request.get_json(force=True, silent=True) or {}
    name = str(data.get("campaign", ""))
    if name not in {c["name"] for c in _list_campaigns()}:
        return "unknown campaign", 404
    import shutil
    from paths import campaigns_dir
    src = campaigns_dir() / name
    trash = _trash_dir()
    try:
        trash.mkdir(parents=True, exist_ok=True)
        dst = trash / f"{name}__{_time.strftime('%Y%m%d-%H%M%S')}"
        n = 1
        while dst.exists():
            dst = trash / f"{name}__{_time.strftime('%Y%m%d-%H%M%S')}-{n}"
            n += 1
        shutil.move(str(src), str(dst))
    except OSError as e:
        return f"could not delete: {e}", 500
    if _active_campaign_name() == name:          # it was the open one: close it, so nothing points at a missing folder
        try:
            os.remove(CAMP_FILE)
        except OSError:
            pass
        with _stats_lock:
            for k in ("players",) + _DISPLAY_KEYS:
                _current_stats.pop(k, None)
            current = dict(_current_stats)
        _persist_stats()
        _broadcast({"stats": current})
    return jsonify({"entry": dst.name}), 200


@app.route("/menu/restore", methods=["POST"])
def menu_restore():
    """Bring a deleted campaign back from the trash under its old name (or 'name-restored' if that is taken)."""
    if not _token_ok():
        return "Forbidden", 403
    if not _from_this_machine():
        return _THIS_MACHINE_ONLY
    data = request.get_json(force=True, silent=True) or {}
    entry = str(data.get("entry", ""))
    if entry not in {e["entry"] for e in _list_trash()}:
        return "not in the trash", 404
    import shutil
    from paths import campaigns_dir
    name = entry.rpartition("__")[0]
    dst = campaigns_dir() / name
    n = 1
    while dst.exists():
        dst = campaigns_dir() / (f"{name}-restored" if n == 1 else f"{name}-restored-{n}")
        n += 1
    try:
        shutil.move(str(_trash_dir() / entry), str(dst))
    except OSError as e:
        return f"could not restore: {e}", 500
    return jsonify({"campaign": dst.name}), 200


@app.route("/menu/purge", methods=["POST"])
def menu_purge():
    """Delete a campaign from the trash for good. This cannot be undone."""
    if not _token_ok():
        return "Forbidden", 403
    if not _from_this_machine():
        return _THIS_MACHINE_ONLY
    data = request.get_json(force=True, silent=True) or {}
    entry = str(data.get("entry", ""))
    if entry not in {e["entry"] for e in _list_trash()}:
        return "not in the trash", 404
    import shutil
    try:
        shutil.rmtree(_trash_dir() / entry)
    except OSError as e:
        return f"could not delete: {e}", 500
    return "", 204


# ── Managing characters: each campaign's party, and the global roster (latest copy of every PC) ──
def _char_scopes() -> dict:
    """scope -> characters folder. Scope 'roster' is the global roster; every other scope is a campaign name."""
    from paths import characters_dir, campaigns_dir
    out = {"roster": characters_dir()}
    for c in _list_campaigns():
        out[c["name"]] = campaigns_dir() / c["name"] / "characters"
    return out


def _char_entry(scope: str, path) -> dict:
    import party as _party
    try:
        info = _party.parse_character(path.read_text(encoding="utf-8", errors="replace")) or {}
    except OSError:
        info = {}
    return {"scope": scope, "file": path.name, "name": info.get("name") or path.stem,
            "race": info.get("race", ""), "class": info.get("class", ""), "level": info.get("level"),
            "hp": info.get("hp"), "ac": info.get("ac")}


def _list_characters() -> dict:
    out = {"roster": [], "campaigns": []}
    for scope, folder in _char_scopes().items():
        try:
            files = sorted(f for f in folder.glob("*.md") if f.is_file())
        except OSError:
            files = []
        chars = [_char_entry(scope, f) for f in files]
        if scope == "roster":
            out["roster"] = chars
        else:
            out["campaigns"].append({"campaign": scope, "active": scope == _active_campaign_name(), "characters": chars})
    return out


def _char_trash_dir():
    from paths import characters_dir
    return characters_dir().parent / "character-trash"


def _list_char_trash() -> list[dict]:
    out = []
    try:
        files = [f for f in _char_trash_dir().glob("*.md") if f.is_file() and f.name.count("__") >= 2]
    except OSError:
        return []
    for f in files:
        scope, stem = f.stem.split("__")[0], "__".join(f.stem.split("__")[1:-1])
        try:
            when = f.stat().st_mtime
        except OSError:
            when = 0.0
        out.append({"entry": f.name, "scope": scope, "name": stem, "deleted": when})
    out.sort(key=lambda e: e["deleted"], reverse=True)
    return out


def _char_path(scope: str, file: str):
    """The real file for (scope, file), only if it is one the listing shows. None otherwise."""
    folder = _char_scopes().get(scope)
    if folder is None or not file.endswith(".md") or "/" in file or "\\" in file or ".." in file:
        return None
    p = folder / file
    return p if p.is_file() else None


@app.route("/characters")
def characters_page():
    return render_template("characters.html", lan_token=_lan_token or "")


@app.route("/characters/list")
def characters_list():
    return jsonify({**_list_characters(), "trash": _list_char_trash()})


@app.route("/characters/view")
def characters_view():
    p = _char_path(str(request.args.get("scope", "")), str(request.args.get("file", "")))
    if not p:
        return "unknown character", 404
    return jsonify({"text": p.read_text(encoding="utf-8", errors="replace")[:60000]})


def _guard_manage():
    if not _token_ok():
        return "Forbidden", 403
    if not _from_this_machine():
        return _THIS_MACHINE_ONLY
    return None


@app.route("/characters/delete", methods=["POST"])
def characters_delete():
    """Move a character file to the trash (it can be restored). From the open campaign it also leaves the sidebar."""
    bad = _guard_manage()
    if bad:
        return bad
    data = request.get_json(force=True, silent=True) or {}
    scope, file = str(data.get("scope", "")), str(data.get("file", ""))
    p = _char_path(scope, file)
    if not p:
        return "unknown character", 404
    import shutil
    name = _char_entry(scope, p)["name"]
    trash = _char_trash_dir()
    try:
        trash.mkdir(parents=True, exist_ok=True)
        dst = trash / f"{scope}__{p.stem}__{_time.strftime('%Y%m%d-%H%M%S')}.md"
        n = 1
        while dst.exists():
            dst = trash / f"{scope}__{p.stem}__{_time.strftime('%Y%m%d-%H%M%S')}-{n}.md"
            n += 1
        shutil.move(str(p), str(dst))
    except OSError as e:
        return f"could not delete: {e}", 500
    if scope != "roster" and scope == _active_campaign_name():
        with _stats_lock:
            _current_stats["players"] = [x for x in _current_stats.get("players", []) if x.get("name") != name]
            current = dict(_current_stats)
        _persist_stats()
        _broadcast({"stats": current})
    return jsonify({"entry": dst.name}), 200


@app.route("/characters/restore", methods=["POST"])
def characters_restore():
    """Put a deleted character back where it was (the roster if its campaign is gone; '-restored' if the name is taken)."""
    bad = _guard_manage()
    if bad:
        return bad
    data = request.get_json(force=True, silent=True) or {}
    entry = str(data.get("entry", ""))
    hit = next((e for e in _list_char_trash() if e["entry"] == entry), None)
    if not hit:
        return "not in the trash", 404
    import shutil
    scopes = _char_scopes()
    scope = hit["scope"] if hit["scope"] in scopes else "roster"
    folder = scopes[scope]
    stem = hit["name"]
    dst = folder / f"{stem}.md"
    n = 1
    while dst.exists():
        dst = folder / (f"{stem}-restored.md" if n == 1 else f"{stem}-restored-{n}.md")
        n += 1
    try:
        folder.mkdir(parents=True, exist_ok=True)
        shutil.move(str(_char_trash_dir() / entry), str(dst))
    except OSError as e:
        return f"could not restore: {e}", 500
    return jsonify({"scope": scope, "file": dst.name}), 200


@app.route("/characters/purge", methods=["POST"])
def characters_purge():
    """Delete a character from the trash for good. This cannot be undone."""
    bad = _guard_manage()
    if bad:
        return bad
    data = request.get_json(force=True, silent=True) or {}
    entry = str(data.get("entry", ""))
    if entry not in {e["entry"] for e in _list_char_trash()}:
        return "not in the trash", 404
    try:
        (_char_trash_dir() / entry).unlink()
    except OSError as e:
        return f"could not delete: {e}", 500
    return "", 204


@app.route("/characters/copy", methods=["POST"])
def characters_copy():
    """Add a roster character to a campaign's party (a copy; the roster keeps its own)."""
    bad = _guard_manage()
    if bad:
        return bad
    data = request.get_json(force=True, silent=True) or {}
    file, campaign = str(data.get("file", "")), str(data.get("campaign", ""))
    src = _char_path("roster", file)
    folder = _char_scopes().get(campaign) if campaign != "roster" else None
    if not src or folder is None:
        return "unknown character or campaign", 404
    dst = folder / file
    if dst.exists():
        return "That campaign already has a character with this name.", 409
    import shutil
    try:
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dst))
    except OSError as e:
        return f"could not copy: {e}", 500
    return jsonify({"scope": campaign, "file": file}), 200


def _clean_place(raw):
    """Where the party is, for the map card in the roleplay screen: a picture from the campaign's images folder,
    a pin on it (x, y as fractions 0-1 of the picture), an optional label, and an optional `view`: the part of the
    picture to show (fractions left/top/width/height), so a big map can be zoomed in on the party."""
    if not isinstance(raw, dict) or not raw:
        return None
    src = os.path.basename(str(raw.get("image", raw.get("src", ""))).replace("\\", "/"))
    if not src.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
        return None

    def _f(v, lo, hi):
        try:
            return round(max(lo, min(hi, float(v))), 4)
        except (TypeError, ValueError):
            return None

    x, y = _f(raw.get("x"), 0, 1), _f(raw.get("y"), 0, 1)
    if x is None or y is None:
        return None
    out = {"image": src, "x": x, "y": y, "label": str(raw.get("label", ""))[:60]}
    v = raw.get("view")
    if isinstance(v, dict):
        vx, vy = _f(v.get("x"), 0, 0.95), _f(v.get("y"), 0, 0.95)
        vw, vh = _f(v.get("w"), 0.05, 1), _f(v.get("h"), 0.05, 1)
        if None not in (vx, vy, vw, vh):
            out["view"] = {"x": vx, "y": vy, "w": round(min(vw, 1 - vx), 4), "h": round(min(vh, 1 - vy), 4)}
    return out


def _clean_map_image(raw, cols, rows, prev=None):
    """The picture under the grid: a file in the campaign's images folder, placed in grid squares.

    x, y = where its top-left corner sits (in squares), w, h = how many squares it spans. When the DM gives
    no placement, `placed` stays False and the page measures the picture's printed grid and reports the fit
    (POST /combat/image). A re-push of the same picture without a placement keeps the earlier fit.
    """
    if isinstance(raw, str):
        raw = {"src": raw}
    if not isinstance(raw, dict):
        return None
    src = os.path.basename(str(raw.get("src", "")).replace("\\", "/"))
    if not src.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
        return None

    def _f(v, lo, hi):
        try:
            return round(max(lo, min(hi, float(v))), 3)
        except (TypeError, ValueError):
            return None

    place = {k: _f(raw.get(k), lo, hi) for k, lo, hi in (("x", -60, 60), ("y", -60, 60), ("w", 1, 120), ("h", 1, 120))}
    # area = the part of the picture the battle happens in (fractions 0-1), for big maps of a whole dungeon
    area = None
    ra = raw.get("area")
    if isinstance(ra, dict):
        ax, ay = _f(ra.get("x"), 0, 0.98), _f(ra.get("y"), 0, 0.98)
        aw, ah = _f(ra.get("w"), 0.02, 1), _f(ra.get("h"), 0.02, 1)
        if None not in (ax, ay, aw, ah):
            area = {"x": ax, "y": ay, "w": min(aw, 1 - ax), "h": min(ah, 1 - ay)}
    placed = True
    if any(v is None for v in place.values()):
        if prev and prev.get("src") == src and prev.get("area") == area and prev.get("for") == [cols, rows]:
            place = {k: prev[k] for k in ("x", "y", "w", "h")}
            placed = bool(prev.get("placed"))
        else:
            a = area or {"x": 0, "y": 0, "w": 1, "h": 1}              # until measured, the area just covers the grid
            place = {"x": round(-a["x"] * cols / a["w"], 3), "y": round(-a["y"] * rows / a["h"], 3),
                     "w": round(cols / a["w"], 3), "h": round(rows / a["h"], 3)}
            placed = False
    out = {"src": src, **place, "placed": placed, "for": [cols, rows]}
    if area:
        out["area"] = area
    return out


# ── Battle map vocabulary ────────────────────────────────────────────────────
# What the ground looks like (the floor of the whole mat) and what stands on it. Every terrain kind has a look on
# the page AND a movement rule, so what the table sees is what the rules do: blocking things cannot be entered,
# difficult ground costs double, a bridge / door / road over anything else is plain ground.
_BIOMES = ("dungeon", "cave", "forest", "grassland", "road", "swamp", "snow", "desert", "tavern", "town",
           "ship", "temple", "sewer", "beach")
_BIOME_WORDS = (   # when the DM gives no biome, the map title decides (first match wins)
    ("sewer", ("sewer", "drain", "cistern")),
    ("ship", ("ship", "deck", "boat", "galley", "barge")),
    ("temple", ("temple", "shrine", "church", "chapel", "cathedral", "sanctum", "monastery")),
    ("tavern", ("tavern", "inn", "taproom", "tankard", "alehouse", "pub", "brewery", "cottage", "barracks", "lodge", "manor", "house", "hall", "shop", "library", "kitchen",
                "bedroom", "study", "roadhouse", "mansion", "castle room")),
    ("swamp", ("swamp", "marsh", "bog", "fen", "mire", "lizardfolk")),
    ("snow", ("snow", "ice", "icy", "tundra", "glacier", "frozen", "winter")),
    ("desert", ("desert", "dune", "wastes", "badlands")),
    ("beach", ("beach", "shore", "coast", "cove")),
    ("cave", ("cave", "cavern", "tunnel", "mine", "grotto", "hatchery", "underdark", "lair", "burrow")),
    ("forest", ("forest", "wood", "woods", "grove", "glade", "jungle", "thicket")),
    ("town", ("town", "street", "square", "market", "village", "city", "alley", "dock", "docks", "plaza", "greenest")),
    ("road", ("road", "path", "trail", "pass", "crossroads", "highway", "ambush")),
    ("grassland", ("field", "meadow", "plain", "plains", "hill", "hills", "farm", "camp", "pasture", "outskirts")),
    ("dungeon", ("dungeon", "crypt", "tomb", "cellar", "vault", "prison", "keep", "catacomb", "ruin", "ruins", "barrow")),
)
_TERRAIN_BLOCK = {"wall", "pillar", "table", "tree", "boulder", "crate", "barrel", "statue", "cliff", "pit",
                  "altar", "bed", "shelf", "wagon", "tent", "grate"}
_TERRAIN_SLOW = {"water", "difficult", "rubble", "bush", "mud", "ice", "sand", "snowdrift", "log", "deepwater", "brush", "web"}
_TERRAIN_CLEAR = {"door", "stairs", "fire", "bridge", "road", "rug"}      # plain ground; bridge/door/road also cancel what is under them
_TERRAIN_KINDS = _TERRAIN_BLOCK | _TERRAIN_SLOW | _TERRAIN_CLEAR


_BIOME_FROM_TERRAIN = (   # no biome and nothing in the title: what stands on the map tells
    ("forest", {"tree", "bush", "brush", "log"}), ("snow", {"snowdrift", "ice"}), ("swamp", {"mud", "deepwater"}),
    ("tavern", {"table", "bed", "shelf", "barrel", "rug"}), ("desert", {"sand"}), ("road", {"road", "wagon", "tent"}),
)


def _map_biome(m, terrain=(), extra: str = "") -> str:
    b = str(m.get("biome", "")).lower().strip()
    if b in _BIOMES:
        return b
    title = (str(m.get("title", "")) + " " + str(extra or "")).lower()     # the map title, else where the party's pin says they are
    words = set(re.findall(r"[a-z]+", title))
    for biome, keys in _BIOME_WORDS:
        if words & set(keys) or any(" " in k and k in title for k in keys):
            return biome
    kinds = [t["kind"] for t in terrain]
    best, score = None, 0
    for biome, ks in _BIOME_FROM_TERRAIN:
        n = sum(1 for k in kinds if k in ks)
        if n > score:
            best, score = biome, n
    return best or "dungeon"


def _furnish(biome: str, title: str, cols: int, rows: int, toks) -> list:
    """A drawn map the DM left (almost) bare still has to look like the place: lay out terrain that fits the setting.
    The layout depends only on the setting, title and size, so it stays the same push after push; pieces that would
    cover a token are left out, and blocking pieces that would cut someone off are removed again."""
    import random
    rnd = random.Random(f"{biome}|{title}|{cols}x{rows}")
    out = []

    def add(kind, x, y, w=1, h=1):
        if 0 <= x < cols and 0 <= y < rows:
            out.append({"kind": kind, "x": x, "y": y, "w": max(1, min(w, cols - x)), "h": max(1, min(h, rows - y))})

    def scatter(kind, share, w=1, h=1):
        for _ in range(max(1, int(cols * rows * share))):
            add(kind, rnd.randrange(cols), rnd.randrange(rows), w, h)

    def border(kind="wall", door=True):
        add(kind, 0, 0, cols, 1); add(kind, 0, rows - 1, cols, 1); add(kind, 0, 0, 1, rows); add(kind, cols - 1, 0, 1, rows)
        if door:
            add("door", cols // 2 - 1, rows - 1, 2, 1)

    mid = rows // 2
    if biome == "forest":
        scatter("tree", .09); scatter("bush", .05); scatter("brush", .02, 2, 1); add("log", rnd.randrange(cols - 2), rnd.randrange(rows), 2, 1); scatter("boulder", .01)
    elif biome == "grassland":
        scatter("bush", .04); scatter("boulder", .015); scatter("tree", .02); scatter("brush", .02, 2, 1)
    elif biome == "road":
        add("road", 0, mid - 1, cols, 2); scatter("tree", .05); scatter("bush", .04); add("wagon", cols // 2, mid - 1, 2, 1) if rnd.random() < .5 else None
    elif biome == "swamp":
        scatter("deepwater", .012, 3, 2); scatter("mud", .02, 2, 1); scatter("brush", .03); scatter("water", .02, 2, 1); add("log", rnd.randrange(cols - 2), rnd.randrange(rows), 2, 1)
    elif biome == "snow":
        scatter("snowdrift", .02, 2, 2); scatter("boulder", .025); scatter("ice", .01, 3, 2); scatter("tree", .02)
    elif biome == "desert":
        scatter("sand", .03, 3, 2); scatter("boulder", .04); scatter("brush", .02); scatter("cliff", .005, 3, 1)
    elif biome == "beach":
        add("water", 0, rows - 2, cols, 1); add("deepwater", 0, rows - 1, cols, 1); scatter("boulder", .02); scatter("log", .01, 2, 1)
    elif biome == "cave":
        border("wall", door=False); scatter("boulder", .04); scatter("rubble", .03, 2, 1); scatter("water", .008, 2, 2)
    elif biome == "tavern":
        border(); add("fire", cols - 2, 1); add("shelf", 1, 1, min(4, cols - 3), 1)
        for i in range(max(1, (cols - 4) // 4)):
            add("table", 2 + i * 4, 3, 2, 1); add("table", 2 + i * 4, rows - 4, 2, 1)
        add("barrel", cols - 2, rows - 2); add("barrel", cols - 3, rows - 2); add("rug", cols // 2 - 1, mid, 3, 1)
    elif biome == "town":
        add("wall", 0, 0, cols // 3, 2); add("wall", cols - cols // 3, 0, cols // 3, 2); add("wall", 0, rows - 2, cols // 4, 2)
        scatter("crate", .02); scatter("barrel", .02); add("wagon", cols // 2, mid, 2, 1)
    elif biome == "ship":
        add("wall", 0, 0, cols, 1); add("wall", 0, rows - 1, cols, 1); add("pillar", cols // 2, mid); scatter("crate", .025); scatter("barrel", .02); add("stairs", 2, mid, 1, 2)
    elif biome == "temple":
        border(); add("altar", cols // 2 - 1, 1, 2, 1); add("statue", 1, 1); add("statue", cols - 2, 1)
        for x in range(3, cols - 3, 3):
            add("pillar", x, 2); add("pillar", x, rows - 3)
        add("rug", cols // 2 - 1, 2, 2, rows - 4)
    elif biome == "sewer":
        add("wall", 0, 0, cols, 1); add("wall", 0, rows - 1, cols, 1); add("water", 0, mid, cols, 1); add("bridge", cols // 2, mid, 2, 1); scatter("rubble", .03)
    else:   # dungeon
        border(); scatter("pillar", .015); scatter("rubble", .03, 2, 1); add("stairs", 1, 1)

    occupied = {(t["x"] + i, t["y"] + j) for t in toks for i in range(t.get("size") or 1) for j in range(t.get("size") or 1)}
    out = [t for t in out if t["kind"] in _TERRAIN_CLEAR or t["w"] == cols or t["h"] == rows      # outer walls stay (tokens step aside)
           or not any((x, y) in occupied for x in range(t["x"], t["x"] + t["w"]) for y in range(t["y"], t["y"] + t["h"]))]

    def reachable(terrain):
        blocked = set()
        for t in terrain:
            if t["kind"] in _TERRAIN_BLOCK:
                blocked |= {(x, y) for x in range(t["x"], t["x"] + t["w"]) for y in range(t["y"], t["y"] + t["h"])}
        for t in terrain:
            if t["kind"] in ("bridge", "door", "road"):
                blocked -= {(x, y) for x in range(t["x"], t["x"] + t["w"]) for y in range(t["y"], t["y"] + t["h"])}
        starts = [(t["x"], t["y"]) for t in toks]
        if len(starts) < 2:
            return True
        seen, q = {starts[0]}, [starts[0]]
        while q:
            cx, cy = q.pop()
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                n = (cx + dx, cy + dy)
                if n not in seen and 0 <= n[0] < cols and 0 <= n[1] < rows and n not in blocked:
                    seen.add(n); q.append(n)
        return all(s in seen for s in starts)

    while not reachable(out):                                   # nobody may be walled in: drop blocking pieces until all can meet
        idx = [i for i, t in enumerate(out) if t["kind"] in _TERRAIN_BLOCK and not (biome in ("tavern", "temple", "dungeon", "cave") and (t["w"] == cols or t["h"] == rows))]
        if not idx:
            idx = [i for i, t in enumerate(out) if t["kind"] in _TERRAIN_BLOCK]
        if not idx:
            break
        out.pop(rnd.choice(idx))
    return out


def _terrain_cells(terrain, cols, rows, fly=False):
    """(blocked, slow) squares. A bridge, door or road clears what is under it. Flyers only stop at walls."""
    blocked, slow, clear = set(), set(), set()
    for t in terrain:
        cells = {(x, y) for x in range(t["x"], t["x"] + t["w"]) for y in range(t["y"], t["y"] + t["h"])}
        k = t["kind"]
        if fly:
            if k in ("wall", "cliff", "pillar", "shelf"):
                blocked |= cells
        elif k in _TERRAIN_BLOCK:
            blocked |= cells
        elif k in _TERRAIN_SLOW:
            slow |= cells
        if k in ("bridge", "door", "road"):
            clear |= cells
    return blocked - clear, slow - clear


def _keep_moves_honest(toks, prev_map, terrain, cols, rows) -> None:
    """A creature that was already on this map can only have walked where the map lets it: around walls and trees,
    difficult ground counting double, at most twice its speed in one round (a Dash). A move that is not possible
    stops at the reachable square nearest to where it was sent. `fly: true` creatures only stop at walls;
    `teleport: true` on a push skips the check for that one move (Misty Step, a portal)."""
    if not prev_map or prev_map.get("cols") != cols or prev_map.get("rows") != rows:
        return
    before = {t["name"]: (t["x"], t["y"]) for t in prev_map.get("tokens", []) if isinstance(t, dict)}
    for tk in toks:
        old = before.get(tk["name"])
        if not old or old == (tk["x"], tk["y"]) or tk.pop("teleport", False):
            continue
        blocked, slow = _terrain_cells(terrain, cols, rows, fly=bool(tk.get("fly")))
        limit = 2 * max(5, tk.get("speed") or 30) // 5                  # squares, Dash included
        cost, q = {old: 0}, [old]
        while q:
            cx, cy = q.pop(0)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    n = (cx + dx, cy + dy)
                    if n == (cx, cy) or not (0 <= n[0] < cols and 0 <= n[1] < rows) or n in blocked:
                        continue
                    c = cost[(cx, cy)] + (2 if n in slow else 1)
                    if c <= limit and c < cost.get(n, 1e9):
                        cost[n] = c
                        q.append(n)
        target = (tk["x"], tk["y"])
        if target not in cost:
            best = min(cost, key=lambda c: (max(abs(c[0] - target[0]), abs(c[1] - target[1])), cost[c]))
            tk["x"], tk["y"] = best


def _picture_void(image, cols, rows) -> set:
    """Squares of the grid the published picture does not cover: nothing can stand there."""
    if not image:
        return set()
    x0, y0, x1, y1 = image["x"], image["y"], image["x"] + image["w"], image["y"] + image["h"]
    return {(x, y) for x in range(cols) for y in range(rows) if x + 0.5 < x0 or x + 0.5 > x1 or y + 0.5 < y0 or y + 0.5 > y1}


def _place_tokens_sensibly(toks, terrain, cols, rows, void=()) -> None:
    """Nobody stands inside a wall, tree or boulder, or on top of somebody else: such a token moves to the nearest free
    square (walking distance), so what the map shows is always possible."""
    cell_kinds = {}
    for t in terrain:
        for x in range(t["x"], t["x"] + t["w"]):
            for y in range(t["y"], t["y"] + t["h"]):
                cell_kinds.setdefault((x, y), set()).add(t["kind"])
    blocked = {c for c, ks in cell_kinds.items() if (ks & _TERRAIN_BLOCK) and not (ks & {"bridge", "door", "road"})}
    blocked |= set(void)
    taken = set()

    def fits(x, y, sz, share=False):     # a swarm may share a square with another creature (5e), never with a wall
        cells = [(x + i, y + j) for i in range(sz) for j in range(sz)]
        return all(0 <= a < cols and 0 <= b < rows and (a, b) not in blocked and (share or (a, b) not in taken) for a, b in cells)

    for tk in toks:
        sz = tk.get("size") or 1
        share = bool(tk.get("swarm"))
        if not fits(tk["x"], tk["y"], sz, share):
            seen, q, spot = {(tk["x"], tk["y"])}, [(tk["x"], tk["y"])], None
            while q and spot is None:
                cx, cy = q.pop(0)
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1)):
                    nx, ny = cx + dx, cy + dy
                    if (nx, ny) in seen or not (0 <= nx < cols and 0 <= ny < rows):
                        continue
                    seen.add((nx, ny))
                    if fits(nx, ny, sz, share):
                        spot = (nx, ny)
                        break
                    q.append((nx, ny))
            if spot:
                tk["x"], tk["y"] = spot
        if share:
            continue                         # a swarm does not keep others out of its square either
        for i in range(sz):
            for j in range(sz):
                taken.add((tk["x"] + i, tk["y"] + j))


def _clean_combat(raw, prev=None, place_label: str = ""):
    """Keep only what the combat screen draws: enemies, and a map with terrain and tokens."""
    if not isinstance(raw, dict) or not raw:
        return None

    def _n(v, lo=0, hi=999):
        try:
            return max(lo, min(hi, int(v)))
        except (TypeError, ValueError):
            return None

    out = {"enemies": [], "map": None}
    for e in (raw.get("enemies") or [])[:24]:
        if isinstance(e, dict) and e.get("name"):
            out["enemies"].append({"name": str(e["name"])[:40], "hp": _n(e.get("hp")), "max": _n(e.get("max")),
                                   "note": str(e.get("note", ""))[:40]})
    m = raw.get("map")
    if isinstance(m, dict):
        cols = _n(m.get("cols", 12), 4, 26) or 12
        rows = _n(m.get("rows", 8), 4, 26) or 8
        toks = []
        for tk in (m.get("tokens") or [])[:60]:
            if not (isinstance(tk, dict) and tk.get("name")):
                continue
            x, y = _n(tk.get("x", 0), -1, 99), _n(tk.get("y", 0), -1, 99)
            if x is None or y is None or not (0 <= x < cols and 0 <= y < rows):
                continue
            conds = [str(c)[:16] for c in (tk.get("conditions") or []) if c][:4] if isinstance(tk.get("conditions"), list) else []
            toks.append({"name": str(tk["name"])[:40], "x": x, "y": y,
                         "side": "enemy" if tk.get("side") == "enemy" else "pc",
                         "size": _n(tk.get("size", 1), 1, 3) or 1,
                         "speed": _n(tk.get("speed"), 0, 200), "conditions": conds,
                         **({"fly": True} if tk.get("fly") else {}), **({"swarm": True} if tk.get("swarm") else {}),
                         **({"teleport": True} if tk.get("teleport") else {})})
        terrain = []
        for tr in (m.get("terrain") or [])[:300]:
            if not isinstance(tr, dict):
                continue
            kind = str(tr.get("kind", "")).lower().replace(" ", "").replace("_", "")
            kind = {"rock": "boulder", "rocks": "boulder", "trees": "tree", "bushes": "bush", "undergrowth": "brush",
                    "barrels": "barrel", "crates": "crate", "column": "pillar", "chasm": "pit", "river": "water",
                    "lake": "deepwater", "shallowwater": "water", "carpet": "rug", "bookshelf": "shelf",
                    "bookcase": "shelf", "snow": "snowdrift", "webs": "web", "cobweb": "web", "cobwebs": "web",
                    "bars": "grate", "portcullis": "grate", "gate": "grate"}.get(kind, kind)
            if kind not in _TERRAIN_KINDS:
                continue
            x, y = _n(tr.get("x"), 0, 99), _n(tr.get("y"), 0, 99)
            if x is None or y is None or x >= cols or y >= rows:
                continue
            w = _n(tr.get("w", 1), 1, 26) or 1
            h = _n(tr.get("h", 1), 1, 26) or 1
            terrain.append({"kind": kind, "x": x, "y": y, "w": min(w, cols - x), "h": min(h, rows - y)})
        # A move a player planned but has not sent yet (arrow on the map) survives a DM push that left the token where it was.
        prev_toks = {t["name"]: t for t in (((prev or {}).get("map") or {}).get("tokens") or []) if isinstance(t, dict)}
        for t in toks:
            old = prev_toks.get(t["name"])
            if old and old.get("plan") and (old.get("x"), old.get("y")) == (t["x"], t["y"]):
                pl = old["plan"]
                if 0 <= pl.get("x", -1) < cols and 0 <= pl.get("y", -1) < rows:
                    t["plan"] = dict(pl)
        title = str(m.get("title", ""))[:60]
        biome = _map_biome(m, terrain, place_label)
        furnished = False
        if len(terrain) < 3 and not m.get("image"):            # a bare drawn map would not look like any place: furnish it
            terrain = terrain + _furnish(biome, title or place_label, cols, rows, toks)
            furnished = True
        prev_map = (prev or {}).get("map") or {}
        _keep_moves_honest(toks, prev_map if prev_map.get("title", "") == title else None, terrain, cols, rows)
        img = _clean_map_image(m.get("image"), cols, rows, prev_map.get("image"))
        _place_tokens_sensibly(toks, terrain, cols, rows, _picture_void(img, cols, rows))
        for tk in toks:
            tk.pop("teleport", None)
        out["map"] = {"cols": cols, "rows": rows, "title": title,
                      "biome": biome, "tokens": toks, "terrain": terrain}
        if furnished:
            out["map"]["furnished"] = True
        if img:
            out["map"]["image"] = img
    return out


def _cell_label(x: int, y: int) -> str:
    return f"{chr(ord('A') + x)}{y + 1}"


@app.route("/combat/move", methods=["POST"])
def combat_move():
    """A token was dragged on the map: remember it and tell Claude (it is the DM's call what that means)."""
    if not _token_ok():
        return "Forbidden", 403
    data = request.get_json(force=True, silent=True) or {}
    name = str(data.get("name", ""))
    try:
        x, y = int(data.get("x")), int(data.get("y"))
    except (TypeError, ValueError):
        return "bad position", 400
    with _stats_lock:
        combat = _current_stats.get("combat") or {}
        m = combat.get("map")
        tok = next((t for t in (m or {}).get("tokens", []) if t["name"] == name), None)
        if not tok or not (0 <= x < m["cols"] and 0 <= y < m["rows"]):
            return "unknown token or cell", 404
        old = _cell_label(tok["x"], tok["y"])
        tok["x"], tok["y"] = x, y
        current = dict(_current_stats)
    try:
        with _staged_lock, open(QUEUE_FILE, "a", encoding="utf-8") as f:
            f.write(f"[Map]: {name} moved from {old} to {_cell_label(x, y)}\n")
    except OSError:
        pass
    _persist_stats()
    _broadcast({"stats": current})
    return "", 204


@app.route("/combat/plan", methods=["POST"])
def combat_plan():
    """A player dragged a token: a PLANNED move (an arrow on every screen) until GO sends the turn to the DM.
    Body {name, x, y, ft} plans or re-plans; {name, cancel: true} (or dropping it back on its own square) cancels."""
    if not _token_ok():
        return "Forbidden", 403
    data = request.get_json(force=True, silent=True) or {}
    name = str(data.get("name", ""))
    with _stats_lock:
        m = (_current_stats.get("combat") or {}).get("map")
        tok = next((t for t in (m or {}).get("tokens", []) if t["name"] == name), None)
        if not tok:
            return "unknown token", 404
        if data.get("cancel"):
            tok.pop("plan", None)
        else:
            try:
                x, y = int(data.get("x")), int(data.get("y"))
            except (TypeError, ValueError):
                return "bad position", 400
            if not (0 <= x < m["cols"] and 0 <= y < m["rows"]):
                return "off the map", 400
            probe = [{**t, "x": t["x"], "y": t["y"]} for t in m.get("tokens", []) if t is not tok] + [{**tok, "x": x, "y": y}]
            _place_tokens_sensibly(probe, m.get("terrain", []), m["cols"], m["rows"], _picture_void(m.get("image"), m["cols"], m["rows"]))
            if (probe[-1]["x"], probe[-1]["y"]) != (x, y):
                return "that square is blocked", 409           # a wall, tree, boulder... or somebody already stands there
            if (x, y) == (tok["x"], tok["y"]):
                tok.pop("plan", None)
            else:
                try:
                    ft = max(0, min(999, int(data.get("ft") or 0)))
                except (TypeError, ValueError):
                    ft = 0
                tok["plan"] = {"x": x, "y": y, "ft": ft}
        current = dict(_current_stats)
    _persist_stats()
    _broadcast({"stats": current})
    return "", 204


def _commit_planned_moves() -> bool:
    """GO was pressed: planned moves become real positions (the text sent to the DM already says where they went)."""
    changed = False
    with _stats_lock:
        m = (_current_stats.get("combat") or {}).get("map")
        for t in (m or {}).get("tokens", []):
            pl = t.pop("plan", None)
            if pl:
                t["x"], t["y"] = pl["x"], pl["y"]
                changed = True
        current = dict(_current_stats)
    if changed:
        _persist_stats()
        _broadcast({"stats": current})
    return changed


@app.route("/combat/image", methods=["POST"])
def combat_image():
    """A screen measured the map picture's printed grid and reports where it sits. Accepted once per picture:
    only while the DM has not given a placement and nothing was reported yet (the first screen wins)."""
    if not _token_ok():
        return "Forbidden", 403
    data = request.get_json(force=True, silent=True) or {}
    with _stats_lock:
        m = (_current_stats.get("combat") or {}).get("map")
        cur = (m or {}).get("image")
        if not cur:
            return "no map picture", 404
        if cur.get("placed"):
            return "", 204
        if data.get("measured") is False:            # no printed grid found: keep the area-covers-grid fit, stop asking
            cur["placed"] = True
        else:
            m["image"] = _clean_map_image({"src": cur["src"], "area": cur.get("area"), **{k: data.get(k) for k in ("x", "y", "w", "h")}},
                                          m["cols"], m["rows"], cur) or cur
        current = dict(_current_stats)
    _persist_stats()
    _broadcast({"stats": current})
    return "", 204


def _tts_engine_label() -> str:
    """Name of the voice engine actually in use, for the line under each narration block."""
    try:
        return "Kokoro TTS" if _tts is not None and _tts._engine() == "kokoro" else "Pocket TTS"
    except Exception:
        return "Pocket TTS"


@app.route("/menu/exit", methods=["POST"])
def menu_exit():
    """Save and exit: ask Claude to save, and send every screen back to the Main Menu.

    The display keeps running (Quit is what stops it). The campaign stays the
    active one, so 'Continue' on the menu picks it up again.
    """
    if not _token_ok():
        return "Forbidden", 403
    if not _from_this_machine():
        return _THIS_MACHINE_ONLY
    global _MENU_FIRST, _exit_requested_at, _story_live
    _story_live = False
    asked_claude = _dm_listening()
    if asked_claude:
        try:
            with _staged_lock, open(QUEUE_FILE, "a", encoding="utf-8") as f:
                f.write("[Menu]: save-exit\n")
        except OSError:
            asked_claude = False
    _exit_requested_at = _time.time() if asked_claude else None
    _MENU_FIRST = True
    _broadcast({"relay": {"t": "go-menu"}})        # every screen, the TV included
    return jsonify({"asked_claude": asked_claude}), 200


# Quit: tell Claude first (so it saves and stops waiting), then stop the display
# and the local voice. The pause gives Claude's wait loop time to pick the line
# up and acknowledge it before the display it polls goes away. The two names
# below exist so tests can swap them; nothing else should.
_QUIT_DELAY = 3.0
_exit_display = os._exit


@app.route("/menu/quit", methods=["POST"])
def menu_quit():
    """Stop the display (and the local voice) after telling Claude to wrap up."""
    if not _token_ok():
        return "Forbidden", 403
    if not _from_this_machine():
        return _THIS_MACHINE_ONLY
    try:
        with _staged_lock, open(QUEUE_FILE, "a", encoding="utf-8") as f:
            f.write("[Menu]: quit\n")
    except OSError:
        pass   # Claude may not be waiting; the display still stops

    def _stop():
        import time as _t
        _t.sleep(_QUIT_DELAY)
        try:
            if _tts is not None:
                _tts.stop_voice()
        finally:
            _exit_display(0)

    threading.Thread(target=_stop, daemon=True).start()
    return "", 204


# ─── New campaign (reached from the Main Menu) ───────────────────────────────
# Two ways in: the Dungeon Master creates one from a short form, or the player
# uploads a published adventure. Both queue a "[Menu]:" line for Claude, like
# /menu/load does. Choices mirror the `/dm:dnd new` wizard in SKILL-commands.md.

_NEW_CHOICES = {
    "tone": ["grimdark", "dark fantasy", "heroic", "horror", "political", "swashbuckling", "cosmic"],
    "magic": ["none", "low", "medium", "high"],
    "setting": ["medieval", "renaissance", "ancient", "nautical", "underground"],
    "danger": ["lethal", "gritty", "standard", "heroic"],
    "ruleset": ["2014", "2024"],
}
_NEW_DEFAULTS = {"tone": "heroic", "magic": "medium", "setting": "medieval",
                 "danger": "standard", "players": 4, "level": 1}
_IMPORT_EXTENSIONS = (".pdf", ".docx", ".md", ".markdown", ".txt")
_IMPORT_MAX_BYTES = 100 * 1024 * 1024
_CAMPAIGN_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")


def _campaign_slug(raw: str) -> str:
    """'The Lost Mine!' -> 'the-lost-mine'. Empty when nothing usable is left."""
    text = unicodedata.normalize("NFKD", str(raw)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48].strip("-")


def _menu_new_guard():
    """None when the caller may use the new-campaign routes, else the error response."""
    if not _token_ok():
        return "Forbidden", 403
    if not _from_this_machine():
        return _THIS_MACHINE_ONLY
    return None


def _queue_menu_line(line: str) -> bool:
    try:
        with _staged_lock, open(QUEUE_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        return True
    except OSError:
        return False


def _new_campaign_name(raw: str):
    """(slug, error). The slug is checked against the campaigns already on disk."""
    slug = _campaign_slug(raw)
    if not slug or not _CAMPAIGN_SLUG.match(slug):
        return "", "Give the campaign a name (letters and numbers)."
    if slug in {c["name"] for c in _list_campaigns()}:
        return "", f"A campaign called '{slug}' already exists. Pick another name."
    return slug, ""


@app.route("/menu/new-options")
def menu_new_options():
    default_ruleset = "2014"
    try:
        import settings as _settings
        default_ruleset = str(_settings.load().get("default_ruleset", "2014"))
    except Exception:
        pass
    return jsonify({**_NEW_CHOICES, "defaults": {**_NEW_DEFAULTS, "ruleset": default_ruleset},
                    "extensions": list(_IMPORT_EXTENSIONS),
                    "max_mb": _IMPORT_MAX_BYTES // (1024 * 1024)})


@app.route("/menu/new", methods=["POST"])
def menu_new():
    """Ask the Dungeon Master to create a campaign from the form's answers."""
    denied = _menu_new_guard()
    if denied:
        return denied
    data = request.get_json(force=True, silent=True)
    if not isinstance(data, dict):
        return jsonify(error="That did not arrive as a form. Try again."), 400
    slug, err = _new_campaign_name(data.get("name", ""))
    if err:
        return jsonify(error=err), 400
    premise = " ".join(str(data.get("premise", "")).split())
    if len(premise) > 300:
        return jsonify(error="Keep the theme to 300 characters or fewer."), 400
    answers: dict = {"name": slug, "premise": premise}
    for key in ("tone", "magic", "setting", "danger", "ruleset"):
        value = str(data.get(key, "")).strip()
        if value and value not in _NEW_CHOICES[key]:
            return jsonify(error=f"'{value}' is not a choice for {key}."), 400
        answers[key] = value        # blank means: the Dungeon Master picks
    for key, low, high in (("players", 1, 8), ("level", 1, 20)):
        try:
            given = data.get(key)
            number = _NEW_DEFAULTS[key] if given in (None, "") else int(given)
        except (TypeError, ValueError):
            return jsonify(error=f"{key.capitalize()} must be a number."), 400
        if not low <= number <= high:
            return jsonify(error=f"{key.capitalize()} must be between {low} and {high}."), 400
        answers[key] = number
    if not _queue_menu_line("[Menu]: new " + json.dumps(answers, ensure_ascii=False)):
        return jsonify(error="Could not reach the Dungeon Master's queue."), 500
    return jsonify(ok=True, campaign=slug), 200


def _safe_upload_name(filename: str):
    """A file name made only of letters, digits, dot, dash, underscore; '' if the type is not allowed."""
    base = os.path.basename(str(filename).replace("\\", "/"))
    stem, ext = os.path.splitext(base)
    ext = ext.lower()
    if ext not in _IMPORT_EXTENSIONS:
        return ""
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", unicodedata.normalize("NFKD", stem).encode("ascii", "ignore").decode())
    return (stem.strip("-")[:60] or "adventure") + ext


@app.route("/menu/import", methods=["POST"])
def menu_import():
    """Save an uploaded adventure under the runtime uploads folder and ask Claude to import it."""
    denied = _menu_new_guard()
    if denied:
        return denied
    if (request.content_length or 0) > _IMPORT_MAX_BYTES + 1024 * 1024:
        return jsonify(error=f"That file is too big (limit {_IMPORT_MAX_BYTES // (1024 * 1024)} MB)."), 413
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return jsonify(error="Choose a file to import."), 400
    safe = _safe_upload_name(upload.filename)
    if not safe:
        return jsonify(error="That file type is not supported. Use PDF, DOCX, MD or TXT."), 400
    name = ""
    if str(request.form.get("name", "")).strip():
        name, err = _new_campaign_name(request.form.get("name", ""))
        if err:
            return jsonify(error=err), 400
    folder = rt("uploads")
    os.makedirs(folder, exist_ok=True)
    target = os.path.join(folder, f"{int(_time.time())}-{safe}")
    size = 0
    try:
        with open(target, "wb") as out:          # saved as data only; never executed
            while True:
                chunk = upload.stream.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > _IMPORT_MAX_BYTES:
                    raise ValueError("too big")
                out.write(chunk)
    except ValueError:
        os.remove(target)
        return jsonify(error=f"That file is too big (limit {_IMPORT_MAX_BYTES // (1024 * 1024)} MB)."), 413
    except OSError:
        return jsonify(error="Could not save the file."), 500
    if size == 0:
        os.remove(target)
        return jsonify(error="That file is empty."), 400
    line = f"[Menu]: import {target}" + (f" {name}" if name else "")
    if not _queue_menu_line(line):
        return jsonify(error="Could not reach the Dungeon Master's queue."), 500
    return jsonify(ok=True, campaign=name or None), 200


# ─── Settings (reached from the Main Menu) ───────────────────────────────────

@app.route("/settings")
def settings_page():
    return render_template("settings.html", lan_token=_lan_token or "")


@app.route("/settings/data")
def settings_data():
    import settings as _settings
    camps = []
    for c in _list_campaigns():
        camps.append({"name": c["name"],
                      "characters": [{"name": n, "experience": e}
                                     for n, e in _settings.load_experience(c["name"]).items()]})
    return jsonify({"settings": _settings.load(),
                    "options": {"flavour": list(_settings.FLAVOUR_WORDS),
                                "combat": list(_settings.COMBAT_LEVELS),
                                "default_ruleset": list(_settings.RULESETS),
                                "experience": list(_settings.EXPERIENCE_LEVELS),
                                "theme": list(_settings.THEMES),
                                "skin": list(_settings.SKINS),
                                "type_speed": list(_settings.TYPE_SPEEDS),
                                "narrator_voice": list(_settings.NARRATOR_VOICES),
                                "text_size": {"min": _settings.TEXT_SIZE_MIN,
                                              "max": _settings.TEXT_SIZE_MAX,
                                              "step": _settings.TEXT_SIZE_STEP},
                                "ui_size": {"min": _settings.UI_SIZE_MIN,
                                            "max": _settings.UI_SIZE_MAX,
                                            "step": _settings.UI_SIZE_STEP}},
                    "campaigns": camps})


@app.route("/settings/save", methods=["POST"])
def settings_save():
    if not _token_ok():
        return "Forbidden", 403
    if not _from_this_machine():
        return _THIS_MACHINE_ONLY
    import settings as _settings
    data = request.get_json(force=True, silent=True) or {}
    try:
        saved = _settings.save(data)
    except ValueError as e:
        return str(e), 400
    if "flavour" in data:
        # One source of truth for reply length: the narration target the DM
        # already reads. The in-game slider can still override it live.
        try:
            os.makedirs(os.path.dirname(rt("narration_target")), exist_ok=True)
            with open(rt("narration_target"), "w", encoding="utf-8") as f:
                f.write(str(_settings.FLAVOUR_WORDS[saved["flavour"]]))
        except OSError:
            pass
    if "sound_effects" in data and _audio:
        try:
            _audio.set_sfx(bool(saved["sound_effects"]))
        except Exception:
            pass
    shown = _settings.display_settings()
    if any(k in data for k in shown):
        _broadcast({"display_settings": shown})    # every screen follows at once
    return jsonify(saved)


VOICE_SAMPLE_TEXT = ("The torchlight gutters as you descend the crumbling stairs. "
                     "Somewhere below, water drips in slow rhythm, and something inside breathes.")


@app.route("/settings/voice-sample", methods=["POST"])
def settings_voice_sample():
    """The same short narration in one narrator voice, as a WAV, for the Settings
    page to play so the voices can be compared. Body: {"voice": "am_adam"}."""
    import settings as _settings
    if _tts is None:
        return "TTS module unavailable", 503
    if not _token_ok():
        return "Forbidden", 403
    if not _rate_ok(request.remote_addr or "?"):
        return "Rate limited", 429
    data = request.get_json(force=True, silent=True) or {}
    voice = data.get("voice")
    if voice not in _settings.NARRATOR_VOICES:
        return "unknown voice", 400
    if not _tts.available():
        return "Local TTS is not installed (see README: Spoken narration)", 503
    if _tts._engine() != "kokoro":
        return "These voices need the Kokoro engine (not installed)", 503
    try:
        pcm = _tts.synthesize_strict(VOICE_SAMPLE_TEXT, "en", voice)
    except _tts.TtsError as e:
        return f"Local TTS failed: {e}", 502
    import io, wave
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(pcm)
    return Response(buf.getvalue(), mimetype="audio/wav", headers={"Cache-Control": "no-store"})


@app.route("/settings/experience", methods=["POST"])
def settings_experience():
    if not _token_ok():
        return "Forbidden", 403
    if not _from_this_machine():
        return _THIS_MACHINE_ONLY
    import settings as _settings
    data = request.get_json(force=True, silent=True) or {}
    campaign = str(data.get("campaign", ""))
    if campaign not in {c["name"] for c in _list_campaigns()}:
        return "unknown campaign", 404
    try:
        _settings.save_experience(campaign, str(data.get("character", "")),
                                  str(data.get("experience", "")))
    except ValueError as e:
        return str(e), 400
    return "", 204


@app.route("/icons/<path:filename>")
def serve_icon(filename):
    """Serve icons, favicon, and brand assets from display/icons/."""
    _icons_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")
    return send_from_directory(_icons_dir, filename)


@app.route("/favicon.ico")
def favicon():
    _icons_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")
    return send_from_directory(_icons_dir, "favicon.ico",
                               mimetype="image/vnd.microsoft.icon")


# --- Dutch explanations: names stay English, the explanation is in the campaign language ---
_NL_FILE = os.path.join(os.path.expanduser("~"), ".claude", "dnd", "tools", "srd_nl.json")
_nl_cache = {"mtime": None, "data": {}}


def _explain_dutch_on() -> bool:
    """The Settings menu's "Explain in Dutch": an addition to the English, never a replacement."""
    try:
        import settings as _settings
        return bool(_settings.load()["explain_dutch"])
    except Exception:
        return False


def _nl_entry(name, category):
    try:
        mt = os.path.getmtime(_NL_FILE)
        if _nl_cache["mtime"] != mt:
            _nl_cache["data"] = json.load(open(_NL_FILE, encoding="utf-8"))
            _nl_cache["mtime"] = mt
    except Exception:
        return None
    d = _nl_cache["data"]
    key = re.sub(r"\s*\(.*?\)\s*$", "", name.strip().lower())
    if category in ("item", "equipment", "magic_item") and (key + " (item)") in d:
        return d[key + " (item)"]
    return d.get(key)


def _nl_text(name, e):
    title = name + ("  [" + e["title_suffix"] + "]" if e.get("title_suffix") else "")
    meta = e.get("meta") or ["Type: " + str(e.get("cat", "")).title()]
    return "## " + title + "\n\n" + "\n".join(meta) + "\n\n" + e.get("desc", "")


@app.route("/srd-lookup")
def srd_lookup():
    """Look up a spell, item, condition, feature, or monster by name.

    Query params:
        name      — the name to look up (required)
        category  — spell | item | equipment | magic_item | condition | monster | feature (optional)
        level     — character level (1–20); collapses scale progressions to the matching entry

    Returns JSON: {"found": bool, "name": str, "category": str, "text": str}
    """
    name     = request.args.get("name", "").strip()[:120]
    category = request.args.get("category", "").strip().lower() or None
    level_s  = request.args.get("level", "").strip()
    level    = int(level_s) if level_s.isdigit() and 1 <= int(level_s) <= 20 else None
    if not name:
        return jsonify({"found": False, "error": "name required"}), 400
    if not _SRD_AVAILABLE or _lookup is None:
        return jsonify({"found": False, "error": "SRD dataset not loaded"}), 503

    text = _lookup.lookup_with_level(name, category=category, level=level)
    if text and _explain_dutch_on():
        # English first, always; the Dutch explanation goes underneath when there is one.
        _e = _nl_entry(name, category)
        if _e:
            text += "\n\n## Nederlands\n\n" + "\n".join(_e.get("meta") or []) + "\n\n" + _e.get("desc", "")
    if text:
        rec = _lookup.lookup_record(name, category=category)
        resolved_cat = (rec or {}).get("_cat", category or "")
        return jsonify({"found": True, "name": name, "category": resolved_cat, "text": text})
    # Not found — offer near-miss "did you mean?" suggestions (typo recovery)
    # plus the wikidot fallback URL so the frontend can still link out.
    # `ref` is {} when there is no VERIFIED destination for this category, and
    # the frontend renders no link at all in that case: a guessed URL reads as
    # an answer and dead-ends, which is worse than saying nothing.
    ref = _lookup.reference_url(name, category=category)
    suggestions = []
    try:
        for sg_name, sg_cat in _lookup.suggest(name, category=category, n=3):
            suggestions.append({"name": sg_name, "category": sg_cat})
    except Exception:
        pass  # suggestion is best-effort; never fail the lookup over it
    return jsonify({"found": False, "name": name,
                    "reference_url": ref.get("url", ""),
                    "reference_label": ref.get("label", ""),
                    # kept so an older cached frontend still gets a link
                    "wikidot_url": ref.get("url", ""),
                    "suggestions": suggestions})


@app.route("/ping")
def ping():
    return "ok", 200


@app.route("/health")
def health():
    """Server-side integrity probe used by send.py --verify and external monitors.

    Returns the live counts the send-side cares about:
      - alive: always True if the route runs
      - tail_buffer: number of entries currently in the rolling tail
      - tail_file_size: size in bytes of the on-disk session_tail.json
      - text_log: number of entries in the replay log
      - campaign: the active campaign name (empty if none set)
      - clients: connected SSE clients

    No auth required — this is a liveness/monitoring endpoint, no PII or game
    content is exposed.
    """
    try:
        camp = open(CAMP_FILE, encoding="utf-8").read().strip()
    except Exception:
        camp = ""
    tail_path = _get_tail_file()
    try:
        tail_size = os.path.getsize(tail_path) if tail_path and os.path.exists(tail_path) else 0
    except OSError:
        tail_size = 0
    with _tail_lock:
        tail_count = len(_tail_buffer)
    with _text_log_lock:
        log_count = len(_text_log)
    with _clients_lock:
        client_count = len(_clients)
    return {
        "alive": True,
        "tail_buffer": tail_count,
        "tail_file_size": tail_size,
        "tail_path": tail_path or "",
        "text_log": log_count,
        "campaign": camp,
        "clients": client_count,
    }, 200


# ── How fast the DM answers ──────────────────────────────────────────────────
# From the moment the players' GO reaches the DM's queue to the first block the DM sends back, and to its narration.
# Every turn is written to latency.jsonl in the runtime folder (scripts/latency.py reports on it) and shown on screen.
_turn_clock = {"t0": None, "first": None, "carry": 0.0}
_turn_clock_lock = threading.Lock()
LATENCY_FILE = rt("latency.jsonl")


def _dm_answered() -> None:
    """The DM put something on screen: the input its wait handed over has been acted on (see autorun_wait.py)."""
    try:
        os.remove(rt(".input_handed"))
    except OSError:
        pass


def _turn_clock_start(after_roll: bool = False) -> None:
    """Start timing the DM. After a player's roll the clock restarts, but the DM's time before the roll request
    (not the player's time on the dice) is carried over, so the badge shows what the DM really took."""
    with _turn_clock_lock:
        carry = 0.0
        if after_roll and _turn_clock["t0"] is not None and _turn_clock["first"] is not None:
            carry = _turn_clock["first"] + (_turn_clock.get("carry") or 0.0)
        _turn_clock["t0"], _turn_clock["first"], _turn_clock["carry"] = _time.time(), None, carry


def _turn_clock_hit(data: dict) -> None:
    now = _time.time()
    plain = not any(data.get(k) for k in ("action", "player", "npc", "dutch", "dice", "tutor", "inspiration_award",
                                           "milestone_award", "milestone_spend", "xp_award", "image"))
    with _turn_clock_lock:
        t0 = _turn_clock["t0"]
        if t0 is None:
            return
        if _turn_clock["first"] is None:
            _turn_clock["first"] = round(now - t0, 2)
        if not plain:
            return
        carry = _turn_clock.get("carry") or 0.0
        rec = {"ts": round(now, 1), "first": round(carry + _turn_clock["first"], 2), "reply": round(carry + now - t0, 2)}
        _turn_clock["t0"] = None
    try:
        with open(LATENCY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass
    _broadcast({"dm_latency": rec})


# ── Beat by beat: the DM shows one step, then waits for the table to press Continue ──
# The DM sends a short beat and POSTs /beat/pause {label: "Next: the kobolds attack"}; every screen shows a Continue
# button. Pressing it (any screen) puts "[Continue]: <label>" in the DM's queue, so its wait hands it over and the DM
# resolves the next beat. Nothing big happens off screen, and every beat is a quick answer.
_beat_pause: dict | None = None
_beat_lock = threading.Lock()


def _end_beat_pause() -> None:
    global _beat_pause
    with _beat_lock:
        had, _beat_pause = _beat_pause, None
    if had:
        _broadcast({"beat_pause": None})


@app.route("/beat/pause", methods=["POST"])
def beat_pause():
    if not _token_ok():
        return "Forbidden", 403
    data = request.get_json(force=True, silent=True) or {}
    label = re.sub(r"[`\\$]", "", str(data.get("label", "")))[:120].strip() or "Continue"
    global _beat_pause
    with _beat_lock:
        _beat_pause = {"label": label, "id": secrets.token_hex(4), "at": _time.time()}
        info = dict(_beat_pause)
    _broadcast({"beat_pause": info})
    return jsonify(info), 200


@app.route("/beat/continue", methods=["POST"])
def beat_continue():
    """Somebody at the table pressed Continue: tell the DM to play the next beat."""
    if not _token_ok():
        return "Forbidden", 403
    global _beat_pause
    with _beat_lock:
        info, _beat_pause = _beat_pause, None
    if not info:
        return "", 204                                   # already continued (another screen was quicker)
    try:
        with _staged_lock, open(QUEUE_FILE, "a", encoding="utf-8") as f:
            f.write(f"[Continue]: {info['label']}\n")
    except OSError:
        return "could not queue", 500
    _turn_clock_start()                                  # the next beat is timed like any answer
    _broadcast({"beat_pause": None})
    return "", 204


@app.route("/chunk", methods=["POST"])
def chunk():
    if not _token_ok():
        return "Forbidden", 403
    data = request.get_json(silent=True) or {}
    raw = data.get("text", "")
    if not raw and not data.get("image"):
        return "", 204

    _turn_clock_hit(data)
    _dm_answered()
    if not any(data.get(k) for k in ("player", "npc", "dice", "dutch", "tutor", "action", "image")):
        global _story_live
        _story_live = True                               # the DM is in the story: the menu may offer "Go to the story"
        _close_answered_rolls()
        _end_beat_pause()                                # the DM went on: no Continue is waited for any more

    is_action      = bool(data.get("action"))
    is_player      = bool(data.get("player"))
    is_npc         = bool(data.get("npc"))
    is_dutch       = bool(data.get("dutch"))
    is_dice        = bool(data.get("dice"))
    is_tutor       = bool(data.get("tutor"))
    is_inspiration = bool(data.get("inspiration_award"))
    is_milestone_award = bool(data.get("milestone_award"))
    is_milestone_spend = bool(data.get("milestone_spend"))
    is_xp_award    = bool(data.get("xp_award"))

    # ── Milestone award/spend (stack-based reward, distinct from binary Inspiration) ──
    if is_milestone_award or is_milestone_spend:
        name = str(data.get("milestone_award") or data.get("milestone_spend") or "").strip()[:80]
        label = str(data.get("label") or "Milestone").strip()[:40]
        if not name:
            return "", 204
        payload: dict = {
            "milestone_award" if is_milestone_award else "milestone_spend": name,
            "label": label,
            "text": name,
        }
        log_entry: dict = dict(payload)
        if is_milestone_award and data.get("reason"):
            payload["reason"] = str(data["reason"]).strip()[:240]
            log_entry["reason"] = payload["reason"]
        with _text_log_lock:
            _text_log.append(log_entry)
        with _tail_lock:
            _tail_buffer.append(log_entry)
        _persist_log()
        _persist_tail()
        _broadcast(payload)
        return "", 204

    # Inspiration and XP awards carry no text from stdin — build synthetic text.
    if is_inspiration:
        name = str(data.get("inspiration_award", "")).strip()[:80]
        if not name:
            return "", 204
        reason = str(data.get("reason", "")).strip()[:240]
        payload: dict = {"inspiration_award": name, "text": name}
        log_entry: dict = {"inspiration_award": name, "text": name}
        if reason:
            payload["reason"] = reason
            log_entry["reason"] = reason
        with _text_log_lock:
            _text_log.append(log_entry)
        with _tail_lock:
            _tail_buffer.append(log_entry)
        _persist_log()
        _persist_tail()
        _broadcast(payload)
        # Also update player inspiration state in stats.
        # NOTE: _persist_stats() acquires _stats_lock internally — capture the snapshot
        # inside the lock, then call persist/broadcast OUTSIDE to avoid deadlock.
        stats_snapshot = None
        with _stats_lock:
            players = _current_stats.setdefault("players", [])
            match = next((p for p in players if p.get("name", "").lower() == name.lower()), None)
            if match:
                match["inspiration"] = True
                stats_snapshot = dict(_current_stats)
        if stats_snapshot is not None:
            _persist_stats()
            _broadcast({"stats": stats_snapshot})
        return "", 204

    if data.get("image"):
        im = data.get("image")
        if not isinstance(im, dict) or not im.get("src"):
            return "", 204
        im = {"src": str(im["src"])[:200], "caption": str(im.get("caption", ""))[:240],
              "big": bool(im.get("big")), "kind": str(im.get("kind", ""))[:20]}
        payload = {"image": im, "text": im["caption"]}
        log_entry = {"image": im, "text": im["caption"]}
        with _text_log_lock:
            _text_log.append(log_entry)
        with _tail_lock:
            _tail_buffer.append(log_entry)
        _persist_log()
        _persist_tail()
        _broadcast(payload)
        return "", 204

    if is_xp_award:
        xp_data = data.get("xp_award", {})
        if not isinstance(xp_data, dict):
            return "", 204
        payload = {"xp_award": xp_data, "text": xp_data.get("summary", "")}
        log_entry = {"xp_award": xp_data, "text": xp_data.get("summary", "")}
        with _text_log_lock:
            _text_log.append(log_entry)
        with _tail_lock:
            _tail_buffer.append(log_entry)
        _persist_log()
        _persist_tail()
        _broadcast(payload)
        return "", 204

    # Player/npc/dice/tutor/action text comes from send.py (no ANSI/chrome) — light clean only.
    # DM narration may come from wrapper.py — full clean.
    cleaned = raw.strip() if (is_action or is_player or is_npc or is_dice or is_tutor) else _clean(raw)
    if not cleaned.strip():
        return "", 204

    payload: dict = {"text": cleaned}

    if is_action:
        payload["action"] = data["action"]
    elif is_player:
        payload["player"] = data["player"]
    elif is_npc:
        payload["npc"] = data["npc"]
    elif is_dutch:
        payload["dutch"] = True
    elif is_dice:
        payload["dice"] = True
    elif is_tutor:
        payload["tutor"] = True
    else:
        # Scene detection only on DM narration
        scene = _detect_scene(cleaned)
        if scene:
            payload["scene"] = scene
            if _audio:
                _audio.on_scene_change(scene["name"])
        # SFX scan on all non-player text
        if _audio:
            _audio.on_text(cleaned)

    # Store full typed payload so replay preserves action/player/npc/dice/tutor context
    log_entry: dict = {"text": cleaned}
    if is_action:
        log_entry["action"] = data["action"]
    elif is_player:
        log_entry["player"] = data["player"]
    elif is_npc:
        log_entry["npc"] = data["npc"]
    elif is_dutch:
        log_entry["dutch"] = True
    elif is_dice:
        log_entry["dice"] = True
    elif is_tutor:
        log_entry["tutor"] = True

    # Stamp campaign on tail entries to prevent bleed when switching campaigns
    try:
        _camp_stamp = open(CAMP_FILE, encoding="utf-8").read().strip()
        if _camp_stamp:
            log_entry["_camp"] = _camp_stamp
    except Exception:
        pass

    with _text_log_lock:
        _text_log.append(log_entry)
    with _tail_lock:
        _tail_buffer.append(log_entry)

    _persist_log()
    _persist_tail()
    _broadcast(payload)
    return "", 204


def _camp_images_dir():
    try:
        name = open(CAMP_FILE, encoding="utf-8").read().strip()
    except Exception:
        return None
    if not name or "/" in name or "\\" in name or ".." in name:
        return None
    root = os.environ.get("DND_CAMPAIGN_ROOT", "").strip() or os.path.join(os.path.expanduser("~"), ".claude", "dnd")
    return os.path.join(os.path.expanduser(root), "campaigns", name, "images")


@app.route("/img/<path:filename>")
def serve_campaign_image(filename):
    """Read-only flavour images of the loaded campaign (<campaign>/images/)."""
    d = _camp_images_dir()
    if not d or not os.path.isdir(d):
        return "no images", 404
    if not filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
        return "bad type", 404
    return send_from_directory(d, filename, max_age=3600)


@app.route("/stats", methods=["POST"])
def stats():
    """Receive character/combat stat updates. Merges players by name, replaces turn_order.

    Pass replace_players=true to replace the entire player list (use on /dnd load to
    prevent stale characters from a previous campaign persisting in the sidebar).
    """
    if not _token_ok():
        return "Forbidden", 403
    data = request.get_json(silent=True) or {}
    if not data:
        return "", 204
    internal = bool(data.pop("_internal", False))       # the display's own pushes (Main Menu) are not the DM answering
    if not internal and any(k not in ("autorun_waiting", "autorun_cycle") for k in data):
        _dm_answered()

    # An EMPTY party for a campaign that does have character files is a mistake (the DM going by memory, e.g. after
    # an aborted character creation): show the characters from the files instead, so the sidebar matches the disk.
    if data.get("replace_players") and data.get("players") == [] and not internal:
        try:
            import party as _party
            camp = str(data.get("campaign") or _active_campaign_name() or "")
            from_files = _party.load_party(camp) if camp else []
        except Exception:
            from_files = []
        if from_files:
            data["players"] = from_files

    _effect_expire_events: list[dict] = []
    with _stats_lock:
        _was_empty = not _current_stats.get("players")
    with _stats_lock:
        if "players" in data:
            # replace_players=true wipes the list first — used on campaign load
            if data.get("replace_players"):
                _current_stats["players"] = []
            existing_players: list = _current_stats.setdefault("players", [])
            for incoming in data["players"]:
                name = incoming.get("name")
                if not name:
                    continue
                match = next((p for p in existing_players if p.get("name") == name), None)
                # Keys prefixed with _ are mutation ops, not stored fields
                _MUTATION_KEYS = {
                    "_inventory_add", "_inventory_remove",
                    "_conditions_add", "_conditions_remove",
                    "_slot_use", "_slot_restore",
                    "_hd_use", "_hd_restore",
                    "_effect_start", "_effect_end",
                    "_sheet_spells",
                    "_milestone_inc", "_milestone_dec",
                }
                if match:
                    for key, val in incoming.items():
                        if key == "_inventory_add":
                            inv = match.setdefault("sheet", {}).setdefault("inventory", [])
                            if val not in inv:
                                inv.append(val)
                        elif key == "_inventory_remove":
                            sheet = match.get("sheet", {})
                            sheet["inventory"] = [
                                i for i in sheet.get("inventory", [])
                                if i.lower() != str(val).lower()
                            ]
                        elif key == "_conditions_add":
                            conds = match.setdefault("conditions", [])
                            if val not in conds:
                                conds.append(val)
                        elif key == "_conditions_remove":
                            match["conditions"] = [
                                c for c in match.get("conditions", [])
                                if c.lower() != str(val).lower()
                            ]
                        elif key == "_slot_use":
                            slots = match.setdefault("spell_slots", {})
                            lvl = str(val)
                            slot = slots.setdefault(lvl, {"used": 0, "max": 0})
                            _normalize_slot(slot)
                            slot["used"] = min(slot["used"] + 1, slot.get("max", 99))
                        elif key == "_slot_restore":
                            slots = match.setdefault("spell_slots", {})
                            lvl = str(val)
                            slot = slots.setdefault(lvl, {"used": 0, "max": 0})
                            _normalize_slot(slot)
                            slot["used"] = max(slot["used"] - 1, 0)
                        elif key == "_hd_use":
                            hd = match.setdefault("hit_dice", {"remaining": 0, "max": 0})
                            hd["remaining"] = max(hd.get("remaining", 0) - 1, 0)
                        elif key == "_hd_restore":
                            hd = match.setdefault("hit_dice", {"remaining": 0, "max": 0})
                            hd["remaining"] = min(
                                hd.get("remaining", 0) + int(val),
                                hd.get("max", 99)
                            )
                        elif key == "_effect_start":
                            # val is an effect dict: {name, duration_type, ...}
                            spell_name = val.get("name", "")
                            effects = match.setdefault("effects", [])
                            # Replace any existing effect with the same name
                            match["effects"] = [
                                e for e in effects
                                if e.get("name", "").lower() != spell_name.lower()
                            ]
                            match["effects"].append(val)
                            # Sync concentration field if this is a conc effect
                            if val.get("concentration") and spell_name:
                                match["concentration"] = spell_name
                        elif key == "_effect_end":
                            # val is the spell name string
                            spell_lower = str(val).lower()
                            removed = [
                                e for e in match.get("effects", [])
                                if e.get("name", "").lower() == spell_lower
                            ]
                            match["effects"] = [
                                e for e in match.get("effects", [])
                                if e.get("name", "").lower() != spell_lower
                            ]
                            # If the ended effect was concentration, also clear it
                            if removed and any(e.get("concentration") for e in removed):
                                if match.get("concentration", "").lower() == spell_lower:
                                    match["concentration"] = None
                        elif key == "_sheet_spells":
                            # Patch only the spells sub-key inside sheet
                            sheet = match.setdefault("sheet", {})
                            sheet["spells"] = val
                        elif key == "inspiration" and val is False:
                            match["inspiration"] = False
                        elif key == "_milestone_inc":
                            # Stack-based reward counter — Inspiration variants,
                            # homebrew Hero Coins, Bardic Inspiration tokens, etc.
                            # The label string is the value; per-label cap optional.
                            label = str(val) or "Milestone"
                            ms = match.setdefault("milestones", {})
                            cap = match.get("milestone_caps", {}).get(label, 99)
                            ms[label] = min(ms.get(label, 0) + 1, cap)
                        elif key == "_milestone_dec":
                            label = str(val) or "Milestone"
                            ms = match.setdefault("milestones", {})
                            ms[label] = max(ms.get(label, 0) - 1, 0)
                            if ms.get(label, 0) == 0:
                                ms.pop(label, None)
                        elif isinstance(val, dict) and isinstance(match.get(key), dict):
                            match[key].update(val)
                        else:
                            match[key] = val
                else:
                    # Strip mutation ops — they're meaningless for new players
                    existing_players.append(
                        {k: v for k, v in incoming.items() if k not in _MUTATION_KEYS}
                    )

        # turn_order replaces entirely (None = clear); also ticks round-based effects
        _effect_expire_events: list[dict] = []
        if "turn_order" in data:
            new_to = data["turn_order"]
            old_to = _current_stats.get("turn_order") or {}
            if isinstance(new_to, dict) and isinstance(new_to.get("order"), list):
                # The DM may list {"name": ..., "init": 17} objects; the screen wants plain names.
                new_to["order"] = [str(e.get("name") or e.get("character") or "") if isinstance(e, dict) else str(e)
                                   for e in new_to["order"]]
                new_to["order"] = [n for n in new_to["order"] if n]
            if isinstance(new_to, dict) and new_to and "order" not in new_to and old_to.get("order"):
                new_to = {**old_to, **new_to}    # `--turn-current` / `--turn-round` only move the pointer: keep the order
            if (not new_to) or not isinstance(new_to, dict) or new_to.get("current") != old_to.get("current") \
                    or new_to.get("round") != old_to.get("round"):
                _clear_dice_pending()        # a new turn: last turn's unanswered roll requests are over
            _current_stats["turn_order"] = new_to
            _broadcast({"input_gate": _effective_allowed()})
            # Decrement round-based effects for the actor whose turn just started
            if new_to and isinstance(new_to, dict) and new_to.get("current"):
                actor = new_to["current"].lower()
                for p in _current_stats.get("players", []):
                    if p.get("name", "").lower() != actor:
                        continue
                    kept, expired = [], []
                    for eff in p.get("effects", []):
                        if eff.get("duration_type") == "rounds":
                            eff = dict(eff)  # don't mutate in-place
                            eff["duration_remaining"] = max(0, eff.get("duration_remaining", 1) - 1)
                            if eff["duration_remaining"] <= 0:
                                expired.append(eff)
                            else:
                                kept.append(eff)
                        else:
                            kept.append(eff)
                    p["effects"] = kept
                    for eff in expired:
                        was_conc = eff.get("concentration", False)
                        if was_conc and p.get("concentration", "").lower() == eff["name"].lower():
                            p["concentration"] = None
                        _effect_expire_events.append({
                            "owner": p["name"],
                            "name": eff["name"],
                            "was_concentration": was_conc,
                        })

        # world_time replaces entirely
        if "world_time" in data:
            _current_stats["world_time"] = data["world_time"]

        # factions replaces entirely ([] clears)
        # Validate: default missing standing to "Neutral" and warn so the root
        # cause (DM omitting the field when building JSON from state.md prose)
        # is surfaced in logs without silently showing "—" in the sidebar.
        if "factions" in data:
            validated_factions = []
            for f in (data["factions"] or []):
                if not isinstance(f, dict):
                    continue
                if f.get("name") and not f.get("standing"):
                    print(
                        f"[WARN] faction '{f['name']}' missing standing field — "
                        "defaulting to Neutral. Push with standing: Allied/Friendly/"
                        "Neutral/Suspicious/Hostile to show correct colour.",
                        file=sys.stderr,
                    )
                    f = dict(f)
                    f["standing"] = "Neutral"
                validated_factions.append(f)
            _current_stats["factions"] = validated_factions

        # quests replaces entirely ([] clears)
        if "quests" in data:
            _current_stats["quests"] = data["quests"]

        # combat_patch: change a few things on the board without resending it (enemy HP, token squares, conditions)
        _cp = data.get("combat_patch")
        if isinstance(_cp, dict) and isinstance(_current_stats.get("combat"), dict):
            import copy as _copy
            _raw = _copy.deepcopy(_current_stats["combat"])
            _same = lambda a, b: str(a or "").strip().lower() == str(b or "").strip().lower()
            for _e in (_cp.get("enemies") or []):
                for _t in (_raw.get("enemies") or []):
                    if isinstance(_e, dict) and _same(_t.get("name"), _e.get("name")):
                        for _k in ("hp", "max", "note"):
                            if _k in _e:
                                _t[_k] = _e[_k]
            _m = _raw.get("map")
            if isinstance(_m, dict):
                for _p in (_cp.get("tokens") or []):
                    for _t in (_m.get("tokens") or []):
                        if isinstance(_p, dict) and _same(_t.get("name"), _p.get("name")):
                            for _k in ("x", "y", "conditions"):
                                if _k in _p:
                                    _t[_k] = _p[_k]
                _gone = [str(n).strip().lower() for n in (_cp.get("remove") or [])]
                if _gone:
                    _m["tokens"] = [_t for _t in (_m.get("tokens") or []) if str(_t.get("name", "")).strip().lower() not in _gone]
            data["combat"] = _raw

        # combat board replaces entirely (null/{} clears): enemies with HP, plus the battle map
        if "combat" in data:
            _current_stats["combat"] = _clean_combat(data["combat"], _current_stats.get("combat"),
                                                     ((_current_stats.get("place") or {}).get("label") or ""))

        # where the party is on a map picture (null/{} clears): {image, x, y, label, view}
        if "place" in data:
            _current_stats["place"] = _clean_place(data["place"])

        # npcs in the current scene replace entirely ([] clears): [{name, role, disposition}]
        if "npcs" in data:
            _current_stats["npcs"] = [
                {"name": str(n.get("name", ""))[:60], "role": str(n.get("role", ""))[:80],
                 "disposition": str(n.get("disposition", ""))[:30]}
                for n in (data["npcs"] or []) if isinstance(n, dict) and n.get("name")][:12]

        current = dict(_current_stats)

    # autorun_waiting / autorun_cycle — display-only signals, not stored in stats
    # (a push that also carried npcs/combat/... is still saved before an early return)
    if "autorun_waiting" in data:
        if not data["autorun_waiting"]:
            # Turn resolved — clear stored cycle so reconnecting clients don't see stale pie
            global _autorun_cycle
            with _autorun_cycle_lock:
                _autorun_cycle = None
        _broadcast({"autorun_waiting": bool(data["autorun_waiting"])})
        if not any(k in data for k in ("players", "turn_order", "world_time", "factions",
                                        "quests", "replace_players", "sheet", "autorun_cycle")):
            _persist_stats()
            return "", 204

    if "autorun_cycle" in data:
        with _autorun_cycle_lock:
            _autorun_cycle = data["autorun_cycle"]
        _broadcast({"autorun_cycle": data["autorun_cycle"]})
        if not any(k in data for k in ("players", "turn_order", "world_time", "factions",
                                        "replace_players", "sheet", "autorun_threshold")):
            _persist_stats()
            return "", 204

    if "autorun_threshold" in data:
        global _autorun_threshold
        val = data["autorun_threshold"]
        _autorun_threshold = int(val) if val is not None else None
        _broadcast({"autorun_threshold": _autorun_threshold})
        if not any(k in data for k in ("players", "turn_order", "world_time", "factions",
                                        "replace_players", "sheet")):
            _persist_stats()
            return "", 204

    # Write active campaign name so dm_help.py always reads the current campaign.
    # Also reload the tail buffer from the new campaign's session_tail.json so
    # display replay at /dnd load shows the correct campaign's last session.
    if "campaign" in data:
        _new_camp = str(data["campaign"]).strip()
        try:
            _prev_camp = open(CAMP_FILE, encoding="utf-8").read().strip()
        except Exception:
            _prev_camp = ""
        try:
            with open(CAMP_FILE, "w", encoding="utf-8") as f:
                f.write(_new_camp)
            _load_tail()
        except Exception:
            pass
        # Reload of a campaign (switch, restart, or after /clear): restore what only the display held.
        global _awaiting_restore
        if _new_camp and (_new_camp != _prev_camp or _was_empty or _awaiting_restore):
            try:
                _restore_campaign_display(_new_camp, data)
                with _stats_lock:
                    current = dict(_current_stats)
            except Exception as e:
                print(f"_restore_campaign_display: {e}", file=sys.stderr)
        _awaiting_restore = False
        # Resolve and stash the ruleset for this campaign so the sidebar badge
        # can render. Defaults to '2014' for legacy campaigns predating the
        # ruleset field. Wrapped in try/except so a missing paths import or
        # malformed state.md never breaks the stats endpoint.
        try:
            from paths import campaign_ruleset as _campaign_ruleset
            _rs = _campaign_ruleset(str(data["campaign"]).strip())
            with _stats_lock:
                _current_stats["ruleset"] = _rs
            current = dict(_current_stats)
        except Exception:
            pass

    # Explicit ruleset override (e.g. push_stats.py --ruleset 2024)
    if "ruleset" in data:
        rs_in = str(data.get("ruleset") or "").strip()
        if rs_in in ("2014", "2024"):
            with _stats_lock:
                _current_stats["ruleset"] = rs_in
            current = dict(_current_stats)

    _persist_stats()
    _broadcast({"stats": current})
    # Broadcast any round-based effect expiries after the stats update
    for evt in _effect_expire_events:
        _broadcast({"effect_expired": evt})

    # Update expected player count for staged-input auto-trigger
    global _expected_count
    with _stats_lock:
        players = _current_stats.get("players", [])
    _expected_count = max(1, len(players))

    return "", 204


@app.route("/effects/expire", methods=["POST"])
def effects_expire():
    """Called by browser when a time-based effect countdown reaches zero.
    Removes the effect from stats, clears concentration if applicable,
    and broadcasts effect_expired to all connected clients.
    """
    if not _token_ok():
        return "Forbidden", 403
    data  = request.get_json(silent=True) or {}
    owner = data.get("owner", "").strip()
    name  = data.get("name", "").strip()
    if not owner or not name:
        return "", 400

    expire_evt = None
    with _stats_lock:
        for p in _current_stats.get("players", []):
            if p.get("name", "").lower() != owner.lower():
                continue
            was_conc   = False
            new_effects = []
            for e in p.get("effects", []):
                if e.get("name", "").lower() == name.lower():
                    was_conc = e.get("concentration", False)
                    if was_conc and p.get("concentration", "").lower() == name.lower():
                        p["concentration"] = None
                else:
                    new_effects.append(e)
            p["effects"] = new_effects
            expire_evt = {"owner": p["name"], "name": name, "was_concentration": was_conc}
            break
        current = dict(_current_stats)

    if expire_evt:
        _broadcast({"effect_expired": expire_evt})
    _broadcast({"stats": current})
    _persist_stats()
    return "", 204


@app.route("/audio-toggle", methods=["POST"])
def audio_toggle():
    """Enable/disable ambient or SFX from the browser toggle switches.

    Body: {"ambient": true|false, "sfx": true|false}  (either or both keys)
    Response: {"ambient": bool, "sfx": bool, "available": bool}
    Broadcasts audio_state to all connected browsers so every device syncs.
    """
    data = request.get_json(silent=True) or {}
    if _audio:
        if "sfx" in data:
            _audio.set_sfx(bool(data["sfx"]))
        state = _audio.get_state()
    else:
        state = {"sfx": False, "available": False}
    return state, 200


@app.route("/narration-pref", methods=["POST"])
def narration_pref():
    """Set the narration-length target the DM aims for each turn.

    Body: {"target_words": int}.  0 clears the preference. Persisted to the
    runtime dir as a plain integer; check_input.py reads it and prepends a
    directive to queued player input so the DM honors it that turn — no
    separate file read required on the DM side.
    """
    if not _token_ok():
        return "Forbidden", 403
    if not _rate_ok(request.remote_addr or "?"):
        return "Rate limited", 429
    data = request.get_json(silent=True) or {}
    try:
        n = int(data.get("target_words", 0))
    except (TypeError, ValueError):
        n = 0
    n = max(0, min(5000, n))
    pref = rt("narration_target")
    try:
        if n:
            with open(pref, "w", encoding="utf-8") as f:
                f.write(str(n))
        elif os.path.exists(pref):
            os.remove(pref)
    except OSError:
        pass
    return {"target_words": n}, 200


def _active_campaign_name() -> Optional[str]:
    try:
        return open(CAMP_FILE, encoding="utf-8").read().strip() or None
    except OSError:
        return None


@app.route("/input-gate", methods=["POST"])
def input_gate():
    """DM sets who may enter input. Body: {"allowed": ["Mira", ...]} or {"allowed": null} to clear."""
    if not _token_ok():
        return "Forbidden", 403
    data = request.get_json(silent=True) or {}
    global _input_gate
    al = data.get("allowed")
    _input_gate = [str(x)[:50] for x in al] if isinstance(al, list) else None
    _broadcast({"input_gate": _effective_allowed()})
    return jsonify({"allowed": _effective_allowed()})


@app.route("/relay", methods=["POST"])
def relay():
    """Forward a small control message to every connected display (PC View -> TV View)."""
    if not _token_ok():
        return "Forbidden", 403
    data = request.get_json(silent=True) or {}
    msg = data.get("msg")
    if not isinstance(msg, dict) or len(json.dumps(msg)) > 20000:
        return "bad message", 400
    _broadcast({"relay": msg})
    return "", 204


@app.route("/tts-cached", methods=["POST"])
def tts_cached():
    """Which of these texts already have a stored clip? (no synthesis)

    Body: {"texts": [str, ...]}
    Response: {"cached": [bool, ...]}
    """
    if _tts is None:
        return jsonify({"cached": []})
    if not _token_ok():
        return "Forbidden", 403
    data = request.get_json(silent=True) or {}
    d = _tts.cache_dir()
    out = []
    langs = data.get("langs") or []
    for i, t in enumerate((data.get("texts") or [])[:100]):
        t = str(t or "").strip()[: _tts.MAX_TEXT_CHARS].strip()
        lang = _tts.clean_lang(langs[i] if i < len(langs) else "en")
        out.append(bool(t) and (d / f"{_tts.cache_key(t, lang=lang)}.pcm").is_file())
    return jsonify({"cached": out})


@app.route("/tts", methods=["POST"])
def tts_synthesize():
    """Speak a narration block with the local Pocket TTS voice.

    Body: {"text": str}
    Response: raw 16-bit mono PCM at 24 kHz (audio/L16).
    Failures: 503 (Pocket TTS not installed or switched off), 400 (empty),
    502 (the local model failed). The page says so; it never switches voice.
    """
    if _tts is None:
        return "TTS module unavailable", 503
    if not _token_ok():
        return "Forbidden", 403
    if not _rate_ok(request.remote_addr or "?"):
        return "Rate limited", 429
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return "empty text", 400
    if not _tts.available():
        return "Local TTS is not installed (see README: Spoken narration)", 503
    try:
        pcm = _tts.synthesize_strict(text, data.get("lang"))
    except _tts.TtsError as e:
        return f"Local TTS failed: {e}", 502
    return Response(pcm, mimetype="audio/L16;codec=pcm;rate=24000",
                    headers={"Cache-Control": "no-store"})


@app.route("/audio/sfx/<name>")
def audio_sfx(name):
    """Serve a synthesized SFX WAV for the given effect name."""
    if not _audio:
        return "Audio not available", 503
    wav = _audio.get_sfx_wav(name)
    if wav is None:
        return "Not found", 404
    return Response(wav, mimetype="audio/wav",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.route("/clear", methods=["POST"])
def clear():
    """Wipe text log AND stats, broadcast clear to all connected browsers.

    Called on /dnd new (fresh campaign). Ensures sidebar shows no stale characters.
    """
    if not _token_ok():
        return "Forbidden", 403
    global _scene_buffer, _current_stats, _awaiting_restore
    _awaiting_restore = True      # keep the campaign's saved display state until it is read back
    with _text_log_lock:
        _text_log.clear()
    with _stats_lock:
        _current_stats = {}
    _scene_buffer = []
    for path in (LOG_FILE, STATS_FILE):
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
    _broadcast({"clear": True})
    return "", 204


@app.route("/help-request", methods=["POST"])
def help_request():
    """Spawn dm_help.py to generate and send an on-demand DM hint.

    Protected by an O_EXCL lock file — concurrent requests return 409
    so multiple players clicking the button never duplicates execution.
    Lock is released by dm_help.py in its finally block.
    """
    if not _token_ok():
        return "Forbidden", 403

    # A lock left behind by a helper that was killed would block DM Help for ever: after three
    # minutes it is stale (a hint takes well under two).
    try:
        if _time.time() - os.path.getmtime(HELP_LOCK) > 180:
            os.unlink(HELP_LOCK)
    except OSError:
        pass

    # Atomic lock: O_EXCL fails if file already exists — no race condition
    try:
        fd = os.open(HELP_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        return "Already running", 409

    # Read active campaign name
    try:
        campaign = open(CAMP_FILE, encoding="utf-8").read().strip()
    except FileNotFoundError:
        os.unlink(HELP_LOCK)
        return "No active campaign", 400

    if not campaign:
        os.unlink(HELP_LOCK)
        return "No active campaign", 400

    dm_help_py = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dm_help.py")
    try:
        subprocess.Popen(
            [sys.executable, dm_help_py, "--campaign", campaign],
            close_fds=True,
            start_new_session=True,
        )
    except OSError:
        try:
            os.unlink(HELP_LOCK)
        except OSError:
            pass
        return "Could not start DM Help", 500
    return "", 202


@app.route("/player-input", methods=["POST"])
def player_input():
    """Queue a player action submitted from the display companion.

    Body: {"character": "Mira", "text": "I draw my rapier", "hold": false}
    Broadcasts pending_input event to all connected browsers.
    """
    if not _token_ok():
        return "Forbidden", 403

    import time
    data = request.get_json(force=True, silent=True) or {}
    character = str(data.get("character", "Party"))[:50]
    text = str(data.get("text", ""))[:500]
    hold = bool(data.get("hold", False))

    # Strip shell metacharacters — input is player dialogue/action, not commands
    text = re.sub(r"[`\\$]", "", text).strip()
    if not text:
        return "empty", 400

    entry = {
        "character": character,
        "text": text,
        "hold": hold,
        "timestamp": time.time(),
    }

    with _input_lock:
        _input_queue.append(entry)
        current = list(_input_queue)

    _persist_input_queue()
    _broadcast({"pending_input": current})
    return "", 204


@app.route("/player-input/dice", methods=["POST"])
def player_dice():
    """Server-side dice roll submitted from the on-screen dice pad.

    Body: {"character": "Piper", "spec": "1d20", "modifier": 5,
           "advantage": "normal" | "advantage" | "disadvantage",
           "label": "Stealth check"  (optional)}

    Rolls server-side (secrets.randbelow → uniform, non-spoofable), broadcasts
    a dice-typed entry on the feed, and returns the result so the pad can
    finish its slot-machine animation on the real value.
    """
    if not _token_ok():
        return "Forbidden", 403

    data = request.get_json(force=True, silent=True) or {}
    character = re.sub(r"[`\\$]", "", str(data.get("character", "Player"))[:50]).strip() or "Player"
    spec      = str(data.get("spec", "1d20")).strip().lower()
    modifier  = int(data.get("modifier", 0) or 0)
    adv       = str(data.get("advantage", "normal")).strip().lower()
    label     = re.sub(r"[`\\$]", "", str(data.get("label", ""))[:60]).strip()
    req_id    = str(data.get("request_id", "")).strip()[:24]

    m = re.fullmatch(r"(\d{1,2})d(\d{1,3})", spec)
    if not m:
        return jsonify({"error": "bad spec"}), 400
    n_dice, n_sides = int(m.group(1)), int(m.group(2))
    if not (1 <= n_dice <= 20 and 2 <= n_sides <= 100):
        return jsonify({"error": "out of range"}), 400
    modifier = max(-100, min(100, modifier))

    def _roll_once() -> list[int]:
        return [secrets.randbelow(n_sides) + 1 for _ in range(n_dice)]

    if adv in ("advantage", "disadvantage") and spec == "1d20":
        r1, r2 = _roll_once(), _roll_once()
        chosen = max(r1[0], r2[0]) if adv == "advantage" else min(r1[0], r2[0])
        rolls  = [chosen]
        kept   = [chosen]
        both   = [r1[0], r2[0]]
    else:
        rolls = _roll_once()
        kept  = rolls
        both  = None

    subtotal = sum(kept)
    total    = subtotal + modifier
    mod_str  = (f"+{modifier}" if modifier > 0 else (str(modifier) if modifier < 0 else ""))
    breakdown = f"[{', '.join(str(r) for r in (both or rolls))}]"
    if both is not None:
        breakdown += f" → keep {kept[0]} ({adv})"
    if modifier:
        breakdown += f" {mod_str}"
    suffix = f" — {label}" if label else ""
    text   = f"{character} rolls {spec}{mod_str}: {breakdown} = {total}{suffix}"

    payload   = {"text": text, "dice": True}
    log_entry = {"text": text, "dice": True}
    try:
        _camp_stamp = open(CAMP_FILE, encoding="utf-8").read().strip()
        if _camp_stamp:
            log_entry["_camp"] = _camp_stamp
    except Exception:
        pass

    with _text_log_lock:
        _text_log.append(log_entry)
    with _tail_lock:
        _tail_buffer.append(log_entry)
    _persist_log()
    _persist_tail()
    _broadcast(payload)

    # Correlate against any pending DM request. Case-insensitive match on the
    # character name — drop them from the request's expected-rollers set.
    pending_changed = False
    queue_roll = False
    if req_id:
        with _dice_pending_lock:
            entry = _dice_pending.get(req_id)
            _waiting = (_time.time() - _dice_last_poll.get(req_id, 0.0)) < _DICE_POLL_FRESH
            if entry is None:
                queue_roll = not _waiting          # the DM's request is gone and nobody is polling for it
            if entry is not None:
                ci = character.lower()
                matched = next((c for c in entry["chars"] if c.lower() == ci), None)
                if matched is not None:
                    queue_roll = not _waiting      # the DM stopped waiting (timeout, other turn): hand the roll over as input
                    entry["chars"].discard(matched)
                    pending_changed = True
                    now = _time.time()
                    for old in [r for r, v in _dice_results.items() if all(now - x["at"] > 600 for x in v.values())]:
                        _dice_results.pop(old, None)
                    _dice_results.setdefault(req_id, {})[matched] = {
                        "total": total, "natural": kept[0] if len(kept) == 1 else None, "text": text, "at": now}
                    if not entry["chars"]:
                        _dice_pending.pop(req_id, None)
                        _turn_clock_start(after_roll=True)      # the players' roll is in: the DM's next answer is timed from here (plus the DM's time before the request), not from GO
    if pending_changed:
        _broadcast({"dice_pending": _dice_pending_snapshot()})
    if queue_roll:
        # A roll nobody is waiting for any more would be lost (the DM's wait timed out after 120 s): give it to the DM
        # as the next input, where its wait loop picks it up.
        try:
            with _staged_lock, open(QUEUE_FILE, "a", encoding="utf-8") as f:
                f.write("[Roll]: " + " ".join(text.split()) + "\n")
        except OSError:
            pass

    return jsonify({
        "character": character,
        "spec": spec,
        "modifier": modifier,
        "advantage": adv,
        "rolls": rolls,
        "kept": kept,
        "both": both,
        "subtotal": subtotal,
        "total": total,
        "text": text,
        "request_id": req_id or None,
    }), 200


@app.route("/dice-request", methods=["POST"])
def dice_request():
    """DM-initiated dice request — broadcast to every screen (no persistence).

    Body: {"character": "Piper" | "any",
           "spec": "1d20", "modifier": 5,
           "advantage": "normal" | "advantage" | "disadvantage",
           "label": "Stealth check"  (optional),
           "dc": 15  (optional, informational)}

    Names match case-insensitively. "any" / ""
    targets every roller. No state stored — late-joining screens will not see
    requests issued before they connected.
    """
    if not _token_ok():
        return "Forbidden", 403

    import time
    data = request.get_json(force=True, silent=True) or {}
    raw_char  = data.get("characters") if "characters" in data else data.get("character", "any")
    if isinstance(raw_char, list):
        chars = [str(c).strip() for c in raw_char if str(c).strip()]
    else:
        chars = [c.strip() for c in re.sub(r"[`\\$]", "", str(raw_char))[:200].split(",") if c.strip()]
    if not chars:
        chars = ["any"]

    spec      = str(data.get("spec", "1d20")).strip().lower()
    modifier  = int(data.get("modifier", 0) or 0)
    adv       = str(data.get("advantage", "normal")).strip().lower()
    label     = re.sub(r"[`\\$]", "", str(data.get("label", ""))[:60]).strip()
    dc        = data.get("dc")

    if not re.fullmatch(r"\d{1,2}d\d{1,3}", spec):
        return jsonify({"error": "bad spec"}), 400
    if adv not in ("normal", "advantage", "disadvantage"):
        adv = "normal"
    modifier = max(-100, min(100, modifier))
    dc_val   = int(dc) if isinstance(dc, (int, float)) else None

    request_id = secrets.token_hex(6)

    # Only register pending entries for explicit named targets. "any" is fire-and-forget.
    trackable = [c for c in chars if c.lower() != "any"]
    if trackable:
        with _dice_pending_lock:
            # a new request for the same people replaces the older unanswered one (no stacked duplicates)
            for rid in [r for r, e in _dice_pending.items() if e["chars"] == set(trackable)]:
                _dice_pending.pop(rid, None)
                _broadcast({"dice_request_cancelled": rid})
            _dice_pending[request_id] = {
                "chars": set(trackable),
                "meta": {"spec": spec, "modifier": modifier, "advantage": adv, "label": label, "dc": dc_val},
                "started_at": time.time(),
            }
        _broadcast({"dice_pending": _dice_pending_snapshot()})

    # Every named target rolls on the screen's dice pad.
    onscreen_targets = [c for c in chars if c.lower() != "any"]
    payload = {
        "dice_request": {
            "request_id": request_id,
            "characters": chars,
            "character": chars[0] if len(chars) == 1 else "any",   # legacy single-target field
            "onscreen_targets": onscreen_targets,
            "spec": spec,
            "modifier": modifier,
            "advantage": adv,
            "label": label,
            "dc": dc_val,
        }
    }
    _broadcast(payload)
    _turn_clock_hit({"dice": True})        # asking for a roll is the DM's first answer: time it
    return jsonify({
        "request_id": request_id,
        "pending": sorted(trackable),
        "complete": not trackable,
    }), 200


@app.route("/dice-request/<request_id>", methods=["GET"])
def dice_request_status(request_id):
    """Poll a dice request's completion state.

    Returns 200 with {complete, pending, label, started_at}. A request that
    never existed (or has already fully drained) reports complete=True with
    an empty pending list — send.py --wait treats both identically.
    """
    if not _token_ok():
        return "Forbidden", 403
    with _dice_pending_lock:
        _dice_last_poll[request_id] = _time.time()          # the DM is waiting on this one right now
        entry = _dice_pending.get(request_id)
        results = {k: {"total": v["total"], "natural": v["natural"], "text": v["text"]}
                   for k, v in (_dice_results.get(request_id) or {}).items()}
        if entry is None or not entry["chars"]:
            return jsonify({"complete": True, "pending": [], "results": results}), 200
        return jsonify({
            "complete": False,
            "pending": sorted(entry["chars"]),
            "label": entry["meta"].get("label", ""),
            "started_at": entry["started_at"],
            "results": results,
        }), 200


@app.route("/dice-request/<request_id>", methods=["DELETE"])
def dice_request_cancel(request_id):
    """Cancel a pending dice request (DM gave up waiting / moved on)."""
    if not _token_ok():
        return "Forbidden", 403
    with _dice_pending_lock:
        _dice_pending.pop(request_id, None)
    _broadcast({"dice_pending": _dice_pending_snapshot(), "dice_request_cancelled": request_id})
    return "", 204


@app.route("/device/approve", methods=["POST"])
def device_approve():
    """DM approves a pending device. Body: {"id": "<device_id>"}"""
    if not _token_ok():
        return "Forbidden", 403
    device_id = str((request.get_json(force=True, silent=True) or {}).get("id", ""))
    with _devices_lock:
        _pending_devices.pop(device_id, None)
        _approved_devices.add(device_id)
    _persist_approved_devices()
    _persist_pending_devices()
    _broadcast({"device_approved": device_id})
    return "", 204


@app.route("/device/deny", methods=["POST"])
def device_deny():
    """DM denies a pending device. Body: {"id": "<device_id>"}"""
    if not _token_ok():
        return "Forbidden", 403
    device_id = str((request.get_json(force=True, silent=True) or {}).get("id", ""))
    with _devices_lock:
        _pending_devices.pop(device_id, None)
        _denied_devices.add(device_id)
    _persist_pending_devices()
    _broadcast({"device_denied": device_id})
    return "", 204


@app.route("/player-input/stage", methods=["POST"])
def stage_input():
    """Stage a player action for review. Broadcasts staged_inputs to all displays.

    Body: {"character": "Mira", "text": "draws her rapier"}
    """
    if not _token_ok():
        return "Forbidden", 403
    if not _rate_ok(request.remote_addr):
        return "Too Many Requests", 429

    device_id = request.headers.get("X-DND-Device", "")
    status    = _device_ok(device_id, request.remote_addr)
    if status == "denied":
        return "Forbidden", 403
    if status == "pending":
        return jsonify({"status": "pending"}), 202

    data      = request.get_json(force=True, silent=True) or {}
    character = str(data.get("character", ""))[:50].strip()
    text      = _sanitize_input(str(data.get("text", "")))

    if not character or not text:
        return "Bad Request", 400

    with _stats_lock:
        known = {p["name"] for p in _current_stats.get("players", [])}
    if not _char_ok(character, known):
        return "Forbidden", 403
    if _gate_blocks(character):
        return "The DM is not waiting for this character", 409

    # In solo mode (1 expected player), skip the manual Ready step and auto-trigger.
    solo = (_expected_count == 1) and not _REQUIRE_GO

    with _staged_lock:
        _staged[character] = {
            "text":      text,
            "ready":     solo,
            "timestamp": _time.time(),
        }
        snap = _staged_snapshot()

    _persist_staged()
    _broadcast({"staged_inputs": snap})

    if solo:
        _check_auto_trigger()

    return "", 204


@app.route("/player-input/ready", methods=["POST"])
def ready_input():
    """Toggle the ready flag for a staged character.

    Body: {"character": "Mira", "ready": true}
    Triggers auto-fire when all expected players are ready.
    """
    if not _token_ok():
        return "Forbidden", 403
    if not _rate_ok(request.remote_addr):
        return "Too Many Requests", 429

    device_id = request.headers.get("X-DND-Device", "")
    status    = _device_ok(device_id, request.remote_addr)
    if status == "denied":
        return "Forbidden", 403
    if status == "pending":
        return jsonify({"status": "pending"}), 202

    data      = request.get_json(force=True, silent=True) or {}
    character = str(data.get("character", ""))[:50].strip()
    ready     = bool(data.get("ready", True))

    with _staged_lock:
        if character not in _staged:
            return "Not Found", 404
        _staged[character]["ready"] = ready
        snap = _staged_snapshot()

    _persist_staged()
    _broadcast({"staged_inputs": snap})

    if ready:
        _check_auto_trigger()

    return "", 204


@app.route("/party-input", methods=["POST"])
def party_input():
    """GO: send every filled row of the Party Input to the DM at once.

    Body: {"entries": [{"character": "Everybody", "text": "..."}, {"character": "Mira", "text": "..."}]}
    Empty rows are ignored. A row the DM's input gate is closed for is refused (409),
    so nothing is half sent. Reaches the DM the same way a staged batch always did.
    """
    if not _token_ok():
        return "Forbidden", 403
    if not _rate_ok(request.remote_addr):
        return "Too Many Requests", 429

    device_id = request.headers.get("X-DND-Device", "")
    status    = _device_ok(device_id, request.remote_addr)
    if status == "denied":
        return "Forbidden", 403
    if status == "pending":
        return jsonify({"status": "pending"}), 202

    data = request.get_json(force=True, silent=True) or {}
    raw = data.get("entries")
    if not isinstance(raw, list):
        return "Bad Request", 400

    with _stats_lock:
        known = {p["name"] for p in _current_stats.get("players", [])}
    entries = {}
    blocked = []
    for item in raw[:30]:
        if not isinstance(item, dict):
            continue
        character = str(item.get("character", ""))[:50].strip()
        text = _sanitize_input(str(item.get("text", "")))
        if not character or not text:
            continue                                  # an empty row is simply not sent
        if not _char_ok(character, known):
            return "Forbidden", 403
        if _gate_blocks(character):
            blocked.append(character)
            continue
        entries[character] = text                     # one line per character; the last one wins
    if blocked:
        return "The DM is not waiting for: " + ", ".join(blocked), 409
    if not entries:
        return "Nothing to send", 400

    now = _time.time()
    with _staged_lock:
        _staged.clear()
        for character, text in entries.items():
            _staged[character] = {"text": text, "ready": True, "timestamp": now}
    _persist_staged()
    _end_beat_pause()
    _commit_planned_moves()
    _check_auto_trigger(force=True)
    return "", 204


@app.route("/player-input/go", methods=["POST"])
def go_input():
    """Final confirmation: send everything that is staged to the DM at once."""
    if not _token_ok():
        return "Forbidden", 403
    device_id = request.headers.get("X-DND-Device", "")
    status    = _device_ok(device_id, request.remote_addr)
    if status == "denied":
        return "Forbidden", 403
    if status == "pending":
        return jsonify({"status": "pending"}), 202
    with _staged_lock:
        if not _staged:
            return "Nothing staged", 409
        for v in _staged.values():
            v["ready"] = True
    _persist_staged()
    _check_auto_trigger(force=True)
    return "", 204


@app.route("/player-input/unstage", methods=["POST"])
def unstage_input():
    """Remove a character's staged action (e.g. player wants to edit it).

    Body: {"character": "Mira"}
    """
    if not _token_ok():
        return "Forbidden", 403

    device_id = request.headers.get("X-DND-Device", "")
    if _device_ok(device_id, request.remote_addr) != "approved":
        return "Forbidden", 403

    data      = request.get_json(force=True, silent=True) or {}
    character = str(data.get("character", ""))[:50].strip()

    with _staged_lock:
        _staged.pop(character, None)
        snap = _staged_snapshot()

    _persist_staged()
    _broadcast({"staged_inputs": snap})
    return "", 204


@app.route("/player-input/skip", methods=["POST"])
def skip_input():
    """Skip a character's turn — stages a 'skips their turn' entry marked ready.

    Counts toward the auto-trigger threshold and fires auto-trigger if threshold met.
    Body: {"character": "Mira"}
    """
    if not _token_ok():
        return "Forbidden", 403

    device_id = request.headers.get("X-DND-Device", "")
    if _device_ok(device_id, request.remote_addr) != "approved":
        return "Forbidden", 403

    data      = request.get_json(force=True, silent=True) or {}
    character = str(data.get("character", ""))[:50].strip()
    if not character:
        return "Bad Request", 400

    with _stats_lock:
        known = {p["name"] for p in _current_stats.get("players", [])}
    if not _char_ok(character, known):
        return "Forbidden", 403
    if _gate_blocks(character):
        return "The DM is not waiting for this character", 409

    with _staged_lock:
        _staged[character] = {
            "text":      "skips their turn",
            "ready":     True,
            "timestamp": _time.time(),
        }
        snap = _staged_snapshot()

    _persist_staged()
    _broadcast({"staged_inputs": snap})
    _check_auto_trigger()
    return "", 204


@app.route("/queue/consumed", methods=["POST"])
def queue_consumed():
    """Called by wrapper.py after it injects .input_queue into the PTY.

    Clears the server-side queue_status and broadcasts to all clients so
    the 'Queued — fires on DM Enter' indicator disappears on every display.
    Token required (called from localhost by the wrapper, but checked for
    consistency).
    """
    if not _token_ok():
        return "Forbidden", 403
    with _queue_status_lock:
        _queue_status.clear()
    _persist_staged()
    _broadcast({"queue_status": [], "dm_processing": True})
    return "", 204


@app.route("/player-input/submit-now", methods=["POST"])
def submit_now():
    """Promote .input_queue → .input_trigger for immediate injection.

    Called by the DM or Claude when they want to process queued player actions
    right now rather than waiting for the DM's next CLI Enter press.
    Token required (DM-only action).
    """
    if not _token_ok():
        return "Forbidden", 403
    try:
        content = open(QUEUE_FILE, encoding="utf-8").read()
        os.unlink(QUEUE_FILE)
    except FileNotFoundError:
        return "No queue", 204
    except Exception:
        return "Error", 500
    try:
        with open(TRIGGER_FILE, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception:
        return "Error", 500
    return "", 204


@app.route("/player-input/drain", methods=["POST"])
def drain_player_input():
    """Read and clear the player input queue. Called by check_input.py at turn start.

    Returns the drained entries as JSON, then broadcasts pending_input: [] to
    clear the indicator on all connected displays.
    """
    if not _token_ok():
        return "Forbidden", 403

    with _input_lock:
        drained = list(_input_queue)
        _input_queue.clear()

    _persist_input_queue()
    _broadcast({"pending_input": []})
    return jsonify(drained), 200


@app.route("/stream")
def stream():
    q: queue.Queue = queue.Queue(maxsize=256)
    with _clients_lock:
        _clients.append(q)

    # Send the current scene immediately on connect so the browser
    # starts with the right background even mid-session.
    initial_scene = SCENES[_current_scene_name] | {"name": _current_scene_name}
    q.put_nowait({"scene": initial_scene})

    # Replay recent entries so late-connecting / reconnecting browsers catch up.
    # _text_log is the durable session record (maxlen=2000); replay only the
    # last 200 chunks — the browser renders this batch on join, not the whole
    # log, so a late joiner isn't shown sessions 1..N rendered at them.
    with _text_log_lock:
        recent = list(_text_log)[-200:]
    if recent:
        q.put_nowait({"replay_batch": recent})

    # Send current stats so the sidebar is populated immediately on (re)connect.
    with _stats_lock:
        if _current_stats:
            q.put_nowait({"stats": dict(_current_stats)})

    q.put_nowait({"input_gate": _effective_allowed()})

    # Send current input queue so the pending indicator is accurate on reconnect.
    with _input_lock:
        if _input_queue:
            q.put_nowait({"pending_input": list(_input_queue)})

    # Send current staged inputs so the panel reflects live state on reconnect.
    with _staged_lock:
        if _staged:
            q.put_nowait({"staged_inputs": _staged_snapshot()})

    # Send current queue status so the 'Queued' indicator survives page reload.
    with _queue_status_lock:
        if _queue_status:
            q.put_nowait({"queue_status": list(_queue_status)})

    # Send current pending dice requests so the "Waiting on…" badge survives reload.
    with _beat_lock:
        _bp = dict(_beat_pause) if _beat_pause else None
    if _bp:
        q.put_nowait({"beat_pause": _bp})             # a screen that opens mid-pause shows the Continue button too
    snap = _dice_pending_snapshot()
    if snap:
        q.put_nowait({"dice_pending": snap})

    # Replay every active dice_request so screens that connected *after* a DM
    # broadcast still pre-fill their pad and store the request_id. Without this,
    # a late-joining or reloaded screen rolls without a request_id, the roll logs
    # but the pending set never drains, and the "Waiting on…" banner gets stuck.
    with _dice_pending_lock:
        active = [(rid, dict(e["meta"]), sorted(e["chars"])) for rid, e in _dice_pending.items() if e["chars"]]
    for rid, meta, chars in active:
        q.put_nowait({"dice_request": {
            "request_id": rid,
            "characters": chars,
            "character": chars[0] if len(chars) == 1 else "any",
            "onscreen_targets": [c for c in chars if c.lower() != "any"],
            "spec": meta.get("spec", "1d20"),
            "modifier": meta.get("modifier", 0),
            "advantage": meta.get("advantage", "normal"),
            "label": meta.get("label", ""),
            "dc": meta.get("dc"),
        }})

    # Replay autorun cycle so reconnecting clients resume the countdown from correct elapsed position.
    with _autorun_cycle_lock:
        if _autorun_cycle:
            q.put_nowait({"autorun_cycle": dict(_autorun_cycle)})

    # Replay threshold so the ready counter reflects the correct target on reconnect.
    if _autorun_threshold is not None:
        q.put_nowait({"autorun_threshold": _autorun_threshold})

    # Send any pending device approval requests so the DM sees them on reconnect.
    with _devices_lock:
        for dev in list(_pending_devices.values()):
            q.put_nowait({"device_request": {"id": dev["id"], "ip": dev["ip"]}})

    def generate():
        try:
            while True:
                try:
                    payload = q.get(timeout=5)
                    yield f"data: {json.dumps(payload)}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"   # prevent proxy timeout
        except GeneratorExit:
            with _clients_lock:
                try:
                    _clients.remove(q)
                except ValueError:
                    pass

    resp = Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Transfer-Encoding": "chunked",
        },
    )
    # Force a single authoritative Connection header — Werkzeug otherwise
    # emits both keep-alive (ours) and close (its default), which confuses
    # transparent proxies (e.g. eero mesh routing) into buffering the stream.
    resp.headers["Connection"] = "keep-alive"
    return resp


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Wire audio SFX broadcast now that _broadcast is defined
    if _audio:
        _audio.set_broadcast(_broadcast)

    host = "0.0.0.0" if _LAN_MODE else "127.0.0.1"   # clients connect to 127.0.0.1 ("localhost" costs ~2 s on Windows)
    # TLS — only enabled when --tls is explicitly passed; HTTP is the default.
    # Certs are runtime state (persist across plugin updates) → rt(); .scheme is a
    # launch-time marker read by simple shell commands → stays in the code dir.
    _display_dir = os.path.dirname(os.path.abspath(__file__))
    _cert = rt("cert.pem")
    _key  = rt("key.pem")
    ssl_ctx = (_cert, _key) if (_TLS_MODE and os.path.exists(_cert) and os.path.exists(_key)) else None
    scheme  = "https" if ssl_ctx else "http"

    # Write .scheme so push_stats.py / send.py / autorun_wait.py know which to use
    try:
        with open(os.path.join(_display_dir, ".scheme"), "w", encoding="utf-8") as _sf:
            _sf.write(scheme)
    except OSError:
        pass

    if _LAN_MODE:
        print(f"DnD DM Display — LAN mode (0.0.0.0:5001) [{scheme.upper()}]")
        print(f"  Local:  {scheme}://localhost:5001")
        print("  Token stored at:", TOKEN_FILE)
        print("  POST endpoints require X-DND-Token header (send.py/push_stats.py handle this automatically)")
        print()
    else:
        print(f"DnD DM Display — Flask server starting on {scheme}://localhost:5001")
        print(f"Open {scheme}://localhost:5001 in your browser, then Chromecast the tab.")
        print()
    try:
        import settings as _settings
        if _audio:
            _audio.set_sfx(bool(_settings.load()["sound_effects"]))
    except Exception:
        pass
    # Load the narrator voice now, in the background, so the first narration
    # block is not the one that waits ~30 s for the model. DND_TTS_WARMUP=off
    # skips it.
    if _tts is not None and os.environ.get("DND_TTS_WARMUP", "on").lower() != "off":
        threading.Thread(target=_tts.warm_up, daemon=True).start()
    # DND_DISPLAY_PORT lets a second copy run beside the live one (testing only);
    # send.py, push_stats.py and the launcher all assume the default 5001.
    app.run(host=host, port=int(os.environ.get("DND_DISPLAY_PORT", "5001")),
            threaded=True, debug=False, ssl_context=ssl_ctx)
