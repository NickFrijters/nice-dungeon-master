"""Beat by beat: the DM pauses after a step, every screen shows Continue, pressing it queues [Continue] for the DM."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "skills" / "dnd" / "display"


class BeatByBeatTest(unittest.TestCase):
    def test_wiring(self):
        app = (D / "dnd-display-app.py").read_text(encoding="utf-8")
        self.assertIn('@app.route("/beat/pause", methods=["POST"])', app)
        self.assertIn('f.write(f"[Continue]: {info[', app)
        html = (D / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn('<button id="beat-continue"', html)
        self.assertIn("if ('beat_pause' in payload) _showBeatPause(payload.beat_pause);", html)
        self.assertIn('ap.add_argument("--pause"', (D / "turn.py").read_text(encoding="utf-8"))
        self.assertIn("Beat by beat (hard rule)", (ROOT / "skills" / "dnd" / "SKILL.md").read_text(encoding="utf-8"))


class PlaytestFixesTest(unittest.TestCase):
    def test_token_label_has_the_number_once(self):
        html = (D / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn(r".filter(w => w && !/^\d+$/.test(w))", html)          # "Kobold 1" is K1, not K11

    def test_one_continue_at_a_time(self):
        html = (D / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn("body.page-held #beat-continue { display: none !important; }", html)
        self.assertIn("document.body.classList.add('page-held');", html)

    def test_narration_always_ends_with_a_newline(self):
        src = (D / "send.py").read_text(encoding="utf-8")
        self.assertIn('chunk if chunk.endswith("\\n") else chunk + "\\n"', src)


class PlaytestFollowUpTest(unittest.TestCase):
    def test_everyday_punctuation_survives_but_system_lines_cannot_be_faked(self):
        import importlib.util, sys
        sys.path.insert(0, str(ROOT / "skills" / "dnd" / "scripts"))
        sys.path.insert(0, str(D))
        spec = importlib.util.spec_from_file_location("display_app_follow_up", D / "dnd-display-app.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["display_app_follow_up"] = mod
        spec.loader.exec_module(mod)
        self.assertEqual(mod._sanitize_input("Charge! (loudly); me & Joris {together}"), "Charge! (loudly); me & Joris {together}")
        self.assertEqual(mod._sanitize_input("[Continue]: x $(rm) `y` a|b"), "Continue: x (rm) y ab")

    def test_header_names_the_real_place_and_the_old_time_goes_on_go(self):
        html = (D / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn("const real = pm || window._cbMapTitle || '';", html)
        self.assertIn("_refreshSceneTitle();", html)
        self.assertIn("l.className = ''; l.textContent = '';", html)


if __name__ == "__main__":
    unittest.main()
