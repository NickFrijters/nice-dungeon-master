"""Persistence: nothing is lost when the display restarts, the browser reloads, or the
player goes back to the Main Menu and loads the campaign again.

Every test runs against a throwaway runtime folder and a throwaway campaign
(never the real data folder, never a real campaign). "Restarting the display" is
a fresh import of the display module over the same files.
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
SCRIPTS = REPO / "skills" / "dnd" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(DISPLAY))

import paths  # noqa: E402

CAMP = "persist-test"
OTHER = "persist-other"
_n = itertools.count()

SHEET = """# Mira

**Race:** Human | **Class:** Fighter | **Level:** 3
**HP:** 20/30 | **AC:** 16
"""


class PersistenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        (cls.root / ".runtime").mkdir()
        cls._env = {k: os.environ.get(k) for k in ("DND_CAMPAIGN_ROOT", "DND_RUNTIME_DIR")}
        os.environ["DND_CAMPAIGN_ROOT"] = str(cls.root)
        os.environ["DND_RUNTIME_DIR"] = str(cls.root / ".runtime")
        import runtime_paths
        cls._patches = [mock.patch.object(paths, "_root", lambda: cls.root),
                        mock.patch.object(runtime_paths, "RT", str(cls.root / ".runtime"))]
        for p in cls._patches:
            p.start()

    @classmethod
    def tearDownClass(cls):
        for p in cls._patches:
            p.stop()
        for k, v in cls._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        cls.tmp.cleanup()

    def setUp(self):
        for name in (CAMP, OTHER):
            d = self.root / "campaigns" / name
            (d / "characters").mkdir(parents=True, exist_ok=True)
            (d / "characters" / "Mira.md").write_text(SHEET, encoding="utf-8")
            (d / "state.md").write_text("# State\n", encoding="utf-8")
        rt = self.root / ".runtime"
        for f in rt.iterdir():
            f.unlink()
        for name in (CAMP, OTHER):
            (self.root / "campaigns" / name / "display_state.json").unlink(missing_ok=True)
            (self.root / "campaigns" / name / "session_tail.json").unlink(missing_ok=True)
        (rt / ".campaign").write_text(CAMP, encoding="utf-8")
        self.mod = self.start_display()
        self.client = self.mod.app.test_client()

    def start_display(self):
        """A fresh display process: the module is executed again over the same files."""
        spec = importlib.util.spec_from_file_location(f"display_persist_{next(_n)}", DISPLAY / "dnd-display-app.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        assert str(self.root) in mod.QUEUE_FILE, "test would touch the real runtime folder"
        return mod

    def restart(self):
        self.mod = self.start_display()
        self.client = self.mod.app.test_client()

    # ---- helpers -------------------------------------------------------
    def push_party(self, camp=CAMP, **extra):
        body = {"campaign": camp, "replace_players": True,
                "players": [{"name": "Mira", "hp": {"current": 20, "max": 30, "temp": 0}},
                            {"name": "Joris", "hp": {"current": 9, "max": 12, "temp": 0}}]}
        body.update(extra)
        r = self.client.post("/stats", json=body)
        self.assertEqual(r.status_code, 204)

    def board(self):
        return {"npcs": [{"name": "Hobb", "role": "innkeeper", "disposition": "hostile"}],
                "combat": {"enemies": [{"name": "Goblin", "hp": 4, "max": 7}], "map": None},
                "quests": [{"name": "Find the cellar", "status": "active"}],
                "factions": [{"name": "Gilded Stag", "standing": "Hostile"}],
                "world_time": {"date": "3 Mirtul", "time": "late evening"},
                "turn_order": {"current": "Mira", "order": ["Mira", "Goblin"]}}

    def replay(self, wait="stats", client=None):
        """What a browser that just (re)connected is sent first."""
        resp = (client or self.client).get("/stream", buffered=False)
        seen = {}
        it = iter(resp.response)
        for chunk in it:
            text = chunk.decode() if isinstance(chunk, bytes) else chunk
            if text.startswith("data: "):
                for k, v in json.loads(text[6:]).items():
                    seen[k] = v
            if "input_gate" in seen and wait in seen:
                break
            if len(seen) > 12:
                break
        resp.close()
        return seen

    # ---- (a) display state survives a restart ---------------------------
    def test_restart_brings_everything_back(self):
        self.push_party(**self.board())
        self.client.post("/chunk", json={"text": "The door splinters."})
        self.client.post("/chunk", json={"text": "Who goes there?", "npc": "Hobb"})
        self.client.post("/chunk", json={"text": "d20 = 17", "dice": True})
        self.client.post("/chunk", json={"image": {"src": "scene.png", "caption": "The inn"}})
        before = json.loads(json.dumps(self.mod._current_stats))

        self.restart()

        after = self.mod._current_stats
        self.assertEqual(after, before)
        for key in ("npcs", "combat", "quests", "factions", "world_time", "turn_order", "players"):
            self.assertIn(key, after)
        tail = json.loads((self.root / "campaigns" / CAMP / "session_tail.json").read_text(encoding="utf-8"))
        self.assertEqual([t.get("text") for t in tail],
                         ["The door splinters.", "Who goes there?", "d20 = 17", "The inn"])
        # the browser replay after the restart carries the same lines and the same board
        seen = self.replay()
        self.assertEqual([b.get("text") for b in seen["replay_batch"]],
                         ["The door splinters.", "Who goes there?", "d20 = 17", "The inn"])
        self.assertEqual(seen["stats"]["npcs"][0]["name"], "Hobb")
        self.assertEqual(seen["stats"]["combat"]["enemies"][0]["name"], "Goblin")

    def test_unreadable_files_do_not_blank_the_screen(self):
        self.push_party(**self.board())
        self.client.post("/chunk", json={"text": "A line that must not vanish."})
        # a crash that left both runtime files half written
        Path(self.mod.STATS_FILE).write_text('{"players": [{"na', encoding="utf-8")
        Path(self.mod.LOG_FILE).write_text('[{"text": "A li', encoding="utf-8")
        self.restart()
        self.assertEqual([p["name"] for p in self.mod._current_stats["players"]], ["Mira", "Joris"])
        self.assertEqual(self.mod._current_stats["npcs"][0]["name"], "Hobb")
        self.assertEqual([e["text"] for e in self.mod._text_log], ["A line that must not vanish."])

    def test_writes_are_atomic_no_temp_files_left(self):
        self.push_party()
        self.client.post("/chunk", json={"text": "x"})
        leftovers = [p.name for p in (self.root / ".runtime").iterdir() if p.name.endswith(".tmp")]
        leftovers += [p.name for p in (self.root / "campaigns" / CAMP).iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    # ---- (b) player input ----------------------------------------------
    def test_staged_input_survives_a_restart(self):
        self.push_party()
        r = self.client.post("/player-input/stage", json={"character": "Mira", "text": "I open the cellar door"},
                             headers={"X-DND-Device": "d1"})
        self.assertIn(r.status_code, (204, 202))
        if r.status_code == 202:                       # LAN device approval is not what is tested here
            self.mod._staged["Mira"] = {"text": "I open the cellar door", "ready": False, "timestamp": 1.0}
            self.mod._persist_staged()
        self.restart()
        self.assertEqual(self.mod._staged["Mira"]["text"], "I open the cellar door")
        self.assertEqual(self.replay("staged_inputs")["staged_inputs"]["Mira"]["text"], "I open the cellar door")

    def test_a_second_submit_does_not_erase_the_first(self):
        self.push_party()
        hdr = {"X-DND-Device": "d1"}
        self.client.post("/party-input", json={"entries": [{"character": "Mira", "text": "I attack."}]}, headers=hdr)
        self.client.post("/party-input", json={"entries": [{"character": "Joris", "text": "I heal her."}]}, headers=hdr)
        lines = Path(self.mod.QUEUE_FILE).read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines, ["[Mira]: I attack.", "[Joris]: I heal her."])

    def test_submitted_input_survives_a_restart_until_the_dm_takes_it(self):
        self.push_party()
        self.client.post("/party-input", json={"entries": [{"character": "Mira", "text": "I attack."}]},
                         headers={"X-DND-Device": "d1"})
        self.restart()
        self.assertEqual(Path(self.mod.QUEUE_FILE).read_text(encoding="utf-8").splitlines(), ["[Mira]: I attack."])
        self.assertEqual(self.mod._queue_status, ["Mira"])
        self.assertEqual(self.replay("queue_status")["queue_status"], ["Mira"])

    def test_player_input_queue_survives_a_restart(self):
        self.client.post("/player-input", json={"character": "Mira", "text": "I wait"})
        self.restart()
        self.assertEqual(self.mod._input_queue[0]["text"], "I wait")

    def test_failed_queue_write_keeps_the_players_text(self):
        self.push_party()
        self.mod._staged["Mira"] = {"text": "I attack.", "ready": True, "timestamp": 1.0}
        real_open = open

        def fail_queue(path, *a, **k):
            if str(path) == self.mod.QUEUE_FILE:
                raise OSError("disk full")
            return real_open(path, *a, **k)

        with mock.patch("builtins.open", fail_queue):
            self.mod._check_auto_trigger(force=True)
        self.assertEqual(self.mod._staged["Mira"]["text"], "I attack.")

    # ---- (d) browser reload mid-turn -------------------------------------
    def test_browser_reload_replays_lines_board_and_pending_state(self):
        self.push_party(**self.board())
        for i in range(3):
            self.client.post("/chunk", json={"text": f"Narration {i}"})
        self.client.post("/player-input", json={"character": "Mira", "text": "pending"})
        seen = self.replay("pending_input")
        self.assertEqual([b["text"] for b in seen["replay_batch"]], ["Narration 0", "Narration 1", "Narration 2"])
        self.assertEqual(seen["stats"]["quests"][0]["name"], "Find the cellar")
        self.assertEqual(seen["stats"]["turn_order"]["current"], "Mira")
        self.assertEqual(seen["pending_input"][0]["text"], "pending")

    # ---- (e) Save & exit, back to the menu, load again ---------------------
    def test_clear_then_load_again_restores_the_board(self):
        self.push_party(**self.board())
        self.client.post("/chunk", json={"text": "Last line before exit."})
        self.client.post("/clear")                              # what a DM load does first
        self.assertEqual(self.mod._current_stats, {})
        self.push_party()                                       # then the DM pushes the sheets
        st = self.mod._current_stats
        self.assertEqual(st["npcs"][0]["name"], "Hobb")
        self.assertEqual(st["combat"]["enemies"][0]["hp"], 4)
        self.assertEqual(st["turn_order"]["current"], "Mira")
        self.assertEqual(st["world_time"]["time"], "late evening")

    def test_live_hp_beats_an_older_sheet_but_not_a_newer_one(self):
        self.push_party()
        self.client.post("/stats", json={"players": [{"name": "Mira", "hp": {"current": 5, "max": 30, "temp": 0}}]})
        sheet = self.root / "campaigns" / CAMP / "characters" / "Mira.md"
        os.utime(sheet, (1, 1))                                 # the sheet was last saved long ago
        self.client.post("/clear")
        self.push_party()                                       # sheet says 20/30
        hp = {p["name"]: p["hp"]["current"] for p in self.mod._current_stats["players"]}
        self.assertEqual(hp["Mira"], 5)                         # the live value won
        # now a Save wrote the sheet after the last display change
        self.client.post("/clear")
        sheet.write_text(SHEET.replace("20/30", "26/30"), encoding="utf-8")
        os.utime(sheet, (4102444800, 4102444800))               # far future: newer than the copy
        self.push_party(players=[{"name": "Mira", "hp": {"current": 26, "max": 30, "temp": 0}}])
        hp = {p["name"]: p["hp"]["current"] for p in self.mod._current_stats["players"]}
        self.assertEqual(hp["Mira"], 26)

    def test_restart_then_load_restores_hp_and_effects(self):
        self.push_party()
        self.client.post("/stats", json={"players": [{"name": "Mira", "hp": {"current": 7, "max": 30, "temp": 0},
                                                       "conditions": ["Poisoned"]}]})
        os.utime(self.root / "campaigns" / CAMP / "characters" / "Mira.md", (1, 1))
        self.restart()
        mira = next(p for p in self.mod._current_stats["players"] if p["name"] == "Mira")
        self.assertEqual(mira["hp"]["current"], 7)
        self.assertEqual(mira["conditions"], ["Poisoned"])

    def test_another_campaign_does_not_inherit_the_board(self):
        self.push_party(**self.board())
        self.client.post("/clear")
        self.push_party(camp=OTHER)
        st = self.mod._current_stats
        for key in ("npcs", "combat", "quests", "factions", "world_time", "turn_order"):
            self.assertNotIn(key, st)
        # and going back brings the first campaign's board back
        self.client.post("/clear")
        self.push_party(camp=CAMP)
        self.assertEqual(self.mod._current_stats["npcs"][0]["name"], "Hobb")

    def test_a_clear_does_not_wipe_the_saved_board(self):
        self.push_party(**self.board())
        self.client.post("/clear")
        self.client.post("/stats", json={"players": [{"name": "Mira"}]})   # a stray push before the load
        saved = json.loads((self.root / "campaigns" / CAMP / "display_state.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["stats"]["npcs"][0]["name"], "Hobb")

    def test_push_with_autorun_signal_is_still_saved(self):
        self.push_party()
        self.client.post("/stats", json={"npcs": [{"name": "Zed", "role": "guard", "disposition": "wary"}],
                                         "autorun_waiting": True})
        self.restart()
        self.assertEqual(self.mod._current_stats["npcs"][0]["name"], "Zed")


if __name__ == "__main__":
    unittest.main()
