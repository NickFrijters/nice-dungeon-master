#!/usr/bin/env python3
"""
micro_save.py: the cheap per-turn save. One call, only the fields that changed.

The display app keeps the screen state (tail, party HP and slots, NPCs here,
combat board, quests, staged player input) on disk by itself. What only the
Dungeon Master knows lives in `state.md`, and this script writes that without
rewriting the whole file and without a model round trip:

  --event "Mira picked the cellar lock; Hobb saw it."
        appends one bullet to `## Recent Events` (keeps the last 12 bullets of
        the current session; older ones stay in the session log at save time)
  --flag "NPC Hobb: hostile, wants 5 gp for the door"
        sets one line in `## Live State Flags` (replaces the line that starts
        with the same label before the colon, else adds it)
  --unflag "NPC Hobb"
        removes that flag line

Both may repeat. The write is atomic (temp file, then replace), so a crash
cannot leave a half-written state.md. Prints one line, no narration.

Usage:
  python micro_save.py -c <campaign> --event "..." [--flag "Label: text"] [--unflag "Label"]
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import find_campaign  # noqa: E402

MAX_EVENTS = 12
PLACEHOLDER = re.compile(r"^\s*\*\(.*\)\*\s*$")      # "*(none established)*", "*(Session 1 pending)*"


def _split(text: str, header: str):
    """(before, body_lines, after) for the '## header' section, or None."""
    m = re.search(r"^## " + re.escape(header) + r"[^\n]*\n", text, re.M)
    if not m:
        return None
    start = m.end()
    nxt = re.search(r"^## ", text[start:], re.M)
    end = start + nxt.start() if nxt else len(text)
    return text[:start], text[start:end].split("\n"), text[end:]


def _join(before, lines, after):
    body = "\n".join(lines).rstrip("\n") + "\n\n"
    return before + body + after.lstrip("\n") if after.strip() else before + body.rstrip("\n") + "\n"


def add_event(text: str, event: str) -> str:
    parts = _split(text, "Recent Events")
    bullet = "- " + event.strip()
    if parts is None:
        return text.rstrip("\n") + "\n\n## Recent Events\n" + bullet + "\n"
    before, lines, after = parts
    lines = [l for l in lines if l.strip() and not PLACEHOLDER.match(l)]
    lines.append(bullet)
    bullets = [i for i, l in enumerate(lines) if l.lstrip().startswith("- ")]
    if len(bullets) > MAX_EVENTS:
        drop = set(bullets[: len(bullets) - MAX_EVENTS])
        lines = [l for i, l in enumerate(lines) if i not in drop]
    return _join(before, lines, after)


def set_flag(text: str, label: str, value: str | None) -> str:
    """Set (value) or remove (value None) the Live State Flags line for `label`."""
    parts = _split(text, "Live State Flags")
    line = f"- {label}: {value}" if value is not None else None
    if parts is None:
        return text if line is None else text.rstrip("\n") + "\n\n## Live State Flags\n" + line + "\n"
    before, lines, after = parts
    key = label.strip().lower()
    out, done = [], False
    for l in lines:
        raw = l.lstrip("- ").strip()
        if ":" in raw and raw.split(":", 1)[0].strip().lower() == key:
            if line is not None and not done:
                out.append(line)
                done = True
            continue
        out.append(l)
    if line is not None and not done:
        while out and not out[-1].strip():
            out.pop()
        out.append(line)
    return _join(before, out, after)


def atomic_write(path: str, text: str) -> None:
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    os.replace(tmp, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Cheap per-turn save of the DM-only campaign state.")
    ap.add_argument("-c", "--campaign", required=True)
    ap.add_argument("--event", action="append", default=[])
    ap.add_argument("--flag", action="append", default=[], help='"Label: text"')
    ap.add_argument("--unflag", action="append", default=[], help="Label")
    a = ap.parse_args(argv)
    path = str(find_campaign(a.campaign) / "state.md")
    if not os.path.exists(path):
        print(f"micro_save: no state.md for campaign '{a.campaign}'", file=sys.stderr)
        return 1
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    crlf = "\r\n" in text
    text = text.replace("\r\n", "\n")
    for e in a.event:
        text = add_event(text, e)
    for fl in a.flag:
        if ":" not in fl:
            print(f"micro_save: --flag needs 'Label: text', got {fl!r}", file=sys.stderr)
            return 2
        label, value = fl.split(":", 1)
        text = set_flag(text, label.strip(), value.strip())
    for label in a.unflag:
        text = set_flag(text, label.strip(), None)
    if crlf:
        text = text.replace("\n", "\r\n")
    atomic_write(path, text)
    print(f"micro-saved: {len(a.event)} event(s), {len(a.flag)} flag(s), {len(a.unflag)} removed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
