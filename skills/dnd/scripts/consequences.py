#!/usr/bin/env python3
"""
consequences.py — durable world-consequence ledger for a campaign.

Why: "the party smashed the inn door in session 3" must still matter in session
53. Prose summaries (Recent Events, the Continuity Archive) get compressed and
fall out of the hot context; the relationship graph holds who-relates-to-whom
but not "what happened here". This ledger holds one line per lasting consequence,
keyed by the places / NPCs / factions it touches, and is queried when the party
enters a place or meets an NPC. Entries are never deleted, only resolved.

File: <campaign>/consequences.md (plain markdown, hand-editable). One entry per line:

    - C3 | S2 | open | Gilded Stag Inn; Hobb Brewer | by Balasar | Smashed the front door in a brawl; Hobb wants it paid for.

Fields: id | session | status | subjects (; separated) | by who | what happened.

CLI (all take -c/--campaign):
  add      --subject S [--subject S2 ...] --text T [--by WHO] [--session N]
  scene    --place P [--present A,B] [--faction F,G]   consequences for a scene entry
  list-for --subject S [--subject S2]                   everything touching S
  search   KEYWORD [KEYWORD ...]                        AND keyword search over the ledger
  resolve  --id C3 [--note T] [--session N]             mark handled (kept, shown last)
  list     [--status open|resolved]
  stats                                                 size summary
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from paths import find_campaign  # noqa: E402
from utf8io import read_text  # noqa: E402

FILENAME = "consequences.md"
HEADER = (
    "# Consequences: {name}\n"
    "*Durable world consequences, one per line: id | session | status | subjects | by | what. "
    "Read at scene entry (`consequences.py scene`); never delete, resolve instead.*\n\n"
)
_LINE = re.compile(r"^- (C\d+) \| S(\d+) \| (open|resolved) \| ([^|]*) \| by ([^|]*) \| (.*)$")


def _path(campaign):
    return find_campaign(campaign) / FILENAME


def _clean(s):
    return re.sub(r"\s+", " ", str(s).replace("|", "/")).strip()


def load(campaign):
    """Return list of entry dicts in file order."""
    p = _path(campaign)
    if not p.exists():
        return []
    out = []
    for line in read_text(p).splitlines():
        m = _LINE.match(line.strip())
        if m:
            out.append({
                "id": m.group(1), "session": int(m.group(2)), "status": m.group(3),
                "subjects": [s.strip() for s in m.group(4).split(";") if s.strip()],
                "by": m.group(5).strip(), "text": m.group(6).strip(),
            })
    return out


def _fmt(e):
    return "- {id} | S{session} | {status} | {subs} | by {by} | {text}".format(
        subs="; ".join(e["subjects"]), **e)


def _write(campaign, entries):
    p = _path(campaign)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = HEADER.format(name=campaign) + "\n".join(_fmt(e) for e in entries) + "\n"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(body, encoding="utf-8", newline="\n")
    os.replace(tmp, p)


def current_session(campaign):
    """Session number from the state.md header; 1 if unreadable."""
    p = find_campaign(campaign) / "state.md"
    try:
        m = re.search(r"\*\*Session count:\*\*\s*(\d+)", read_text(p))
        return max(1, int(m.group(1))) if m else 1
    except Exception:
        return 1


def add(campaign, subjects, text, by="the party", session=None):
    subs = [_clean(s) for s in subjects if _clean(s)]
    if not subs or not _clean(text):
        raise ValueError("need at least one subject and some text")
    entries = load(campaign)
    n = max([int(e["id"][1:]) for e in entries] + [0]) + 1
    e = {"id": f"C{n}", "session": session or current_session(campaign), "status": "open",
         "subjects": subs, "by": _clean(by) or "the party", "text": _clean(text)}
    entries.append(e)
    _write(campaign, entries)
    return e


def resolve(campaign, cid, note="", session=None):
    entries = load(campaign)
    for e in entries:
        if e["id"].upper() == cid.upper():
            e["status"] = "resolved"
            tail = f": {_clean(note)}" if note else ""
            e["text"] += f" ↳ resolved S{session or current_session(campaign)}{tail}"
            _write(campaign, entries)
            return e
    raise KeyError(cid)


def _hit(query, entry):
    q = query.strip().lower()
    if len(q) < 3:
        return False
    for s in entry["subjects"]:
        sl = s.lower()
        if q in sl or sl in q:
            return True
    return re.search(r"\b" + re.escape(q) + r"\b", entry["text"].lower()) is not None


def for_subjects(campaign, queries):
    qs = [q for q in queries if q.strip()]
    hits = [e for e in load(campaign) if any(_hit(q, e) for q in qs)]
    # open first, then newest first
    return sorted(hits, key=lambda e: (e["status"] != "open", -e["session"], e["id"]))


def search(campaign, keywords):
    kws = [k.lower() for k in keywords]
    return [e for e in load(campaign) if all(k in _fmt(e).lower() for k in kws)]


def _show(entries, empty="(no consequences recorded)"):
    if not entries:
        print(empty)
        return
    for e in entries:
        print(f"[{e['id']}] S{e['session']} {e['status'].upper()} · {'; '.join(e['subjects'])}"
              f" · by {e['by']}: {e['text']}")


def _csv(s):
    return [x.strip() for x in (s or "").split(",") if x.strip()]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-c", "--campaign", required=True)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("add")
    s.add_argument("--subject", action="append", default=[])
    s.add_argument("--text", required=True)
    s.add_argument("--by", default="the party")
    s.add_argument("--session", type=int)
    s = sub.add_parser("scene")
    s.add_argument("--place", default="")
    s.add_argument("--present", default="")
    s.add_argument("--faction", default="")
    s = sub.add_parser("list-for")
    s.add_argument("--subject", action="append", required=True)
    s = sub.add_parser("search")
    s.add_argument("keywords", nargs="+")
    s = sub.add_parser("resolve")
    s.add_argument("--id", required=True)
    s.add_argument("--note", default="")
    s.add_argument("--session", type=int)
    s = sub.add_parser("list")
    s.add_argument("--status", choices=["open", "resolved"])
    sub.add_parser("stats")
    a = ap.parse_args(argv)

    try:
        if a.cmd == "add":
            e = add(a.campaign, a.subject, a.text, a.by, a.session)
            print(f"added {e['id']} (S{e['session']}): {'; '.join(e['subjects'])}")
        elif a.cmd == "scene":
            qs = ([a.place] if a.place else []) + _csv(a.present) + _csv(a.faction)
            _show(for_subjects(a.campaign, qs), "(no consequences for this scene)")
        elif a.cmd == "list-for":
            _show(for_subjects(a.campaign, a.subject))
        elif a.cmd == "search":
            _show(search(a.campaign, a.keywords), "(no matches)")
        elif a.cmd == "resolve":
            e = resolve(a.campaign, a.id, a.note, a.session)
            print(f"resolved {e['id']}")
        elif a.cmd == "list":
            es = load(a.campaign)
            _show([e for e in es if not a.status or e["status"] == a.status])
        elif a.cmd == "stats":
            es = load(a.campaign)
            size = len("\n".join(_fmt(e) for e in es))
            print(f"{len(es)} entries ({sum(e['status'] == 'open' for e in es)} open), "
                  f"{size} chars (~{size // 4} tokens)")
    except (ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
