"""Picking a campaign in the Main Menu shows its real party at once.

Regression for: loading the test adventure showed only a placeholder
"Aldric". The display's saved party had been overwritten by test data, and
nothing put the campaign's own characters back until Claude (who may not be
running) loaded the campaign.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd"
DISPLAY = SKILL / "display"
SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(DISPLAY))

import paths  # noqa: E402
import party  # noqa: E402

DAGNA = """# Dagna Gloedbaard
**Player:** Joyce  **Campaign:** demo  **Last Updated:** 2026-11-14

## Identity
- **Race:** Hill Dwarf | **Class:** Sorcerer (Draconic Bloodline) | **Level:** 1 | **Background:** Acolyte
- **Alignment:** Chaotic Good | **XP:** 137 / 300

## Combat Stats
- **HP:** 11 / 11 | **Temp HP:** 0
- **AC:** 14 (Draconic Resilience: 13 + DEX 1) | **Initiative:** +1 | **Speed:** 7,5 m
- **Hit Dice:** 1d6 (remaining: 1)
"""

BALASAR = """# Balasar Vuurschaal
## Identity
- **Race:** Dragonborn (Red) | **Class:** Paladin | **Level:** 3 | **Background:** Soldier
- **XP:** 1,200 / 2,700
## Combat Stats
- **HP:** 12 / 30 | **Temp HP:** 4
- **AC:** 18 | **Speed:** 9 m
- **Hit Dice:** 3d10 (remaining: 2)
"""


class ParseTest(unittest.TestCase):
    def test_a_character_file_becomes_a_sidebar_player(self):
        p = party.parse_character(DAGNA)
        self.assertEqual(p["name"], "Dagna Gloedbaard")
        self.assertEqual(p["race"], "Hill Dwarf")
        self.assertEqual(p["class"], "Sorcerer (Acolyte)")        # base class + background, as the sidebar shows it
        self.assertEqual(p["level"], 1)
        self.assertEqual(p["hp"], {"current": 11, "max": 11, "temp": 0})
        self.assertEqual(p["ac"], 14)
        self.assertEqual(p["speed"], "7,5 m")
        self.assertEqual(p["hit_dice"], {"max": 1, "die": "d6", "remaining": 1})
        self.assertEqual(p["xp"], {"current": 137, "next": 300})

    def test_numbers_with_thousands_separators_and_temp_hp(self):
        p = party.parse_character(BALASAR)
        self.assertEqual(p["xp"], {"current": 1200, "next": 2700})
        self.assertEqual(p["hp"], {"current": 12, "max": 30, "temp": 4})
        self.assertEqual(p["hit_dice"], {"max": 3, "die": "d10", "remaining": 2})
        self.assertEqual(p["class"], "Paladin (Soldier)")

    def test_a_sparse_file_gives_what_it_has_and_never_crashes(self):
        p = party.parse_character("# Pip\n\nJust a name.\n")
        self.assertEqual(p["name"], "Pip")
        self.assertNotIn("hp", p)
        self.assertEqual(party.parse_character("no heading here"), None)


class PartyOpenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        for camp, files in {"demo": {"Dagna Gloedbaard": DAGNA, "Balasar Vuurschaal": BALASAR},
                            "solo": {"Dagna Gloedbaard": DAGNA}, "empty": {}}.items():
            d = cls.root / "campaigns" / camp
            (d / "characters").mkdir(parents=True)
            (d / "state.md").write_text("**Ruleset:** 2014\n", encoding="utf-8")
            for name, text in files.items():
                (d / "characters" / f"{name}.md").write_text(text, encoding="utf-8")
        (cls.root / ".runtime").mkdir()
        cls._env = {k: os.environ.get(k) for k in ("DND_CAMPAIGN_ROOT", "DND_RUNTIME_DIR")}
        os.environ["DND_CAMPAIGN_ROOT"] = str(cls.root)
        os.environ["DND_RUNTIME_DIR"] = str(cls.root / ".runtime")
        import runtime_paths  # remembers its folder from first import: pin both
        cls._patches = [mock.patch.object(paths, "_root", lambda: cls.root),
                        mock.patch.object(runtime_paths, "RT", str(cls.root / ".runtime"))]
        for p in cls._patches:
            p.start()
        spec = importlib.util.spec_from_file_location("display_app_party", DISPLAY / "dnd-display-app.py")
        cls.mod = importlib.util.module_from_spec(spec)
        sys.modules["display_app_party"] = cls.mod
        spec.loader.exec_module(cls.mod)
        cls.client = cls.mod.app.test_client()
        assert str(cls.root) in cls.mod.QUEUE_FILE, "test would touch the real runtime folder"

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
        for f in (self.mod.QUEUE_FILE, self.mod.CAMP_FILE):
            if os.path.exists(f):
                os.remove(f)
        self.client.post("/stats", json={"players": [{"name": "Aldric", "milestones": {"Hero Coin": 3}}],
                                         "replace_players": True})        # the stray placeholder

    def _names(self):
        with self.mod._stats_lock:
            return [p["name"] for p in self.mod._current_stats["players"]]

    def test_loading_a_campaign_replaces_the_stale_party_with_its_own_characters(self):
        self.assertEqual(self._names(), ["Aldric"])
        r = self.client.post("/menu/load", json={"campaign": "demo"})
        self.assertEqual(r.status_code, 204)
        self.assertEqual(self._names(), ["Balasar Vuurschaal", "Dagna Gloedbaard"])
        self.assertEqual(self.mod._active_campaign_name(), "demo")

    def test_the_menu_sees_the_campaign_as_active_right_away(self):
        self.client.post("/menu/load", json={"campaign": "demo"})
        self.assertEqual(self.client.get("/menu/campaigns").get_json()["active"], "demo")

    def test_claude_is_still_asked_to_load_it(self):
        self.client.post("/menu/load", json={"campaign": "demo"})
        with open(self.mod.QUEUE_FILE, encoding="utf-8") as f:
            self.assertIn("[Menu]: load demo", f.read())

    def test_continuing_does_not_reset_hit_points_that_changed_in_play(self):
        self.client.post("/menu/load", json={"campaign": "demo"})
        self.client.post("/stats", json={"players": [{"name": "Dagna Gloedbaard", "hp": {"current": 3, "max": 11, "temp": 0}}]})
        self.client.post("/menu/load", json={"campaign": "demo"})            # Continue
        with self.mod._stats_lock:
            dagna = next(p for p in self.mod._current_stats["players"] if p["name"] == "Dagna Gloedbaard")
        self.assertEqual(dagna["hp"]["current"], 3)

    def test_switching_campaign_swaps_the_whole_party(self):
        self.client.post("/menu/load", json={"campaign": "demo"})
        self.client.post("/menu/load", json={"campaign": "solo"})
        self.assertEqual(self._names(), ["Dagna Gloedbaard"])

    def test_a_campaign_with_no_characters_does_not_keep_the_last_party(self):
        self.client.post("/menu/load", json={"campaign": "demo"})
        self.client.post("/menu/load", json={"campaign": "empty"})
        self.assertEqual(self._names(), [])
        self.assertEqual(self.mod._active_campaign_name(), "empty")


class ListeningTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / "campaigns").mkdir()
        (root / ".runtime").mkdir()
        cls._env = {k: os.environ.get(k) for k in ("DND_CAMPAIGN_ROOT", "DND_RUNTIME_DIR")}
        os.environ["DND_CAMPAIGN_ROOT"] = str(root)
        os.environ["DND_RUNTIME_DIR"] = str(root / ".runtime")
        import runtime_paths
        cls._patches = [mock.patch.object(paths, "_root", lambda: root),
                        mock.patch.object(runtime_paths, "RT", str(root / ".runtime"))]
        for p in cls._patches:
            p.start()
        spec = importlib.util.spec_from_file_location("display_app_listen", DISPLAY / "dnd-display-app.py")
        cls.mod = importlib.util.module_from_spec(spec)
        sys.modules["display_app_listen"] = cls.mod
        spec.loader.exec_module(cls.mod)
        cls.client = cls.mod.app.test_client()
        cls.beat = Path(cls.mod.rt(".dm-heartbeat"))
        assert str(root) in str(cls.beat), "test would touch the real runtime folder"

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
        if self.beat.exists():
            self.beat.unlink()

    def test_nobody_is_listening_without_a_heartbeat(self):
        self.assertFalse(self.client.get("/menu/campaigns").get_json()["dm_listening"])

    def test_a_fresh_heartbeat_means_a_dungeon_master_is_there(self):
        self.beat.write_text(str(time.time()), encoding="utf-8")
        self.assertTrue(self.client.get("/menu/campaigns").get_json()["dm_listening"])

    def test_an_old_heartbeat_means_the_session_is_gone(self):
        self.beat.write_text("x", encoding="utf-8")
        old = time.time() - 600
        os.utime(self.beat, (old, old))
        self.assertFalse(self.client.get("/menu/campaigns").get_json()["dm_listening"])

    def test_the_menu_page_warns_when_nobody_is_connected(self):
        html = self.client.get("/menu").get_data(as_text=True)
        self.assertIn('id="no-dm"', html)
        self.assertIn("No Dungeon Master is connected", html)
        self.assertIn("data.dm_listening", html)

    def test_the_wait_loop_keeps_beating(self):
        src = (DISPLAY / "autorun_wait.py").read_text(encoding="utf-8")
        self.assertIn(".dm-heartbeat", src)
        self.assertIn("if _i % 30 == 0:", src)       # about every three seconds while it waits (30 polls of 0.1 s)


if __name__ == "__main__":
    unittest.main()
