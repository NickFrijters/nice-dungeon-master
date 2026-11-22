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
    a = APP.index("def _clean_map_image(")
    b = APP.index("def _cell_label")
    ns = {"os": os, "re": __import__("re")}
    exec(APP[a:b], ns)
    return ns["_clean_combat"]


class CombatScreenTest(unittest.TestCase):
    def test_page_has_the_combat_regions_and_drag(self):
        for needle in ('id="combat-view"', 'id="cb-order"', 'id="cb-map"', "function _tokenDrag(", "body.combat #text-scroll"):
            self.assertIn(needle, HTML)
        self.assertIn("document.body.classList.toggle('combat', on)", HTML)

    def test_mat_refits_and_big_tokens_check_their_whole_footprint(self):
        # playtest fixes: the mat refit on window resize, crowded side lists tighten, big tokens need all squares free
        for needle in ("new ResizeObserver(() => {", "_cbDragging", "side.classList.add('tight')", "const cover = (x, y, f)", "const walled = (x, y)"):
            self.assertIn(needle, HTML)

    def test_map_picture_is_cleaned_placed_and_kept_when_repushed(self):
        clean = _clean_combat()
        base = {"cols": 14, "rows": 10, "tokens": []}
        out = clean({"map": {**base, "image": "../../secret/cellar.PNG"}})
        self.assertEqual(out["map"]["image"], {"src": "cellar.PNG", "x": 0, "y": 0, "w": 14, "h": 10, "placed": False, "for": [14, 10]})   # path stripped, fits the grid until measured
        self.assertNotIn("image", clean({"map": {**base, "image": "notes.txt"}})["map"])               # only picture files
        self.assertNotIn("image", clean({"map": base})["map"])
        placed = clean({"map": {**base, "image": {"src": "cellar.png", "x": -0.4, "y": "-0.4", "w": 14.8, "h": 10.8}}})
        self.assertEqual(placed["map"]["image"], {"src": "cellar.png", "x": -0.4, "y": -0.4, "w": 14.8, "h": 10.8, "placed": True, "for": [14, 10]})
        # the DM pushes the board again without a placement: the placement already measured stays
        again = clean({"map": {**base, "image": "cellar.png"}}, placed)
        self.assertEqual(again["map"]["image"], placed["map"]["image"])
        # a different picture starts from the default fit
        other = clean({"map": {**base, "image": "other.png"}}, placed)
        self.assertEqual(other["map"]["image"], {"src": "other.png", "x": 0, "y": 0, "w": 14, "h": 10, "placed": False, "for": [14, 10]})

    def test_area_of_a_big_map_and_stale_fits(self):
        clean = _clean_combat()
        base = {"cols": 10, "rows": 6, "tokens": []}
        out = clean({"map": {**base, "image": {"src": "dungeon.webp", "area": {"x": 0.1, "y": 0.5, "w": 0.4, "h": 0.2}}}})["map"]["image"]
        # until the page measures the printed grid, the area just covers the grid: 10 squares per 40% of the picture
        self.assertEqual((out["x"], out["y"], out["w"], out["h"]), (-2.5, -15.0, 25.0, 30.0))
        self.assertFalse(out["placed"])
        self.assertEqual(out["area"], {"x": 0.1, "y": 0.5, "w": 0.4, "h": 0.2})
        bad = clean({"map": {**base, "image": {"src": "d.png", "area": {"x": 5, "y": -1, "w": 0, "h": "x"}}}})["map"]["image"]
        self.assertNotIn("area", bad)                                                 # nonsense area: ignored, picture covers the grid
        measured = {**out, "x": -3.2, "placed": True}
        same = clean({"map": {**base, "image": {"src": "dungeon.webp", "area": out["area"]}}}, {"map": {"image": measured}})["map"]["image"]
        self.assertEqual(same["x"], -3.2)                                               # same picture, area and grid size: the measured fit stays
        other = clean({"map": {**base, "cols": 12, "image": {"src": "dungeon.webp", "area": out["area"]}}}, {"map": {"image": measured}})["map"]["image"]
        self.assertFalse(other["placed"])                                               # a different grid size needs a new fit

    def test_align_route_page_and_pushers(self):
        self.assertIn('@app.route("/combat/image", methods=["POST"])', APP)
        for needle in ("function _cbAutoFit(", "function _cbDetectGrid(", "cb-bgimg", "'/combat/image'"):
            self.assertIn(needle, HTML)
        self.assertIn("_resolve_combat_image(payload[\"combat\"])", (D / "push_stats.py").read_text(encoding="utf-8"))
        self.assertIn("measures the picture", (ROOT / "skills" / "dnd" / "SKILL-commands.md").read_text(encoding="utf-8"))

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
        self.assertEqual(out["map"]["terrain"][0], {"kind": "wall", "x": 0, "y": 0, "w": 10, "h": 1})   # the DM's piece first
        self.assertTrue(out["map"].get("furnished"))                                                       # a nearly bare map gets furnished
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
