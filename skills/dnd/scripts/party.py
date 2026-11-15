#!/usr/bin/env python3
"""party.py — the party of a campaign, read straight from its character files.

The display shows these characters as soon as a campaign is picked in the Main
Menu, so the sidebar is right even before the Dungeon Master (Claude) has loaded
the campaign and pushed the full sheets. Claude's own push, which has more
detail, merges over these by name.

CLI:
    python3 party.py <campaign-name>        # print the party as JSON
"""
from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths  # noqa: E402


def _num(s: str) -> int:
    return int(re.sub(r"[^\d]", "", s) or 0)


def _field(text: str, label: str) -> str | None:
    """The value after '**Label:**' up to the next '|' or the end of the line."""
    m = re.search(r"\*\*" + re.escape(label) + r":\*\*\s*([^|\n]+)", text)
    return m.group(1).strip() if m else None


def parse_character(text: str) -> dict | None:
    """One character file -> the player dict the display's sidebar uses."""
    m = re.search(r"^#\s+(.+?)\s*$", text, re.M)
    if not m:
        return None
    player: dict = {"name": m.group(1).strip()}

    race = _field(text, "Race")
    cls = _field(text, "Class")
    background = _field(text, "Background")
    if race:
        player["race"] = race
    if cls:
        base = re.sub(r"\s*\(.*?\)\s*$", "", cls) or cls          # "Sorcerer (Draconic Bloodline)" -> "Sorcerer"
        player["class"] = f"{base} ({background})" if background else cls
    level = _field(text, "Level")
    if level and level.strip().isdigit():
        player["level"] = int(level)

    hp = re.search(r"\*\*HP:\*\*\s*(\d+)\s*/\s*(\d+)", text)
    if hp:
        temp = re.search(r"\*\*Temp HP:\*\*\s*(\d+)", text)
        player["hp"] = {"current": int(hp.group(1)), "max": int(hp.group(2)),
                        "temp": int(temp.group(1)) if temp else 0}
    ac = re.search(r"\*\*AC:\*\*\s*(\d+)", text)
    if ac:
        player["ac"] = int(ac.group(1))
    speed = _field(text, "Speed")
    if speed:
        player["speed"] = speed
    hd = re.search(r"\*\*Hit Dice:\*\*\s*(\d+)d(\d+)(?:\s*\(remaining:\s*(\d+)\))?", text)
    if hd:
        player["hit_dice"] = {"max": int(hd.group(1)), "die": f"d{hd.group(2)}",
                              "remaining": int(hd.group(3)) if hd.group(3) is not None else int(hd.group(1))}
    xp = re.search(r"\*\*XP:\*\*\s*([\d.,]+)\s*/\s*([\d.,]+)", text)
    if xp:
        player["xp"] = {"current": _num(xp.group(1)), "next": _num(xp.group(2))}
    player.setdefault("conditions", [])
    return player


def load_party(campaign: str) -> list[dict]:
    """Every character of the campaign, in alphabetical order. [] if there are none."""
    chars = paths.campaign_dir(campaign) / "characters"
    out = []
    try:
        files = sorted(chars.glob("*.md"))
    except OSError:
        return []
    for f in files:
        try:
            player = parse_character(f.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if player:
            out.append(player)
    return out


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    print(json.dumps(load_party(sys.argv[1]), indent=2, ensure_ascii=False))
