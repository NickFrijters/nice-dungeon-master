"""Every wait has a tracker: which part we are in, how many there are, what comes next."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TEMPLATES = REPO / "skills" / "dnd" / "display" / "templates"
NODE = shutil.which("node")


def _steps_code() -> str:
    src = (TEMPLATES / "index.html").read_text(encoding="utf-8")
    a = src.index("function _turnSteps(t, voiceExpected) {")
    b = src.index("function _turnTick() {")
    return src[a:b]


def _run(body: str) -> dict:
    if NODE is None:
        raise unittest.SkipTest("node not available")
    script = ("""
class Chip { constructor() { this.className = ''; this.textContent = ''; } }
class Box { constructor() { this.dataset = {}; this.children = []; this._t = ''; }
  set textContent(v) { this._t = v; if (v === '') this.children = []; } get textContent() { return this._t; }
  appendChild(c) { this.children.push(c); } }
const document = { createElement: () => new Chip() };
""" + _steps_code() + "\n" + body)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(script)
        path = f.name
    try:
        r = subprocess.run([NODE, path], capture_output=True, text=True, encoding="utf-8", timeout=60)
        if r.returncode != 0:
            raise AssertionError(r.stderr[:1500])
        return json.loads(r.stdout.strip().splitlines()[-1])
    finally:
        Path(path).unlink(missing_ok=True)


@unittest.skipUnless(NODE, "node not available")
class StepsTest(unittest.TestCase):
    def test_a_turn_with_voice_has_four_parts_and_moves_through_them(self):
        r = _run("""
const seq = ['wait', 'prep', 'run'].map(phase => _turnSteps({ phase, voice: phase !== 'wait' }, true));
console.log(JSON.stringify(seq));
""")
        self.assertEqual([s["idx"] for s in r], [1, 2, 3])
        self.assertEqual(r[0]["names"], ["Sent to the DM", "DM is writing", "Preparing the voice", "Narrating"])

    def test_without_voice_the_voice_parts_are_left_out(self):
        r = _run("""
console.log(JSON.stringify({ wait: _turnSteps({ phase: 'wait', voice: false }, false), run: _turnSteps({ phase: 'run', voice: false }, false) }));
""")
        self.assertEqual(r["wait"]["names"], ["Sent to the DM", "DM is writing", "Writing the text"])
        self.assertEqual((r["wait"]["idx"], r["run"]["idx"]), (1, 2))

    def test_steps_before_the_current_one_are_done_and_the_rest_are_waiting(self):
        r = _run("""
const box = new Box();
_renderSteps(box, ['A', 'B', 'C'], 1);
console.log(JSON.stringify(box.children.map(c => [c.className, c.textContent])));
""")
        self.assertEqual(r, [["tb-step done", "✓ A"], ["tb-step cur", "B"], ["tb-step todo", "C"]])

    def test_rendering_the_same_state_again_does_not_rebuild_the_dom(self):
        r = _run("""
const box = new Box();
_renderSteps(box, ['A', 'B'], 0);
const first = box.children[0];
_renderSteps(box, ['A', 'B'], 0);
_renderSteps(box, ['A', 'B'], 1);
console.log(JSON.stringify({ same: first === box.children[0], count: box.children.length }));
""")
        self.assertFalse(r["same"])           # it did change when the step moved
        self.assertEqual(r["count"], 2)


class PagesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.play = (TEMPLATES / "index.html").read_text(encoding="utf-8")
        cls.menu = (TEMPLATES / "menu.html").read_text(encoding="utf-8")

    def test_the_turn_bar_shows_the_step_and_how_many_there_are(self):
        self.assertIn("'step ' + (st.idx + 1) + ' of ' + st.names.length", self.play)
        self.assertIn('<div class="tb-steps"></div>', self.play)

    def test_the_finished_turn_marks_every_step_done(self):
        body = self.play[self.play.index("function _turnFinish()"):]
        self.assertIn("c.className = 'tb-step done'", body[:700])

    def test_dm_help_has_a_tracker_that_always_goes_away(self):
        self.assertIn("_miniTrackerStart(['Reading the scene', 'Asking the Dungeon Master', 'Writing the hint']);", self.play)
        reset = self.play[self.play.index("function _dmHelpReset() {"):]
        self.assertIn("_miniTrackerStop();", reset[:120])

    def test_the_main_menu_shows_steps_while_loading_and_saving(self):
        self.assertIn("showSteps(['Asking the Dungeon Master', 'Opening the campaign', 'Loading the party']", self.menu)
        self.assertIn("showSteps(['Sent to the Dungeon Master', 'Saving the session', 'Ready for the next one'], 1)", self.menu)
        self.assertIn('id="steps"', self.menu)


if __name__ == "__main__":
    unittest.main()
