#!/usr/bin/env python3
"""How fast does the DM answer? Reads latency.jsonl that the display writes for every turn: seconds from the players'
GO to the DM's first block (`first`) and to its narration (`reply`).

    python latency.py            last 20 turns + median / slowest / share within the 5-second target
    python latency.py --all      every recorded turn
"""
import argparse
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "display"))
from runtime_paths import rt   # noqa: E402

TARGET = 5.0


def load(path=None):
    path = path or rt("latency.jsonl")
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    if isinstance(r.get("reply"), (int, float)):
                        out.append(r)
                except ValueError:
                    pass
    except OSError:
        pass
    return out


def report(rows) -> str:
    if not rows:
        return "No turns measured yet: the display records one every time the players press GO and the DM answers."
    replies = [r["reply"] for r in rows]
    firsts = [r["first"] for r in rows if isinstance(r.get("first"), (int, float))]
    within = sum(1 for v in replies if v <= TARGET)
    lines = [f"{len(rows)} turns  |  narration after GO: median {statistics.median(replies):.1f} s, "
             f"slowest {max(replies):.1f} s  |  within {TARGET:.0f} s: {within}/{len(rows)} ({100 * within // len(rows)}%)"]
    if firsts:
        lines.append(f"first block on screen: median {statistics.median(firsts):.1f} s")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--file")
    a = ap.parse_args(argv)
    rows = load(a.file)
    shown = rows if a.all else rows[-20:]
    for r in shown:
        flag = "ok " if r["reply"] <= TARGET else "SLOW"
        print(f"{flag}  first {r.get('first', '?')} s   narration {r['reply']} s")
    print(report(shown))
    return 0


if __name__ == "__main__":
    sys.exit(main())
