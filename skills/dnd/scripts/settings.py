#!/usr/bin/env python3
"""settings.py — table settings chosen in the web Main Menu.

Global settings live in <data-root>/settings.json. Each campaign keeps a
per-character Experience level in <campaign>/experience.json.

Claude receives them as short [[...]] directive lines at the top of each
batch of player input (see directive_lines), the same way narration length
and roll mode already arrive.

CLI:
    python3 settings.py show
    python3 settings.py directives [campaign-name]
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths  # noqa: E402

FLAVOUR_WORDS = {"low": 100, "medium": 250, "high": 500}
COMBAT_LEVELS = ("none", "low", "medium", "high")
EXPERIENCE_LEVELS = ("New", "Experienced")
RULESETS = ("2014", "2024")

DEFAULTS = {
    "flavour": "medium",
    "combat": "medium",
    "explain_dutch": False,
    "narration_audio": False,
    "default_ruleset": "2014",
}

_CHOICES = {
    "flavour": tuple(FLAVOUR_WORDS),
    "combat": COMBAT_LEVELS,
    "default_ruleset": RULESETS,
}
_BOOLS = ("explain_dutch", "narration_audio")


def settings_path() -> pathlib.Path:
    return paths._root() / "settings.json"


def load() -> dict:
    """Saved settings merged over the defaults; bad or missing values fall back."""
    out = dict(DEFAULTS)
    try:
        raw = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    if isinstance(raw, dict):
        for key in DEFAULTS:
            if key in raw and _valid(key, raw[key]):
                out[key] = raw[key]
    return out


def _valid(key: str, value) -> bool:
    if key in _BOOLS:
        return isinstance(value, bool)
    return value in _CHOICES.get(key, ())


def save(changes: dict) -> dict:
    """Apply known, valid keys from `changes`. Returns the full saved settings.

    Raises ValueError for an unknown key or a value that is not allowed, so a
    typo never silently becomes a setting.
    """
    current = load()
    for key, value in changes.items():
        if key not in DEFAULTS:
            raise ValueError(f"unknown setting: {key}")
        if not _valid(key, value):
            raise ValueError(f"invalid value for {key}: {value!r}")
        current[key] = value
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return current


# ── Per-character Experience ────────────────────────────────────────────────

def _experience_path(campaign: str) -> pathlib.Path:
    return paths.campaign_dir(campaign) / "experience.json"


def character_names(campaign: str) -> list[str]:
    chars = paths.campaign_dir(campaign) / "characters"
    try:
        return sorted(p.stem for p in chars.glob("*.md"))
    except OSError:
        return []


def load_experience(campaign: str) -> dict:
    """{character: 'New'|'Experienced'} for every character in the campaign.

    Characters with no saved choice are Experienced (the full set of options)."""
    try:
        raw = json.loads(_experience_path(campaign).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    return {name: raw.get(name) if raw.get(name) in EXPERIENCE_LEVELS else "Experienced"
            for name in character_names(campaign)}


def save_experience(campaign: str, character: str, level: str) -> None:
    if level not in EXPERIENCE_LEVELS:
        raise ValueError(f"invalid experience: {level!r}")
    if character not in character_names(campaign):
        raise ValueError(f"unknown character: {character!r}")
    current = load_experience(campaign)
    current[character] = level
    path = _experience_path(campaign)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(current, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# ── What Claude is told ────────────────────────────────────────────────────

_COMBAT_TEXT = {
    "none": "no combat encounters; resolve conflict through roleplay, exploration and puzzles.",
    "low": "very few combat situations; most challenges are social or exploration.",
    "medium": "the balance of a typical published adventure; follow the module.",
    "high": "frequent combat with little roleplay between fights.",
}


def directive_lines(campaign: str | None = None) -> list[str]:
    """The [[...]] lines Claude must honor this turn."""
    s = load()
    lines = [
        f"[[Table settings - Flavour {s['flavour']}: every DM step at most "
        f"{FLAVOUR_WORDS[s['flavour']]} words.]]",
        f"[[Table settings - Combat {s['combat']}: {_COMBAT_TEXT[s['combat']]}]]",
    ]
    if s["explain_dutch"]:
        lines.append("[[Table settings - Explain in Dutch is ON: write the full English text "
                     "as usual. Then send one short, simple Dutch summary (two sentences at "
                     "most) as its own block with send.py --dutch; it gets its own Play "
                     "button and the Dutch voice. Never shorten or replace the English.]]")
    if campaign:
        exp = load_experience(campaign)
        new = [n for n, lvl in exp.items() if lvl == "New"]
        old = [n for n, lvl in exp.items() if lvl == "Experienced"]
        if new:
            lines.append(f"[[Table settings - Experience New ({', '.join(new)}): offer simple "
                         "choices, use plain words and explain each rule or option clearly.]]")
        if old and new:
            lines.append(f"[[Table settings - Experience Experienced ({', '.join(old)}): "
                         "full character options, brief explanations.]]")
    return lines


def _main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "show"
    if cmd == "show":
        print(json.dumps(load(), indent=2))
        return 0
    if cmd == "directives":
        print("\n".join(directive_lines(argv[2] if len(argv) > 2 else None)))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(_main(sys.argv))
