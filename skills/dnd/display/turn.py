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
        [--combat '{...}'] [--turn-order '{...}']   (the battle board, pushed first, in the same call)
        [--resist half|double]                      (with --damage: swarms and resistances)
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


def _local_roll(notation):
    """(total, natural d20 or None) with advantage/disadvantage, like dice.py."""
    import dice
    num, die, mod, keep_mode, keep_count, adv, dis = dice.parse_notation(notation)
    if die == 20 and num == 1 and (adv or dis):
        a, b = dice.roll_dice(1, 20)[0], dice.roll_dice(1, 20)[0]
        nat = max(a, b) if adv else min(a, b)
        return nat + mod, nat, f"[{a}, {b}] keep {nat}"
    rolls = dice.roll_dice(num, die)
    if keep_mode and keep_count:
        rolls = sorted(rolls, reverse=(keep_mode == "kh"))[:keep_count]
    nat = rolls[0] if (die == 20 and num == 1) else None
    return sum(rolls) + mod, nat, "[" + ", ".join(map(str, rolls)) + "]"


def _player_roll(name, notation, label, vs, timeout=300):
    """Ask the player to roll on the display and wait for their number (roll_mode: players)."""
    import json
    import time
    import urllib.request
    import dice
    num, die, mod, *_rest = dice.parse_notation(notation)
    adv = "advantage" if "adv" in notation else ("disadvantage" if "dis" in notation else "normal")
    token = send._read_token()
    hdr = {"Content-Type": "application/json", **({"X-DND-Token": token} if token else {})}
    body = {"characters": [name], "spec": f"{num}d{die}", "modifier": mod, "advantage": adv, "label": label or ""}
    if vs is not None:
        body["dc"] = vs
    r = urllib.request.urlopen(urllib.request.Request(send.BASE_URL + "/dice-request", data=json.dumps(body).encode(),
                                                      headers=hdr, method="POST"), timeout=10, context=send._SSL_CTX)
    rid = json.loads(r.read().decode() or "{}").get("request_id", "")
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = urllib.request.urlopen(urllib.request.Request(f"{send.BASE_URL}/dice-request/{rid}", headers=hdr),
                                   timeout=10, context=send._SSL_CTX)
        st = json.loads(s.read().decode() or "{}")
        res = (st.get("results") or {})
        hit = next((v for k, v in res.items() if k.lower() == name.lower()), None)
        if hit:
            return hit["total"], hit.get("natural")
        time.sleep(0.25)
    raise TimeoutError(f"no roll from {name} within {timeout} s")


def _resolve(a):
    """Roll (or wait for the player's roll), decide hit or miss, roll damage on a hit, choose the narration.
    Returns (dice_line or None, narration, extra_stat_flags, summary)."""
    attack = a.damage is not None
    if a.ask:
        total, nat = _player_roll(a.ask, a.roll, a.label, a.vs)
        shown = None                                        # the display already shows the player's roll
    else:
        total, nat, detail = _local_roll(a.roll)
        shown = detail
    if attack and nat == 20:
        ok, crit = True, True
    elif attack and nat == 1:
        ok, crit = False, False
    else:
        ok, crit = (a.vs is None or total >= a.vs), False
    dmg = 0
    if ok and attack:
        import dice
        num, die, mod, *_rest = dice.parse_notation(a.damage)
        rolls = dice.roll_dice(num * (2 if crit else 1), die)
        dmg = max(0, sum(rolls) + mod)
        resist = getattr(a, "resist", None)
        if resist == "half":
            dmg //= 2
        elif resist == "double":
            dmg *= 2
    verdict = ("critical hit" if crit else ("hit" if attack else "success")) if ok else ("miss" if attack else "failure")
    vs_txt = f" vs {a.vs}" if a.vs is not None else ""
    nat_txt = f" (natural {nat})" if nat in (1, 20) else ""
    line = None
    if shown is not None:
        line = f"{a.label or 'Roll'}: {a.roll}{vs_txt} = {shown} = {total}{nat_txt} → {verdict}" + (f", {dmg} damage" if dmg else "")
    elif a.label:
        line = f"{a.label}: {total}{vs_txt}{nat_txt} → {verdict}" + (f", {dmg} damage" if dmg else "")
    text = (a.hit if ok else a.miss) or ""
    text = text.replace("{dmg}", str(dmg)).replace("{total}", str(total))
    extra = []
    if ok and dmg and a.hp_on_hit:                          # the target is a PC: lower their HP on the sheet card
        name, cur, mx = (a.hp_on_hit.rsplit(":", 2) + ["", ""])[:3]
        try:
            extra = ["--stat-hp", f"{name}:{max(0, int(cur) - dmg)}:{int(mx)}"]
        except ValueError:
            pass
    summary = f"ROLL total={total} natural={nat} {verdict}" + (f" damage={dmg}" if attack else "")
    return line, text, extra, summary


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
    ap.add_argument("--combat", help="battle board JSON (same as push_stats.py --combat), pushed with this turn")
    ap.add_argument("--turn-order", help="turn order JSON (same as push_stats.py --turn-order), pushed with this turn")
    ap.add_argument("--resist", choices=["half", "double"], help="target resists (half damage, rounded down) or is vulnerable (double)")
    ap.add_argument("--pause", metavar="NEXT", help='beat by beat: show a Continue button ("Next: the kobolds attack") '
                                                     'and, with --wait, go on only when the table presses it')
    # roll and resolve in the same call: write both outcomes, the script picks one
    ap.add_argument("--roll", help='dice for the action, e.g. "d20+5" or "d20+5 adv"')
    ap.add_argument("--vs", type=int, help="AC or DC to beat (equal or higher succeeds)")
    ap.add_argument("--label", help='dice line label, e.g. "Goblin 1 — Scimitar vs Dagna (AC 14)"')
    ap.add_argument("--damage", help='damage on a hit, e.g. "1d6+2" (doubled dice on a natural 20)')
    ap.add_argument("--hit", help="narration on a hit/success; {dmg} and {total} are filled in")
    ap.add_argument("--miss", help="narration on a miss/failure")
    ap.add_argument("--ask", metavar="NAME", help="a player rolls it on the display (roll_mode: players); waits for the number")
    ap.add_argument("--hp-on-hit", metavar="NAME:CUR:MAX", help="target PC's HP now: lowered by the damage on a hit")
    a, stat_flags = ap.parse_known_args(argv)

    resolved = None
    if a.roll:
        try:
            resolved = _resolve(a)
        except Exception as e:
            print(f"turn.py: roll failed: {e}", file=sys.stderr)
            return 2

    if a.combat or a.turn_order:
        old_argv = sys.argv
        sys.argv = ["push_stats.py"] + (["--turn-order", a.turn_order] if a.turn_order else []) +                    (["--combat", a.combat] if a.combat else [])
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                push_stats.main()
        except SystemExit as e:
            if e.code not in (None, 0):
                print(f"turn.py: board push failed ({e.code})", file=sys.stderr)
        finally:
            sys.argv = old_argv

    for spec in a.player:
        name, line = _split(spec)
        if name and line:
            _run_send(["--player", name], line)
    for line in a.dice:
        _run_send(["--dice"], line)
    if resolved and resolved[0]:
        _run_send(["--dice"], resolved[0])
    if resolved and resolved[1].strip():
        _run_send(stat_flags + resolved[2], resolved[1])
    elif a.narrate is not None:
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

    if a.pause:
        import json
        import urllib.request
        try:
            token = send._read_token()
            urllib.request.urlopen(urllib.request.Request(
                send.BASE_URL + "/beat/pause", data=json.dumps({"label": a.pause}).encode(), method="POST",
                headers={"Content-Type": "application/json", **({"X-DND-Token": token} if token else {})}),
                timeout=10, context=send._SSL_CTX)
        except Exception as e:
            print(f"turn.py: could not show the Continue button: {e}", file=sys.stderr)
    if resolved:
        print(resolved[3], flush=True)            # the outcome, for the DM's own bookkeeping
    if a.wait:
        sys.stdout.flush()
        runpy.run_path(os.path.join(HERE, "autorun_wait.py"), run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main())
