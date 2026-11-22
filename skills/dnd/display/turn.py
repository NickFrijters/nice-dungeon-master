#!/usr/bin/env python3
"""One fast DM turn in ONE call and ONE process (instead of a send.py / push_stats.py / micro_save.py call each).

Order on screen: the player's line, the dice lines, the narration (with its stat changes), NPC lines; then the
micro-save, the turn pointer, and optionally the autorun wait for the next players' input.

    python turn.py -c <campaign> \\
        --player "Dagna Gloedbaard: Dagna hurls a Fire Bolt at the second swarm." \\
        --dice "Dagna Gloedbaard — Fire Bolt vs Rattenzwerm 2 (AC 10): d20+5 = 17 → hit" \\
        --narrate "The bolt bursts among the rats; a dozen flee burning into the webs." \\
        --stat-hp "Dagna Gloedbaard:11:11"  (any send.py --stat-*/--effect-* flag is passed to the narration) \\
        --npc "Escobert: Hurry, the grate!" \\
        --event "Dagna burned swarm 2 (9 dmg)" [--flag "Label: text"] [--unflag Label] \\
        --turn-current "Rattenzwerm 2" [--turn-round 3] \\
        [--wait]            (then wait for the next players' input and print it, like autorun_wait.py)

--narrate - reads the narration from stdin. Every step that fails is reported on stderr; the others still run.
"""
import argparse
import contextlib
import io
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import push_stats   # noqa: E402
import send         # noqa: E402


def _run_send(argv, text):
    """send.py main() in this process, with its own argv and stdin."""
    old_argv, old_stdin = sys.argv, sys.stdin
    sys.argv, sys.stdin = ["send.py"] + argv, io.StringIO(text)
    try:
        send.main()
    except SystemExit as e:
        if e.code not in (None, 0):
            print(f"turn.py: send {argv[:1]} failed ({e.code})", file=sys.stderr)
    finally:
        sys.argv, sys.stdin = old_argv, old_stdin


def _split(spec):
    name, _, line = spec.partition(":")
    return name.strip(), line.strip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-c", "--campaign")
    ap.add_argument("--player", action="append", default=[], help='"Name: what they do" (shown first)')
    ap.add_argument("--dice", action="append", default=[], help="a dice line, already rolled")
    ap.add_argument("--narrate", help="the narration text, or - for stdin")
    ap.add_argument("--npc", action="append", default=[], help='"Name: what they say"')
    ap.add_argument("--event", action="append", default=[])
    ap.add_argument("--flag", action="append", default=[])
    ap.add_argument("--unflag", action="append", default=[])
    ap.add_argument("--turn-current")
    ap.add_argument("--turn-round", type=int)
    ap.add_argument("--wait", action="store_true")
    a, stat_flags = ap.parse_known_args(argv)

    for spec in a.player:
        name, line = _split(spec)
        if name and line:
            _run_send(["--player", name], line)
    for line in a.dice:
        _run_send(["--dice"], line)
    if a.narrate is not None:
        text = sys.stdin.read() if a.narrate == "-" else a.narrate
        if text.strip():
            _run_send(stat_flags, text)
    elif stat_flags:
        print(f"turn.py: stat flags without --narrate are ignored: {stat_flags}", file=sys.stderr)
    for spec in a.npc:
        name, line = _split(spec)
        if name and line:
            _run_send(["--npc", name], line)

    if a.campaign and (a.event or a.flag or a.unflag):
        try:
            import micro_save
            args = ["-c", a.campaign]
            for e in a.event:
                args += ["--event", e]
            for f in a.flag:
                args += ["--flag", f]
            for u in a.unflag:
                args += ["--unflag", u]
            with contextlib.redirect_stdout(io.StringIO()):
                micro_save.main(args)
        except Exception as e:      # the turn is on screen already; a failed save must not hide that
            print(f"turn.py: micro-save failed: {e}", file=sys.stderr)

    if a.turn_current or a.turn_round is not None:
        payload = {}
        if a.turn_current:
            payload["turn_current"] = a.turn_current
        if a.turn_round is not None:
            payload["turn_round"] = a.turn_round
        old = sys.argv
        sys.argv = ["push_stats.py"] + (["--turn-current", a.turn_current] if a.turn_current else []) + \
                   (["--turn-round", str(a.turn_round)] if a.turn_round is not None else [])
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                push_stats.main()
        except SystemExit as e:
            if e.code not in (None, 0):
                print(f"turn.py: turn pointer failed ({e.code})", file=sys.stderr)
        finally:
            sys.argv = old

    if a.wait:
        sys.stdout.flush()
        runpy.run_path(os.path.join(HERE, "autorun_wait.py"), run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main())
