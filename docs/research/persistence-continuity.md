# Persistence and continuity: how a fact survives (or doesn't)

Backlog item: *"If a character destroys the door of a local inn, that should have effects when the same character visits the inn again 50 sessions later. The innkeeper won't forget that!"*

Sample data: `~/.claude/dnd/campaigns/test-adventure` (1 session). Sizes below are measured there and extrapolated.

## 1. How a fact flows today

| Where | What goes in | When written | Loaded at `/dm:dnd load`? | Compressed / dropped |
|---|---|---|---|---|
| `state.md → Recent Events` | prose of the last session(s) | save | yes (whole file) | rewritten each save; old items fall off |
| `state.md → Live State Flags` | cover, faction stance, NPC disposition (one line each) | every save + micro-save | yes | lines removed when "back to baseline"; one-line reasons only |
| `state.md → Pinned Facts` | things the table says to never forget | on `/dm:dnd pin` only | yes | never, but only manual |
| `state.md → Continuity Archive` | 3-5 bullets per session older than 2 | at save, when a session leaves `session-log.md` | yes (it is inside state.md) | graph-compression rule *tells the DM to omit relational clauses*; legacy pass trims further |
| `session-log.md` | full narration of the 2 newest sessions | save/end | **no** | older entries move to `session-log-archive.md` (never read unless asked) |
| `session-log-archive.md` | everything older | append only | no | none, but nothing looks at it unless the DM searches |
| `graph.json` | typed edges (member_of, hostile_to, disposition) | micro-save, save sweep (needs a yes) | scene-context subgraph for place + present NPCs | edges can be closed; carries *relations*, not *events* |
| `npcs.md` / `npcs-full.md` | NPC index row (attitude) and full profile | on NPC creation / `npc attitude` | index only; full entry on demand | attitude is one word, no memory of events |
| `world.md` | foundations, factions | at creation | in full | static |
| `characters/*.md` | sheet, resources | save | yes | n/a |
| `session_tail.json` | last display lines | continuously | replayed to the display | overwritten |

Nothing in this table is a place or NPC keyed list of *what the party did there*.

## 2. The inn door, walked through 50 sessions

Session 3: Balasar smashes the door of the Gilded Stag. Innkeeper Hobb is furious.

1. **Same session.** The DM narrates it. It sits in context only. If compaction happens, it survives only as a summary.
2. **Micro-save / save, session 3.** Live State Flags has a slot for *NPC disposition* ("Hobb: hostile, door smashed"). Optimistically it gets written. A minor NPC with a minor grudge often does not qualify as "changed or notable standing", so often nothing is written. Graph sweep: `hostile_to` edge from Hobb to the party is possible, but "door broken, wants 5 gp" has no edge type. The prose goes to `session-log.md`.
3. **Session 4-5.** The log entry is older than 2 entries and is archived. The archive bullet is limited to 3-5 bullets for the *whole session*; the door competes with plot beats, XP and items. Likely dropped. The graph rule says to drop relational restatements, which the door arguably is.
4. **Session 6+.** The Live State Flags line for Hobb is removed once "back to baseline", or never existed. `session-log-archive.md` is not loaded and nobody searches it.
5. **Session 53.** Party walks in. At load the DM sees: no Hobb line, no archive bullet, no graph edge. Scene-context shows Hobb as a neutral innkeeper. The door is a pristine door.

Failure modes found (ranked by how often they will bite):

1. **No home for local consequences.** Live State Flags is for stances; Pinned Facts is manual; the archive is chronological and lossy. A consequence tied to a place is nowhere.
2. **Archive is a 3-5 bullet filter, per session, written in one pass.** Small consequences lose to plot beats, and the graph-compression rule removes more.
3. **No lookup keyed by place or NPC.** The only recall is "check Continuity Archive first" when a *DM already remembers* something resurfaces; nothing prompts it on entering the inn.
4. **Scene entry has no consult step.** Load runs graph `scene-context` for the opening location only; later moves rely on the DM remembering to look.
5. **Sweep is batched at save, not when it happens.** After a compaction or a crash the consequence is gone (autosave backstop did not mention consequences).
6. **Search skipped it.** `campaign_search.py` searched state/log/archive/world/npcs only.
7. **Compaction rule for dispositions removes lines at "baseline"**, so a resolved grudge and an unresolved one look the same once the line is gone.

## 3. Size and token limits

- `test-adventure` (1 session): `state.md` 6.6 kB (~1.7k tokens), `world.md` 6.1 kB, `npcs.md` 1.7 kB, `graph.json` 7.3 kB, session-log 0.8 kB.
- Everything the load reads in full is `state.md + world.md + npcs.md + characters`. `state.md` grows by about 0.4-0.6 kB per session through the archive: ~25 kB (~6k tokens) by session 50, ~50 kB by session 100, with `Recent Events` and Live State Flags on top. It is still affordable, but that is exactly why the archive is kept to a few bullets, and why consequences cannot simply be appended there.
- Ledger cost: one entry is about 40-60 tokens. Even 300 entries over 100 sessions is ~15k tokens as a file, but it is **never read in full**: `scene` returns only entries touching the place and the NPCs present, typically 0-5 lines.

## 4. Ranked fixes

1. **Consequence ledger** keyed by place / NPC / faction (done, see below). Fixes failure modes 1, 2, 3, 7.
2. **Scene-entry consult** in skill text: run `consequences.py scene` when the party enters a place or meets a named NPC, not only at load (done). Fixes 3, 4.
3. **Record on the spot** (micro-save) plus a save sweep that needs no confirmation, and an archive guard: before a log entry is archived, its consequences must be in the ledger (done). Fixes 2, 5.
4. **Autosave hook text** now mentions the ledger (done). Fixes 5 when the hook is installed.
5. **Search** includes the ledger (done). Fixes 6.
6. *Not done:* seed the ledger from existing campaigns (`session-log-archive.md` backfill). Needs judgment per entry; best as an LLM-assisted one-off command.
7. *Not done:* graph edge type `consequence_of` or auto-link of ledger subjects to graph node ids; names are matched by case-insensitive substring today, which can miss an NPC who changes name (use `npc_rename.py`, then edit the ledger line).
8. *Not done:* a `Live State Flags` rule to stop dropping a disposition line while a related ledger entry is open.
9. *Not done:* a display panel showing open consequences for the current scene (display files are owned by another session).

## 5. What was built

- `skills/dnd/scripts/consequences.py`: plain-markdown ledger `<campaign>/consequences.md`, one line per entry `id | S<session> | open/resolved | subjects | by <who> | text`. Commands `add`, `scene`, `list-for`, `search`, `resolve`, `list`, `stats`. Entries are never deleted. Session number comes from `state.md`. Hand-editable. Matching is case-insensitive, either name may contain the other, and whole-word matches in the text count (so an NPC mentioned in a line, but not a subject, still surfaces).
- Skill text: SKILL.md (a consequence-ledger rule in Active DM Mode, and the directory layout), SKILL-commands.md (load step 5 and a save sweep paragraph), SKILL-scripts.md (syntax), `autosave_checkpoint.py` (hook reason), `campaign_search.py` (default files).
- Tests: `tests/test_consequences.py`, including the 50-session inn-door case.

Inn door after the fix: session 3 the DM runs `add --subject "Gilded Stag Inn" --subject "Hobb Brewer" --by Balasar --text "Smashed the front door..."`. Session 53 the party enters; the DM runs `scene --place "Gilded Stag Inn" --present "Hobb Brewer"` and gets the line back, so Hobb mentions the door.
