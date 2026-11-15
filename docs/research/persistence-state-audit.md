# Persistence audit: is the game saved after every DM turn and every player submit?

Backlog item: *"After every DM input, and after every players-submit, the game state should be processed and saved, so when the browser closes or the app goes back to the main menu nothing is lost."*

Method: read the display app (`skills/dnd/display/dnd-display-app.py`, `send.py`, `autorun_wait.py`, `wrapper.py`) and the DM rules (`SKILL.md`, `SKILL-commands.md`, `scripts/autosave_checkpoint.py`), then **ran** the cases against the code as it was at 2.34.0 and against the fix. `tests/test_persistence.py` has 15 cases; run against the 2.34.0 display app, **10 of them fail** (so each gap below is real and reproduced); against the fixed app all 15 pass. All runs use a temp runtime folder and a temp campaign, never real data.

## Short answer

**Partly.** Narration blocks were already saved after every block (tail + log), and a browser that reloads got everything replayed. But three things were not safe: (1) the display's own state beyond the party (NPCs here, combat board, quests, factions, time, turn order) lived in one global file shared by all campaigns, so it was lost or leaked between campaigns when you went back to the menu and loaded again; (2) player input that was typed or staged but not yet sent vanished when the display restarted, and a second submit before the DM picked up the first **overwrote** it; (3) the DM's own notes (`state.md`) were only written on "natural breaks", every ~10 turns by the optional hook, and on Save, so a long stretch of play could exist only in Claude's context. The first two are fixed in the display; the third now has an explicit one-call, per-turn rule.

## Before the fix (2.34.0)

| # | Event | What is saved | Where (file / function) | When | How data could be lost |
|---|---|---|---|---|---|
| a1 | DM block via `send.py` (narration, NPC, dice, image, XP/inspiration/milestone) | the line, with type and campaign stamp | `session_tail.json` in the campaign folder (`_persist_tail`, atomic, last 30); `text_log.json` in runtime (`_persist_log`, last 2000) | on every `/chunk`, before the browser is told | `text_log.json` was written **non-atomically**: a crash mid-write left invalid JSON, and `_load_log` swallowed the error, so a restart showed an empty screen. Tail holds only 30 lines. |
| a2 | stat flags / `push_stats.py` (party HP, slots, effects, conditions) | whole `_current_stats` | `stats.json` in runtime, **global, not per campaign** (`_persist_stats`) | every `/stats` | non-atomic write (same crash case). A push that carried `autorun_waiting` returned early **before** saving, so `npcs`/`combat` in it were not saved. |
| a3 | NPCs here, combat board, quests, factions, world time, turn order | same `_current_stats` keys | same global `stats.json` | every `/stats` | **Campaign bleed and loss:** loading another campaign replaced only the players; the rest stayed from the previous campaign. `/clear` (first step of every DM load) deleted `stats.json`, so after Save & exit and a load the board was empty until the DM re-pushed from memory. |
| a4 | live HP / slots between saves | only the display (`_current_stats`) | `stats.json` (global) | every push | The character sheets (`characters/*.md`) change only on a DM save. `/clear` + load pushed the **sheet** values (older HP). |
| b1 | Player types and stages an action | `_staged` | **memory only** (`stage_input`, `ready_input`, `skip_input`, `unstage_input`) | never | display restart or crash loses every staged line. |
| b2 | Player presses GO / `/party-input` | `[Name]: text` lines | `.input_queue` in runtime (`_check_auto_trigger`) | at once, but written with mode `"w"` | **a second submit before the DM picked up the first overwrote it.** If the write failed, `_staged` was already cleared: text gone. Consumer (`autorun_wait.py`, `wrapper.py`) read then deleted, so a line appended in between was deleted unread. |
| b3 | "Queued" indicator (`_queue_status`) | memory only | none | never | after a restart the screen no longer shows the lines as queued, though the file still holds them. |
| b4 | `/player-input` (companion queue) | `_input_queue` | `player_input.json` (`_persist_input_queue`) | every submit | non-atomic write. |
| c1 | DM continuity (`state.md` Live State Flags, Recent Events) | DM prose | `state.md` | "scene boundary" and "every several turns" (SKILL.md micro-save rule, model habit); the optional Stop hook `autosave_checkpoint.py` snapshots `state.md` every turn and asks for a micro-save every 10th | between those moments the facts exist only in Claude's context. A compaction or closed session loses up to a scene. |
| c2 | `session-log.md`, character sheets, `consequences.md`, `drift-log.md`, graph | full DM save | their own files | `/dm:dnd save`, `end`, `[Menu]: save-exit` (consequences also "on the spot" by rule) | by design only at saves; sheets older than the live display values (a4). |
| d | Browser closes or reloads mid-turn | nothing needs saving: the server holds the truth | `/stream` replays `_text_log[-200:]`, `stats`, `pending_input`, `staged_inputs`, `queue_status`, open dice requests, autorun cycle | on reconnect | worked. Text typed into the Party Input boxes but not staged is browser-only. The voice-held narration (`_heldItems` in `index.html`) is already in the log before it is shown, so it is replayed. |
| e | Save & exit, Main Menu, load again | DM runs a full save; display keeps running (`/menu/exit` only queues `[Menu]: save-exit`, and only if the DM is listening) | all of the above | on exit and on load | tail replay skipped by design (recap instead); the rows a3/a4/b1 gaps showed here: board empty, party HP from the sheet, staged text gone. |
| f | Display restarted or crashed | `stats.json`, `text_log.json`, `player_input.json`, tail, `.input_queue` are re-read at import | as above | at start | gaps a1 (torn files), a3, b1, b3. Open dice requests (`_dice_pending`) are memory only. |

## After the fix

| # | Event | Saved where | When | Remaining way to lose data |
|---|---|---|---|---|
| a1 | DM block | tail (campaign) + `text_log.json` now written **atomically** (`_atomic_write_json`); unreadable log is rebuilt from the tail at start (`_load_log`) | every `/chunk` | older than 30 lines after a log loss. |
| a2-a4 | party, NPCs, combat, quests, factions, time, turn order, live HP/slots/effects | `stats.json` (atomic) **and** `<campaign>/display_state.json` (`_persist_stats`), unreadable `stats.json` falls back to the campaign copy (`_load_stats`) | every `/stats`, including pushes that carry an autorun signal | a crash in the same millisecond as the OS losing the write (fsync is used). |
| a3 | board when switching/reloading a campaign | `_restore_campaign_display` on the `campaign` key of `/stats`: board keys come back from that campaign's copy, or are **dropped** if it has none (no bleed). `/clear` no longer costs the board (`_awaiting_restore` freezes the copy until the load read it) | on every DM load | none known. |
| a4 | live HP etc. at load | restored from the copy **only if newer than every character file**; a Save (which writes the files) wins | on load | a hand edit of a sheet within the same second as the last display change. |
| b1 | staged input | `display_state.json` (`_persist_staged` after every stage/ready/unstage/skip/GO) and restored at start (`_restore_staged_at_start`) | every change | none known. |
| b2 | submits | `.input_queue` is **appended** (`_check_auto_trigger`), fsynced; if the write fails the text goes back to `_staged`; consumers claim the file by atomic rename before reading (`autorun_wait.py`, `wrapper.py`) | at once | a DM process killed between claiming and reading (the claim file `.input_queue.taking` stays, readable by hand). |
| b3 | queued indicator | `display_state.json`, restored if `.input_queue` still exists | on change | none. |
| b4 | companion queue | atomic | every submit | none. |
| c1 | DM continuity | new `scripts/micro_save.py` (one atomic call: `--event`, `--flag`, `--unflag`); rule "Save after every turn" in SKILL.md: run it in the same Bash call as narration after every turn that changed something lasting and after every player submit acted on | each turn | **Cannot be done by the display: only Claude knows these facts.** If Claude skips the call, that turn exists only in context. The Stop hook still backs it up every 10 turns. |
| c2 | session log, sheets | unchanged (save/end/save-exit) | at saves | by design; live values are now kept by the display and restored on load. |
| d | browser reload | unchanged (works); covered by a test | | unsent text in the Party Input boxes is browser-only. |
| e | exit, menu, load | board, party live values, staged input and queued indicator come back | on load | the held-back narration is not replayed after a menu load (recap instead, by design). |
| f | restart/crash | all of the above | at start | open dice requests (`_dice_pending`) are still memory only: the DM's wait ends and it asks again. |

## Speed

The display writes a few small files per event (one stats file, one campaign copy); measured on this Windows machine (with the full test suite running alongside, so pessimistic), a stats push including its saves takes about 7 ms and a narration block about 6 ms. `micro_save.py` is one local Python call in the same shell command as `send.py`, with no model rewrite of `state.md`, so it adds well under a second and no model tokens beyond the one line of arguments.
