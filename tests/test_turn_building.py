"""Building a combat turn on the table: a dragged token is a PLANNED move (arrow) until GO; GO moves it. Sheet clicks
and planned moves become fixed text in the Party Input row."""
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
HTML = (DISPLAY / "templates" / "index.html").read_text(encoding="utf-8")

BOARD = {"enemies": [{"name": "Goblin 1", "hp": 7, "max": 7}],
         "map": {"cols": 10, "rows": 6, "tokens": [{"name": "Dagna", "x": 1, "y": 1},
                                                    {"name": "Goblin 1", "x": 7, "y": 2, "side": "enemy"}]}}


class TurnBuildingTest(unittest.TestCase):
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
        spec = importlib.util.spec_from_file_location("display_app_turn_building", DISPLAY / "dnd-display-app.py")
        cls.mod = importlib.util.module_from_spec(spec)
        sys.modules["display_app_turn_building"] = cls.mod
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
        self.client.post("/stats", json={"players": [{"name": "Dagna", "hp": {"current": 10, "max": 10}}],
                                         "turn_order": {"order": ["Dagna", "Goblin 1"], "current": "Dagna", "round": 1},
                                         "combat": BOARD})

    def token(self, name):
        return next(t for t in self.mod._current_stats["combat"]["map"]["tokens"] if t["name"] == name)

    def plan(self, **body):
        return self.client.post("/combat/plan", json=body)

    def test_a_drag_is_only_a_plan_until_go(self):
        r = self.plan(name="Dagna", x=4, y=1, ft=15)
        self.assertEqual(r.status_code, 204)
        t = self.token("Dagna")
        self.assertEqual(((t["x"], t["y"]), t["plan"]), ((1, 1), {"x": 4, "y": 1, "ft": 15}))   # still standing, arrow planned
        queue = Path(self.mod.QUEUE_FILE)
        self.assertNotIn("[Map]", queue.read_text(encoding="utf-8") if queue.exists() else "")  # nothing sent to the DM yet
        r = self.client.post("/party-input", json={"entries": [{"character": "Dagna", "text": "Moves from B2 to E2 (15 ft)."}]},
                             headers={"X-DND-Device": "test-device"})
        self.assertLess(r.status_code, 300, r.data)
        t = self.token("Dagna")
        self.assertEqual(((t["x"], t["y"]), t.get("plan")), ((4, 1), None))                     # GO: moved, arrow gone

    def test_cancel_and_drop_back_home(self):
        self.plan(name="Dagna", x=4, y=1, ft=15)
        self.plan(name="Dagna", cancel=True)
        self.assertNotIn("plan", self.token("Dagna"))
        self.plan(name="Dagna", x=4, y=1, ft=15)
        self.plan(name="Dagna", x=1, y=1)                                                        # dropped on its own square
        self.assertNotIn("plan", self.token("Dagna"))
        self.assertEqual(self.plan(name="Dagna", x=40, y=1).status_code, 400)
        self.assertEqual(self.plan(name="Nobody", x=2, y=1).status_code, 404)

    def test_a_plan_onto_an_obstacle_or_a_creature_is_refused(self):
        walled = {**BOARD, "map": {**BOARD["map"], "terrain": [{"kind": "tree", "x": 3, "y": 1}]}}
        self.client.post("/stats", json={"combat": walled})
        self.assertEqual(self.plan(name="Dagna", x=3, y=1, ft=10).status_code, 409)     # a tree
        self.assertEqual(self.plan(name="Dagna", x=7, y=2, ft=30).status_code, 409)     # the goblin's square
        self.assertNotIn("plan", self.token("Dagna"))

    def test_a_dm_push_keeps_the_plan_unless_the_token_moved(self):
        self.plan(name="Dagna", x=4, y=1, ft=15)
        self.client.post("/stats", json={"combat": BOARD})                                       # e.g. an HP update
        self.assertEqual(self.token("Dagna").get("plan"), {"x": 4, "y": 1, "ft": 15})
        moved = {**BOARD, "map": {**BOARD["map"], "tokens": [{"name": "Dagna", "x": 2, "y": 2}, BOARD["map"]["tokens"][1]]}}
        self.client.post("/stats", json={"combat": moved})                                       # the DM put her elsewhere
        self.assertNotIn("plan", self.token("Dagna"))

    def test_page_builds_turn_text(self):
        for needle in ("'/combat/plan'", "function _cbAutoTexts(", "'Moves from '", "'Attacks ' + target + ' with '",
                       "function _cbArm(", "function _partyTexts(", "Object.entries(_partyTexts())", "plan-ghost",
                       "svg.cb-arrows", ".pr-chip", "window._smPlayer = name"):
            self.assertIn(needle, HTML)
        self.assertIn("Moves from D5 to G5, 15 ft.", (REPO / "skills" / "dnd" / "SKILL-commands.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
