"""Map picture under the combat grid: the page measures the printed grid itself (node harness on synthetic maps)."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "skills" / "dnd" / "display" / "templates" / "index.html").read_text(encoding="utf-8")
DETECT = HTML[HTML.index("// <cb-detect>"):HTML.index("// </cb-detect>")]

HARNESS = r"""
%s
function rng(seed) { let s = seed; return () => (s = (s * 1664525 + 1013904223) %% 4294967296) / 4294967296; }
// A fake printed map: floor with a few wall blocks, grid lines every `cell` px starting `ox`,`oy` from the corner.
function makeMap(W, H, cell, ox, oy, lines, seed) {
  const r = rng(seed), g = new Float32Array(W * H).fill(200);
  for (let i = 0; i < 40; i++) {                       // random blobs: walls, water, furniture
    const x0 = Math.floor(r() * W), y0 = Math.floor(r() * H), w = 20 + r() * 90, h = 20 + r() * 60, v = 60 + r() * 140;
    for (let y = y0; y < Math.min(H, y0 + h); y++) for (let x = x0; x < Math.min(W, x0 + w); x++) g[y * W + x] = v;
  }
  if (lines) {
    for (let x = ox; x < W; x += cell) for (let y = 0; y < H; y++) g[y * W + Math.round(x)] = 40;
    for (let y = oy; y < H; y += cell) for (let x = 0; x < W; x++) g[Math.round(y) * W + x] = 40;
  }
  return g;
}
const out = {};
// 14 x 10 squares of 50px with a 20px margin: the grid sits at -0.4 squares and the picture spans 14.8 x 10.8
out.margin = _cbDetectGrid(makeMap(740, 540, 50, 20, 20, true, 1), 740, 540, 14, 10);
// bigger squares, different margins per side
out.big = _cbDetectGrid(makeMap(1010, 760, 70, 35, 30, true, 2), 1010, 760, 14, 10);
// no margin at all
out.flush = _cbDetectGrid(makeMap(700, 500, 50, 0, 0, true, 3), 700, 500, 14, 10);
// no printed grid
out.gridless = _cbDetectGrid(makeMap(740, 540, 50, 20, 20, false, 4), 740, 540, 14, 10);
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "node not installed")
class MapAutofitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "h.js"
            f.write_text(HARNESS % DETECT, encoding="utf-8")
            cls.out = json.loads(subprocess.run(["node", str(f)], capture_output=True, text=True, check=True).stdout)

    def near(self, got, want, tol=0.12):
        self.assertIsNotNone(got)
        for k, v in want.items():
            self.assertAlmostEqual(got[k], v, delta=tol, msg=f"{k}: {got}")

    def test_finds_the_printed_grid_with_margins(self):
        self.near(self.out["margin"], {"x": -0.4, "y": -0.4, "w": 14.8, "h": 10.8})
        self.near(self.out["big"], {"x": -0.5, "y": -0.43, "w": 14.43, "h": 10.86}, 0.15)

    def test_flush_grid_and_gridless_pictures(self):
        self.near(self.out["flush"], {"x": 0, "y": 0, "w": 14, "h": 10})
        self.assertIsNone(self.out["gridless"])            # no printed grid: the picture just covers ours


class WiringTest(unittest.TestCase):
    def test_no_align_button_and_page_fits_on_its_own(self):
        for gone in ("Align map", "cb-align-bar", "_cbAlignUi"):
            self.assertNotIn(gone, HTML)
        for needle in ("function _cbAutoFit(", "_cbAutoFit(m)", "'/combat/image'"):
            self.assertIn(needle, HTML)


if __name__ == "__main__":
    unittest.main()
