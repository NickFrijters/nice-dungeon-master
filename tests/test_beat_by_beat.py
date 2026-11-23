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


class PlaytestRound2Test(unittest.TestCase):
    def test_wiring(self):
        app = (D / "dnd-display-app.py").read_text(encoding="utf-8")
        html = (D / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn("the players' roll is in: the DM's next answer is timed from here", app)      # latency excludes the player's roll
        self.assertIn('new_to["order"] = [str(e.get("name")', app)                                   # {name, init} objects accepted
        self.assertIn("function _orderNames(o)", html)
        self.assertIn("_inCombat = ", html.replace("const inCombat", "_inCombat"))                    # combat reserves only what the panel covers
        self.assertIn("const pb = parseFloat(getComputedStyle(textScroll).paddingBottom) || 0;", html)
        self.assertIn("_inputPanel.classList.add('collapsed'); _inputArrow.textContent", html)       # panel folds after GO in combat
        cmds = (ROOT / "skills" / "dnd" / "SKILL-commands.md").read_text(encoding="utf-8")
        self.assertIn("Do not run `push_stats.py --help`", cmds)


class TurnPyBoardAndResistTest(unittest.TestCase):
    def test_resist_halves_and_board_options_exist(self):
        import sys, types
        sys.path.insert(0, str(D)); sys.path.insert(0, str(ROOT / "skills" / "dnd" / "scripts"))
        import turn
        src = (D / "turn.py").read_text(encoding="utf-8")
        for flag in ('"--combat"', '"--turn-order"', '"--resist"'):
            self.assertIn("ap.add_argument(" + flag, src)
        a = types.SimpleNamespace(damage="1d1+9", roll="d20+30", ask=None, label="x", vs=1, hit="{dmg}", miss="", hp_on_hit=None, resist="half")
        seen = set()
        for _ in range(60):
            seen.add(turn._resolve(a)[3].split("damage=")[1])
        self.assertTrue(seen <= {"0", "5", "10"} and "5" in seen, seen)       # 1d1+9 = 10 normally, 5 halved, 10 on a crit (2d1+9=11 -> 5)


if __name__ == "__main__":
    unittest.main()
