"""The "Waiting on…" badge cleans up: a newer request for the same people replaces the old one, a new turn ends
last turn's requests, and old requests expire."""
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
SCRIPTS = REPO / "skills" / "dnd" / "scripts"


class DicePendingCleanupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / "campaigns").mkdir()
        cls._env = {k: os.environ.get(k) for k in ("DND_CAMPAIGN_ROOT", "DND_RUNTIME_DIR")}
        os.environ["DND_CAMPAIGN_ROOT"] = str(root)
        os.environ["DND_RUNTIME_DIR"] = str(root / ".runtime")
        sys.path.insert(0, str(SCRIPTS))
        sys.path.insert(0, str(DISPLAY))
        import paths
        import runtime_paths
        (root / ".runtime").mkdir(exist_ok=True)
        cls._rt_patch = mock.patch.object(runtime_paths, "RT", str(root / ".runtime"))
        cls._rt_patch.start()
        cls._root_patch = mock.patch.object(paths, "_root", lambda: root)
        cls._root_patch.start()
        spec = importlib.util.spec_from_file_location("display_app_dice_cleanup", DISPLAY / "dnd-display-app.py")
        cls.mod = importlib.util.module_from_spec(spec)
        sys.modules["display_app_dice_cleanup"] = cls.mod
        spec.loader.exec_module(cls.mod)
        cls.client = cls.mod.app.test_client()
        assert str(root) in cls.mod.QUEUE_FILE, "test would touch the real runtime folder"

    @classmethod
    def tearDownClass(cls):
        cls._root_patch.stop()
        cls._rt_patch.stop()
        for k, v in cls._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        cls.tmp.cleanup()

    def setUp(self):
        self.mod._clear_dice_pending()

    def ask(self, who, label):
        r = self.client.post("/dice-request", json={"characters": [who], "spec": "1d20", "label": label})
        self.assertLess(r.status_code, 300, r.data)

    def pending(self):
        return [(tuple(e["pending"]), e["label"]) for e in self.mod._dice_pending_snapshot()]

    def test_a_new_request_for_the_same_person_replaces_the_old_one(self):
        self.ask("Test Hero", "Longsword attack vs CA1")
        self.ask("Test Hero", "Longsword attack vs CA1")
        self.ask("Test Hero", "Death saving throw")
        self.assertEqual(self.pending(), [(("Test Hero",), "Death saving throw")])

    def test_different_people_can_both_be_waited_on(self):
        self.ask("Dagna", "Dex save")
        self.ask("Joris", "Dex save")
        self.assertEqual(len(self.pending()), 2)

    def test_the_next_turn_ends_last_turns_requests(self):
        self.client.post("/stats", json={"turn_order": {"order": ["Dagna", "Goblin"], "current": "Dagna", "round": 1}})
        self.ask("Dagna", "Attack")
        self.client.post("/stats", json={"turn_order": {"order": ["Dagna", "Goblin"], "current": "Dagna", "round": 1}})
        self.assertEqual(len(self.pending()), 1)                          # same turn pushed again: still waiting
        self.client.post("/stats", json={"turn_order": {"order": ["Dagna", "Goblin"], "current": "Goblin", "round": 1}})
        self.assertEqual(self.pending(), [])                              # next turn: gone
        self.ask("Dagna", "Death saving throw")
        self.client.post("/stats", json={"turn_order": None})
        self.assertEqual(self.pending(), [])                              # combat over: gone

    def test_old_requests_expire(self):
        self.ask("Dagna", "Perception")
        with self.mod._dice_pending_lock:
            for e in self.mod._dice_pending.values():
                e["started_at"] -= self.mod._DICE_PENDING_MAX_AGE + 5
        self.assertEqual(self.pending(), [])

    def test_narration_after_the_roll_closes_it_but_not_a_request_sent_with_it(self):
        self.ask("Milo", "Persuasion")
        self.client.post("/chunk", json={"text": "Make a Persuasion check, Milo."})   # sent with the request
        self.assertEqual(len(self.pending()), 1)
        with self.mod._dice_pending_lock:
            for e in self.mod._dice_pending.values():
                e["started_at"] -= 30                                                   # the DM went on later
        self.client.post("/chunk", json={"text": "The kobold lowers its spear, unsure.", "player": None})
        self.assertEqual(self.pending(), [])
        self.ask("Milo", "Stealth")
        with self.mod._dice_pending_lock:
            for e in self.mod._dice_pending.values():
                e["started_at"] -= 30
        self.client.post("/chunk", json={"text": "d20+5 = 17", "dice": True})          # a dice line is not the DM moving on
        self.assertEqual(len(self.pending()), 1)

    def test_moving_the_pointer_keeps_the_turn_order(self):
        self.client.post("/stats", json={"turn_order": {"order": ["Dagna", "Goblin"], "current": "Dagna", "round": 2}})
        self.client.post("/stats", json={"turn_order": {"current": "Goblin"}})          # what --turn-current sends
        to = self.mod._current_stats["turn_order"]
        self.assertEqual((to["order"], to["current"], to["round"]), (["Dagna", "Goblin"], "Goblin", 2))
        self.client.post("/stats", json={"turn_order": None})
        self.assertFalse(self.mod._current_stats.get("turn_order"))

    def test_board_patch_changes_hp_and_squares_without_a_resend(self):
        board = {"enemies": [{"name": "Goblin 1", "hp": 7, "max": 7}, {"name": "Goblin 2", "hp": 7, "max": 7}],
                 "map": {"cols": 12, "rows": 8, "biome": "dungeon", "title": "Hall",
                         "tokens": [{"name": "Dagna", "x": 1, "y": 3}, {"name": "Goblin 1", "x": 8, "y": 3, "side": "enemy"},
                                    {"name": "Goblin 2", "x": 8, "y": 5, "side": "enemy"}]}}
        self.client.post("/stats", json={"combat": board})
        self.client.post("/stats", json={"combat_patch": {"enemies": [{"name": "goblin 1", "hp": 3}],
                                                          "tokens": [{"name": "Dagna", "x": 3, "y": 3}], "remove": ["Goblin 2"]}})
        c = self.mod._current_stats["combat"]
        self.assertEqual([(e["name"], e["hp"], e["max"]) for e in c["enemies"]], [("Goblin 1", 3, 7), ("Goblin 2", 7, 7)])
        toks = {t["name"]: (t["x"], t["y"]) for t in c["map"]["tokens"]}
        self.assertEqual(toks["Dagna"], (3, 3))
        self.assertNotIn("Goblin 2", toks)
        self.assertEqual(c["map"]["title"], "Hall")                       # the rest of the map is untouched
        self.client.post("/stats", json={"combat": {}})

    def test_badge_moves_out_of_the_map_in_combat(self):
        html = (DISPLAY / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn("body.combat #dice-pending-badge", html)


if __name__ == "__main__":
    unittest.main()
