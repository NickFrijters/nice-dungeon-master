# Campaign drift: how the DM stays near the adventure

Backlog: "Make sure we aim towards (but not bound to) the general adventure/campaign setting, so we don't drift further with every session."

## How drift happens today

**What already pulls the DM back** (before this change):
- Structured arcs: `state.md → ## Campaign Arc` holds the current and next chapter, `key_beats`, `telegraph_scene`, `outstanding_beats`, `steering_notes`. Chapter text is read on demand from `source/<id>.md`.
- Rules for steering by world pressure, not walls; a load-time check that the chapter pointer isn't stale; a save-time rule to advance the pointer.
- Dynamic arcs: beats as consequences, `world_pressure`, a pre-emption check at `end`, `arc revise`.

**What does not exist** (the gaps):
1. **No record of drift.** Nothing says whether a session served the arc. Each session starts from the pointer as if the previous one were on course. A slow slide (three "just one more town" sessions) is invisible, because every save looks fine on its own.
2. **Structured arcs have no end-of-session arc check.** `end` step 1e is explicitly "dynamic arcs only". The only structured checks are "did the pointer advance?" at save and a stale-pointer sanity check at load. Both are binary and only notice *completion*, not *neglect*.
3. **No setting anchors.** Tone, themes and signature places live in `world.md` Tone/Premise and in the source, but nothing tells the DM to improvise *against* them. Source chapters are read only when a scene is run in that chapter, so improvised scenes elsewhere are anchored to nothing but the DM's memory of a summary. Dynamic and sandbox campaigns have only `theme` in the arc (dynamic) or nothing (sandbox).
4. **Steering has no escalation or exit.** "Apply indirect pressure" has no ladder: the DM either keeps nudging forever (railroading) or silently gives up (drift). There is no explicit rule for *accepting* the players' direction and re-aiming the arc, except dynamic `arc revise`; structured campaigns have no revise at all.
5. **Compaction erodes the aim.** After a context compaction the DM remembers the last scenes, not the pull. The pointer is re-read only at load.

## Scenarios

- **The long inn.** Test-adventure, ch. 1.1. The party skips Seek the Keep and spends two sessions in a Greenest tavern. Save says "no chapter advanced", which is true and tells nobody anything. Session 3 starts in the tavern again. *Today:* nothing changes. *Now:* S1 detour (level 0), S2 drifting (level 1: a rumour of the Keep's gate failing), S3 drifting (level 2: kobolds reach the tavern). The detour stays legit, the pressure grows from the world.
- **The friendly cultist.** Players adopt a captured cultist and travel north, away from the Raiders' Camp. This is a real choice and should win. *Today:* the DM keeps "steering" for sessions. *Now:* `off-arc` jumps to level 3: offer one strong hook; if declined, follow, and re-aim `outstanding_beats` at what the source still offers (the cultist knows the Hatchery).
- **Tone creep.** Improvised NPCs and villages drift from a child-friendly heroic tone to grimdark across sessions. *Today:* nothing compares them to the tone. *Now:* anchors list the tone and what to avoid; improvised content is checked against them.
- **Dynamic campaign stall.** Beat 2a keeps being "overdue" but each session the DM only edits `steering_notes`. *Now:* the stall counter reaches level 3 and forces a decision.

## Ranked fixes

1. **Drift verdict at every save + escalation (done).** One DM-judged line per save (`on-course | detour | drifting | off-arc`), stored in `drift-log.md` by `scripts/arc_drift.py`. The script counts the drift streak and the saves without progress (no beat landed, no chapter change) and gives an escalation level 0-3. Covers structured and dynamic arcs. Highest value: it adds the missing memory.
2. **Setting Anchors in `world.md` (done).** 5-8 bullets: tone and what to avoid, themes, key places with signature images, key people, the pull. Written at import and `new`, backfilled at load for older campaigns, re-read through `load` and used when improvising. World.md is already loaded in full, so it costs almost nothing.
3. **Bend-back procedure and "yield" rule (done).** Honour, tie, press, offer, yield: the deviation is played in full, tied to an outstanding beat, the world presses anyway, one offer, then follow the players and revise. Gives "aim, don't bind" an explicit stopping point.
4. **Structured end-of-session decision at level 3 (done, light).** `end` step e2 asks the player whether to steer back or follow, and records the divergence in `steering_notes`.
5. **Not done:** a real `arc revise` for structured campaigns (re-ordering chapters in `arc.md`, a divergence log); auto-checking improvised content against anchors (an LLM judgement, cannot be tested deterministically); re-reading the pull after a compaction (belongs with the continuity/memory session: surface `arc_drift.py status` in the continuity checkpoint).

## Design notes

- The script never judges the story: the verdict is the DM's. It only stores, counts and escalates, so it is deterministic and testable.
- Levels: 1 = streak 1 or 2 saves without progress; 2 = streak 2 or 3 stalled; 3 = `off-arc`, streak 3 or 4 stalled. A `detour` is free for the first saves, but a long run of detours still escalates through the stall counter.
- Players' direction always wins at level 3 ("follow them, revise the arc"); the story's pull is the world acting, never a wall, and the arc is never mentioned to players.
