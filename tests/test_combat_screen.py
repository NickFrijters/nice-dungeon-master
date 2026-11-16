"""Combat screen: initiative bar, battle map with draggable tokens, HP lists."""
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "skills" / "dnd" / "display"
HTML = (D / "templates" / "index.html").read_text(encoding="utf-8")
APP = (D / "dnd-display-app.py").read_text(encoding="utf-8")


def _clean_combat():
    # Pull the pure helper out of the app source without starting the app.
    a = APP.index("def _clean_combat(raw):")
    b = APP.index("def _cell_label")
    ns = {}
    exec(APP[a:b], ns)
    return ns["_clean_combat"]


class CombatScreenTest(unittest.TestCase):
    def test_page_has_the_combat_regions_and_drag(self):
        for needle in ('id="combat-view"', 'id="cb-order"', 'id="cb-map"', "function _tokenDrag(", "body.combat #text-scroll"):
            self.assertIn(needle, HTML)
        self.assertIn("document.body.classList.toggle('combat', on)", HTML)

    def test_server_cleans_the_board(self):
        clean = _clean_combat()
        self.assertIsNone(clean({}))
        out = clean({"enemies": [{"name": "Goblin 1", "hp": "7", "max": 7}, {"nope": 1}],
                     "map": {"cols": 99, "rows": 2, "tokens": [
                         {"name": "Dagna", "x": 1, "y": 1}, {"name": "Lost", "x": 50, "y": 0},
                         {"name": "Gob", "x": 2, "y": 2, "side": "enemy"}]}})
        self.assertEqual(out["enemies"], [{"name": "Goblin 1", "hp": 7, "max": 7, "note": ""}])
        self.assertEqual((out["map"]["cols"], out["map"]["rows"]), (26, 4))
        self.assertEqual([(t["name"], t["side"]) for t in out["map"]["tokens"]], [("Dagna", "pc"), ("Gob", "enemy")])

    def test_terrain_size_speed_and_conditions_are_cleaned(self):
        clean = _clean_combat()
        out = clean({"map": {"cols": 10, "rows": 8, "terrain": [
            {"kind": "wall", "x": 0, "y": 0, "w": 50, "h": 1}, {"kind": "lava", "x": 1, "y": 1}, {"kind": "water", "x": 20, "y": 1}],
            "tokens": [{"name": "Ogre", "x": 2, "y": 2, "side": "enemy", "size": 9, "speed": 40, "conditions": ["Frightened", "Burning"]}]}})
        self.assertEqual(out["map"]["terrain"], [{"kind": "wall", "x": 0, "y": 0, "w": 10, "h": 1}])
        tk = out["map"]["tokens"][0]
        self.assertEqual((tk["size"], tk["speed"], tk["conditions"]), (3, 40, ["Frightened", "Burning"]))

    def test_the_board_has_range_distance_and_damage_numbers(self):
        for needle in ("function _cbReach(", "cb-range", "cb-float", "id=\"cb-legend\"", ".cb-ter.wall"):
            self.assertIn(needle, HTML)

    def test_dragging_tells_claude_and_push_stats_sends_the_board(self):
        self.assertIn("[Map]: {name} moved from {old} to", APP)
        self.assertIn('"--combat"', (D / "push_stats.py").read_text(encoding="utf-8"))
        self.assertIn("[Map]:", (ROOT / "skills" / "dnd" / "SKILL-commands.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
