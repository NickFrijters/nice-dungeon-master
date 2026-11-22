"""Hovering a square says what is there and what it does; cover from the map counts in attacks (node harness)."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "skills" / "dnd" / "display" / "templates" / "index.html").read_text(encoding="utf-8")
BLOCK = HTML[HTML.index("const _CB_BLOCK = new Set("):HTML.index("function _cbHover(ev) {")]

HARNESS = r"""
%s
function _cbCell(x, y) { return String.fromCharCode(65 + x) + (y + 1); }
function _cbHp(n) { return { hp: 5, max: 7 }; }
const T = (kind, x, y, w, h) => ({ kind, x, y, w: w || 1, h: h || 1 });
const m = { cols: 12, rows: 8, terrain: [T('tree', 4, 4), T('crate', 4, 2, 1, 2), T('wall', 8, 0, 1, 8), T('mud', 2, 6, 2, 1), T('fire', 6, 6),
             T('deepwater', 10, 0, 2, 8), T('bridge', 10, 3, 2, 1)],
            tokens: [{ name: 'Dagna', x: 1, y: 4 }, { name: 'Gob', x: 6, y: 4, side: 'enemy' }, { name: 'Gob2', x: 6, y: 1, side: 'enemy' },
                     { name: 'Gob3', x: 1, y: 1, side: 'enemy' }, { name: 'Joris', x: 1, y: 2 }, { name: 'Far', x: 9, y: 4, side: 'enemy' }] };
const tk = n => m.tokens.find(t => t.name === n);
console.log(JSON.stringify({
  tree: _cbCover(m, tk('Dagna'), tk('Gob')), crate: _cbCover(m, tk('Dagna'), tk('Gob2')), open: _cbCover(m, tk('Dagna'), { x: 1, y: 6 }),
  creature: _cbCover(m, tk('Dagna'), tk('Gob3')), wall: _cbCover(m, tk('Dagna'), tk('Far')), adjacent: _cbCover(m, tk('Gob3'), tk('Joris')),
  cellTree: _cbCellText(m, 4, 4), cellMud: _cbCellText(m, 2, 6), cellFire: _cbCellText(m, 6, 6), cellOpen: _cbCellText(m, 0, 0),
  cellBridge: _cbCellText(m, 10, 3), cellToken: _cbCellText(m, 1, 4)
}));
"""


@unittest.skipUnless(shutil.which("node"), "node not installed")
class CellInfoAndCoverTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "h.js"
            f.write_text(HARNESS % BLOCK, encoding="utf-8")
            r = subprocess.run(["node", str(f)], capture_output=True, text=True)
            assert r.returncode == 0, r.stderr
            cls.o = json.loads(r.stdout)

    def test_cover_from_the_map(self):
        o = self.o
        self.assertEqual(o["tree"], 2)          # a tree in between: three-quarters
        self.assertEqual(o["crate"], 1)         # crates: half
        self.assertEqual(o["open"], 0)
        self.assertEqual(o["creature"], 1)      # somebody standing in between: half
        self.assertEqual(o["wall"], 3)          # through a wall: total
        self.assertEqual(o["adjacent"], 0)      # side by side: nothing in between

    def test_hover_text_says_what_a_square_does(self):
        o = self.o
        self.assertIn("three-quarters cover", " ".join(o["cellTree"]["lines"]))
        self.assertIn("blocks movement", " ".join(o["cellTree"]["lines"]))
        self.assertIn("difficult terrain", " ".join(o["cellMud"]["lines"]))
        self.assertIn("burns", " ".join(o["cellFire"]["lines"]))
        self.assertEqual(o["cellOpen"], {"title": "A1", "lines": ["Open ground"]})
        self.assertEqual(o["cellBridge"]["lines"], ["Bridge: open ground over what is below"])   # the deep water under it is not in play
        self.assertTrue(o["cellToken"]["lines"][0].startswith("Dagna (5/7 HP)"))

    def test_page_wiring_and_dm_rule(self):
        self.assertIn("function _cbHover(ev)", HTML)
        self.assertIn("#cb-tip {", HTML)
        self.assertIn("const covText = cov ?", HTML)
        commands = (ROOT / "skills" / "dnd" / "SKILL-commands.md").read_text(encoding="utf-8")
        self.assertIn("Terrain works mechanically, for monsters too", commands)


if __name__ == "__main__":
    unittest.main()
