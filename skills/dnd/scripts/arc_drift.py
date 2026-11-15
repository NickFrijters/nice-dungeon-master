#!/usr/bin/env python3
"""
arc_drift.py — keep a campaign aimed at its arc without binding it to it.

The DM judges each session ("did the story stay near the current chapter/beat?")
and records ONE line. This script does the deterministic part: it stores the
verdict, notices when the campaign has stalled (no beat landed, no chapter
advanced) and turns the history into an escalation level for the next session.

Verdicts (the DM's call, never the script's):
  on-course  events served the current chapter/beat, directly or by a detour
  detour     a player-driven tangent, fully played; arc untouched (legit)
  drifting   the arc was ignored this session; no pull was felt in the fiction
  off-arc    the story now lives somewhere the arc no longer describes

Escalation levels (steering is always in-fiction, never a wall):
  0  nothing to do
  1  telegraph: plant a rumour / NPC urgency pointing at the outstanding beat
  2  pressure: a world event that makes the beat's location or NPC unavoidable
  3  decide: either re-anchor by steering once more, or accept the new direction
     and run `arc revise` (dynamic) / note the divergence in steering_notes
     (structured). The players' direction wins when they keep choosing it.

Usage:
  arc_drift.py status  --campaign NAME [--json]
  arc_drift.py record  --campaign NAME --verdict V [--note TEXT] [--move TEXT]
  arc_drift.py anchors --campaign NAME

The log lives in <campaign>/drift-log.md, one line per record.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import paths
except ImportError:  # pragma: no cover
    paths = None  # type: ignore

VERDICTS = ("on-course", "detour", "drifting", "off-arc")
LOG_NAME = "drift-log.md"
LOG_HEADER = (
    "# Drift log\n"
    "*One line per save: `S<n> | date | chapter-or-beat | verdict | outstanding=<count> "
    "| note | move`. Written by `arc_drift.py record`.*\n\n"
)
ANCHOR_HEAD = re.compile(r"^##\s+Setting Anchors\s*$", re.M)


def _read(p: pathlib.Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def arc_block(state: str) -> str:
    m = re.search(r"^##\s+Campaign Arc\s*$(.*?)(?=^##\s|\Z)", state, re.M | re.S)
    return m.group(1) if m else ""


def _list_field(block: str, key: str) -> list[str]:
    """Read `key: [a, "b"]` or a dash list under `key:`; top-level keys only."""
    m = re.search(rf"^{key}:\s*(\[.*?\])\s*$", block, re.M)
    if m:
        try:
            return [str(x) for x in json.loads(m.group(1))]
        except ValueError:
            return [s.strip().strip("\"'") for s in m.group(1).strip("[]").split(",") if s.strip()]
    m = re.search(rf"^{key}:\s*$((?:\n\s*-\s*.*)+)", block, re.M)
    if m:
        return [re.sub(r"^\s*-\s*", "", ln).strip().strip("\"'")
                for ln in m.group(1).splitlines() if ln.strip()]
    return []


def _scalar(block: str, key: str) -> str:
    m = re.search(rf"^\s*{key}:\s*\"?([^\"\n#]+?)\"?\s*(?:#.*)?$", block, re.M)
    return m.group(1).strip() if m else ""


def read_arc(camp: pathlib.Path) -> dict:
    block = arc_block(_read(camp / "state.md"))
    kind = _scalar(block, "type") or "none"
    pos = _scalar(block, "current_chapter") if kind == "structured" else _scalar(block, "current_beat")
    notes = ""
    m = re.search(r"^steering_notes:\s*>\s*\n((?:[ \t]+.*\n?)+)", block, re.M)
    if m:
        notes = " ".join(ln.strip() for ln in m.group(1).splitlines()).strip()
    telegraph = ""
    if kind == "structured":
        telegraph = _scalar(block, "telegraph_scene")
    return {
        "type": kind,
        "position": pos,
        "outstanding": _list_field(block, "outstanding_beats"),
        "steering_notes": notes,
        "telegraph_scene": telegraph,
    }


def session_number(camp: pathlib.Path) -> int:
    m = re.search(r"\*\*Session count:\*\*\s*(\d+)", _read(camp / "state.md"))
    return int(m.group(1)) if m else 0


def parse_log(camp: pathlib.Path) -> list[dict]:
    rows = []
    for ln in _read(camp / LOG_NAME).splitlines():
        parts = [p.strip() for p in ln.split("|")]
        if len(parts) < 5 or not re.match(r"^S\d+$", parts[0]):
            continue
        out = re.search(r"outstanding=(\d+)", parts[4])
        rows.append({
            "session": int(parts[0][1:]),
            "date": parts[1],
            "position": parts[2],
            "verdict": parts[3],
            "outstanding": int(out.group(1)) if out else None,
            "note": parts[5] if len(parts) > 5 else "",
            "move": parts[6] if len(parts) > 6 else "",
        })
    return rows


def analyse(rows: list[dict]) -> dict:
    """Streak of non-course verdicts, saves since progress, escalation level."""
    streak = 0
    for r in reversed(rows):
        if r["verdict"] in ("drifting", "off-arc"):
            streak += 1
        else:
            break
    stalled = 0
    for i in range(len(rows) - 1, 0, -1):
        a, b = rows[i - 1], rows[i]
        progressed = (a["position"] != b["position"]) or (
            a["outstanding"] is not None and b["outstanding"] is not None
            and b["outstanding"] < a["outstanding"])
        if progressed:
            break
        stalled += 1
    last = rows[-1]["verdict"] if rows else None
    level = 0
    if last == "off-arc" or streak >= 3 or stalled >= 4:
        level = 3
    elif streak == 2 or stalled == 3:
        level = 2
    elif streak == 1 or stalled == 2:
        level = 1
    return {"last_verdict": last, "drift_streak": streak,
            "saves_without_progress": stalled, "level": level}


LEVEL_TEXT = {
    0: "on course: nothing to do",
    1: "telegraph: plant a rumour or NPC urgency pointing at the outstanding beat",
    2: "pressure: stage a world event that makes the beat's place or person unavoidable",
    3: "decide: steer once more with a strong in-fiction pull, OR accept the players' "
       "direction and revise the arc; do not keep forcing",
}


def status(camp: pathlib.Path) -> dict:
    arc = read_arc(camp)
    rows = parse_log(camp)
    res = analyse(rows)
    res.update({
        "arc_type": arc["type"],
        "position": arc["position"],
        "outstanding": len(arc["outstanding"]),
        "telegraph_scene": arc["telegraph_scene"],
        "steering_notes": arc["steering_notes"],
        "anchors_present": bool(anchors(camp)),
        "last_move": rows[-1]["move"] if rows else "",
        "advice": LEVEL_TEXT[res["level"]],
    })
    return res


def anchors(camp: pathlib.Path) -> str:
    """Body of `## Setting Anchors` in world.md ('' when missing or empty)."""
    world = _read(camp / "world.md")
    m = ANCHOR_HEAD.search(world)
    if not m:
        return ""
    body = re.split(r"^##\s", world[m.end():], maxsplit=1, flags=re.M)[0]
    body = "\n".join(ln for ln in body.splitlines()
                     if ln.strip() and not ln.strip().startswith("*(") and ln.strip() != "---")
    return body.strip()


def record(camp: pathlib.Path, verdict: str, note: str = "", move: str = "") -> str:
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {', '.join(VERDICTS)}")
    arc = read_arc(camp)
    clean = lambda s: " ".join(s.replace("|", "/").split())  # noqa: E731
    line = (f"S{session_number(camp)} | {datetime.date.today().isoformat()} | "
            f"{arc['position'] or '-'} | {verdict} | outstanding={len(arc['outstanding'])} | "
            f"{clean(note)} | {clean(move)}\n")
    log = camp / LOG_NAME
    if not log.exists():
        log.write_text(LOG_HEADER, encoding="utf-8")
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(line)
    return line.strip()


def _camp(name: str) -> pathlib.Path:
    camp = paths.find_campaign(name) if paths else pathlib.Path(name)
    if not camp.exists():
        sys.exit(f"campaign not found: {name}")
    return camp


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("status", "record", "anchors"):
        p = sub.add_parser(name)
        p.add_argument("--campaign", required=True)
        if name == "status":
            p.add_argument("--json", action="store_true")
        if name == "record":
            p.add_argument("--verdict", required=True, choices=VERDICTS)
            p.add_argument("--note", default="")
            p.add_argument("--move", default="")
    a = ap.parse_args(argv)
    camp = _camp(a.campaign)
    if a.cmd == "record":
        print(record(camp, a.verdict, a.note, a.move))
        print(f"level {status(camp)['level']}: {status(camp)['advice']}")
    elif a.cmd == "anchors":
        text = anchors(camp)
        print(text or "no Setting Anchors in world.md: write them (see the import/new steps)")
        return 0 if text else 1
    else:
        s = status(camp)
        if a.json:
            print(json.dumps(s, ensure_ascii=False, indent=2))
        else:
            print(f"arc: {s['arc_type']} at {s['position'] or '-'}, "
                  f"{s['outstanding']} beats outstanding")
            print(f"drift: last={s['last_verdict'] or 'none yet'}, streak={s['drift_streak']}, "
                  f"saves without progress={s['saves_without_progress']}")
            print(f"level {s['level']}: {s['advice']}")
            if s["level"] and s["telegraph_scene"]:
                print(f"telegraph: {s['telegraph_scene']}")
            if not s["anchors_present"]:
                print("warning: world.md has no Setting Anchors section")
    return 0


if __name__ == "__main__":
    sys.exit(main())
