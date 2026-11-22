"""The battle map looks like the place, and the rules follow the picture: setting (biome), terrain kinds with a look
AND a movement rule that match on server and page, tokens never inside obstacles, and the grid always on top."""
import os
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "skills" / "dnd" / "display"
HTML = (D / "templates" / "index.html").read_text(encoding="utf-8")
APP = (D / "dnd-display-app.py").read_text(encoding="utf-8")
COMMANDS = (ROOT / "skills" / "dnd" / "SKILL-commands.md").read_text(encoding="utf-8")


def _ns():
    a = APP.index("def _clean_map_image(")
    b = APP.index("def _cell_label")
    ns = {"os": os, "re": re}
    exec(APP[a:b], ns)
    return ns


NS = _ns()
clean = NS["_clean_combat"]


def T(kind, x, y, w=1, h=1):
    return {"kind": kind, "x": x, "y": y, "w": w, "h": h}


def board(title="", terrain=(), tokens=(), biome=None):
    m = {"cols": 12, "rows": 8, "title": title, "terrain": list(terrain), "tokens": list(tokens)}
    if biome:
        m["biome"] = biome
    return clean({"map": m})["map"]


def js_set(name):
    m = re.search(r"const " + name + r" = new Set\(\[(.*?)\]\);", HTML, re.S)
    return set(re.findall(r"'([a-z]+)'", m.group(1)))


class BattleMapRealismTest(unittest.TestCase):
    def test_the_setting_comes_from_the_dm_the_title_or_the_terrain(self):
        self.assertEqual(board("Whatever", biome="snow")["biome"], "snow")
        self.assertEqual(board("Ambush on the Forest Road")["biome"], "forest")
        self.assertEqual(board("Brawl in the Rusty Tankard")["biome"], "tavern")
        self.assertEqual(board("Lizardfolk Marsh")["biome"], "swamp")
        self.assertEqual(board("The Drowned Cellar")["biome"], "dungeon")
        self.assertEqual(board("Somewhere", [T("tree", 1, 1), T("bush", 3, 3), T("tree", 5, 1)])["biome"], "forest")
        self.assertEqual(board("Somewhere", [T("table", 1, 1), T("barrel", 3, 3)])["biome"], "tavern")
        self.assertEqual(board("Somewhere")["biome"], "dungeon")
        self.assertEqual(board("x", biome="moon")["biome"], "dungeon")                 # unknown biome: not passed through

    def test_terrain_names_are_understood_and_unknown_ones_dropped(self):
        kinds = [t["kind"] for t in board("", [T("Trees", 1, 1), T("rock", 2, 2), T("undergrowth", 3, 3),
                                               T("river", 4, 4), T("spaceship", 5, 5)])["terrain"]]
        self.assertEqual(kinds, ["tree", "boulder", "brush", "water"])

    def test_a_token_never_stands_inside_an_obstacle_or_on_another_token(self):
        m = board("Forest", [T("tree", 4, 1), T("wall", 0, 0, 12, 1)],
                  [{"name": "Goblin", "x": 4, "y": 1, "side": "enemy"}, {"name": "Orc", "x": 4, "y": 0, "side": "enemy"},
                   {"name": "Dagna", "x": 6, "y": 4}, {"name": "Joris", "x": 6, "y": 4}])
        cells = {}
        blocked = {(x, 0) for x in range(12)} | {(4, 1)}
        for t in m["tokens"]:
            self.assertNotIn((t["x"], t["y"]), blocked, t)
            self.assertNotIn((t["x"], t["y"]), cells, t)
            cells[(t["x"], t["y"])] = t["name"]
        self.assertEqual(max(abs(t["x"] - 4) + abs(t["y"] - 1) for t in m["tokens"] if t["name"] == "Goblin"), 1)  # nearest free square

    def test_a_token_on_a_bridge_over_deep_water_stays(self):
        m = board("Marsh", [T("deepwater", 4, 0, 2, 8), T("bridge", 4, 3, 2, 1)], [{"name": "Dagna", "x": 4, "y": 3}])
        self.assertEqual((m["tokens"][0]["x"], m["tokens"][0]["y"]), (4, 3))

    def test_server_and_page_agree_on_what_blocks_and_what_slows(self):
        self.assertEqual(js_set("_CB_BLOCK"), NS["_TERRAIN_BLOCK"])
        self.assertEqual(js_set("_CB_SLOW"), NS["_TERRAIN_SLOW"])
        self.assertTrue(js_set("_CB_OVER") <= NS["_TERRAIN_CLEAR"])

    def test_every_terrain_kind_and_every_setting_has_a_look(self):
        for kind in NS["_TERRAIN_KINDS"] - {"difficult"}:
            self.assertRegex(HTML, r"\.cb-ter\." + kind + r"\b", kind)
        for biome in NS["_BIOMES"]:
            if biome != "dungeon":                                                      # dungeon = the default stone floor
                self.assertIn('#cb-map[data-biome="' + biome + '"]', HTML, biome)
        self.assertIn("map.dataset.biome = m.biome || 'dungeon';", HTML)

    def test_the_grid_is_always_drawn_over_the_terrain(self):
        terrain_at = HTML.index("const d = document.createElement('div'); d.className = 'cb-ter ' + t.kind;")
        grid_at = HTML.index("  map.appendChild(grid);")
        self.assertGreater(grid_at, terrain_at)
        self.assertIn("#cb-map .cb-grid { position: absolute; pointer-events: none; z-index: 1;", HTML)

    def test_a_bare_map_is_furnished_to_fit_its_setting_for_every_setting(self):
        toks = [{"name": "Dagna", "x": 1, "y": 3}, {"name": "Joris", "x": 2, "y": 4},
                {"name": "Foe 1", "x": 9, "y": 3, "side": "enemy"}, {"name": "Foe 2", "x": 10, "y": 5, "side": "enemy", "size": 2}]
        for biome in NS["_BIOMES"]:
            m = board("A place", [], toks, biome=biome)
            self.assertTrue(m.get("furnished"), biome)
            self.assertGreaterEqual(len(m["terrain"]), 4, biome)
            blocked = set()
            for t in m["terrain"]:
                if t["kind"] in NS["_TERRAIN_BLOCK"]:
                    blocked |= {(x, y) for x in range(t["x"], t["x"] + t["w"]) for y in range(t["y"], t["y"] + t["h"])}
            for t in m["terrain"]:
                if t["kind"] in ("bridge", "door", "road"):
                    blocked -= {(x, y) for x in range(t["x"], t["x"] + t["w"]) for y in range(t["y"], t["y"] + t["h"])}
            cells = [(t["x"] + i, t["y"] + j) for t in m["tokens"] for i in range(t["size"]) for j in range(t["size"])]
            self.assertFalse(set(cells) & blocked, biome)                                    # nobody inside an obstacle
            start = (m["tokens"][0]["x"], m["tokens"][0]["y"])
            seen, q = {start}, [start]
            while q:
                cx, cy = q.pop()
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    n = (cx + dx, cy + dy)
                    if n not in seen and 0 <= n[0] < 12 and 0 <= n[1] < 8 and n not in blocked:
                        seen.add(n); q.append(n)
            for t in m["tokens"]:
                self.assertIn((t["x"], t["y"]), seen, (biome, t["name"]))                    # everybody can reach everybody
            again = board("A place", [], toks, biome=biome)
            self.assertEqual(again["terrain"], m["terrain"], biome)                           # same layout push after push

    def test_a_map_the_dm_furnished_is_left_alone_and_pictures_are_not_furnished(self):
        m = board("Cellar", [T("wall", 0, 0, 12, 1), T("pillar", 4, 4), T("rubble", 6, 6)])
        self.assertEqual(len(m["terrain"]), 3)
        self.assertNotIn("furnished", m)
        pic = clean({"map": {"cols": 12, "rows": 8, "title": "x", "image": "cellar.png", "tokens": []}})["map"]
        self.assertEqual(pic["terrain"], [])

    def test_where_the_party_is_decides_the_setting_when_the_map_says_nothing(self):
        m = clean({"map": {"cols": 12, "rows": 8, "title": "", "tokens": []}}, None, "Misty Forest - the old road")["map"]
        self.assertEqual(m["biome"], "forest")

    def test_dm_rules(self):
        self.assertIn("Battle map = the place (hard rule)", COMMANDS)
        for word in ("`map.biome`", "**blocking**", "**difficult**", "Never put a token inside blocking terrain"):
            self.assertIn(word, COMMANDS)


if __name__ == "__main__":
    unittest.main()
