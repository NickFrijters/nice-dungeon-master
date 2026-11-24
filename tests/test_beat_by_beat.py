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


class VisualPlaytestTest(unittest.TestCase):
    def setUp(self):
        self.html = (D / "templates" / "index.html").read_text(encoding="utf-8")

    def test_readability_changes(self):
        h = self.html
        self.assertIn("transparent 0, transparent 3vh, rgba(0,0,0,.08) 4.2vh", h)       # first line not dimmed
        self.assertIn("#sidebar .sb-hp-nums { font-size: 12px;", h)
        self.assertIn("#sidebar .sb-faction-name, #sidebar .sb-quest-name { font-size: 11px;", h)
        self.assertIn(".tts-status.st-idle { display: none; }", h)
        self.assertIn("font: 14px/1.4 'EB Garamond', Georgia, serif; color: rgba(245,235,205,.98); }", h)  # square tooltip
        self.assertIn("body.combat #world-clock #wc-date", h)

    def test_sent_text_stays_visible_and_waiting_shows_after_continue(self):
        h = self.html
        self.assertIn("sent.className = 'tb-sent'", h)
        self.assertIn("window._lastSent = entries.map(", h)
        self.assertIn("if (typeof _turnStart === 'function') _turnStart();", h)

    def test_combat_strip_keeps_three_lines(self):
        self.assertIn("Math.max(96, h - pb - 24)", self.html)

    def test_speed_unit_not_doubled(self):
        self.assertIn("(typeof p.speed === 'string' && /[a-z]/i.test(p.speed)) ? p.speed", self.html)

    def test_board_patch_options_and_rules(self):
        t = (D / "turn.py").read_text(encoding="utf-8")
        for flag in ('"--enemy-hp"', '"--move"', '"--remove-token"'):
            self.assertIn("ap.add_argument(" + flag, t)
        import sys
        sys.path.insert(0, str(D)); sys.path.insert(0, str(ROOT / "skills" / "dnd" / "scripts"))
        import turn
        self.assertEqual((turn._cell("C7"), turn._cell("2,6")), ((2, 6), (2, 6)))
        skill = (ROOT / "skills" / "dnd" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("The board must match the words (hard rule)", skill)
        self.assertIn("When a fight starts, the table gets words within seconds", skill)


class Round3Test(unittest.TestCase):
    def test_wiring(self):
        h = (D / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn("function _speedText(sp)", h)
        self.assertIn("activeScene.name !== 'tavern'", h)                       # no "The Inn" guess as a place
        self.assertIn("animation-name: beatNudgeSide;", h)                       # combat Continue beside the strip
        cmds = (ROOT / "skills" / "dnd" / "SKILL-commands.md").read_text(encoding="utf-8")
        self.assertIn("Fast path for `[Menu]: load` (hard rule)", cmds)
        app = (D / "dnd-display-app.py").read_text(encoding="utf-8")
        self.assertIn('_turn_clock_hit({"dice": True})', app)


class ConditionDuplicatesTest(unittest.TestCase):
    def test_one_chip_per_condition(self):
        h = (D / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn("const _seenCond = new Set();", h)
        app = (D / "dnd-display-app.py").read_text(encoding="utf-8")
        self.assertIn('"unconscious" and "Unconscious" are one condition'.replace('"unconscious" and "Unconscious"', '"Unconscious" and "unconscious"'), app)


class LeaveCombatAndWatchdogTest(unittest.TestCase):
    def test_wiring(self):
        h = (D / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="cb-leave"', h)
        self.assertIn("body.combat #cb-leave { display: block; }", h)
        self.assertIn("if (Date.now() - window._typeTick > 6000)", h)            # typing that stalls finishes instantly
        self.assertIn("window._typeTick = Date.now();\n  scrollToBottom();", h)
        t = (D / "turn.py").read_text(encoding="utf-8")
        self.assertIn('ap.add_argument("--end-combat"', t)
        skill = (ROOT / "skills" / "dnd" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("End combat in the same call as its last beat", skill)


class AlliesAndActionButtonsTest(unittest.TestCase):
    def test_wiring(self):
        h = (D / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn("function _cbAllies()", h)
        self.assertIn("h3.textContent = 'Allies'", h)
        self.assertIn("pick an action", h)
        self.assertIn("b.addEventListener('click', () => _cbArm(actor.name, kind, name));", h)
        app = (D / "dnd-display-app.py").read_text(encoding="utf-8")
        self.assertIn('out = {"enemies": [], "allies": [], "map": None}', app)
        cmds = (ROOT / "skills" / "dnd" / "SKILL-commands.md").read_text(encoding="utf-8")
        self.assertIn("**Allies:** friendly NPCs fighting with the party", cmds)


class FloorTextureTest(unittest.TestCase):
    def test_town_and_default_floors_are_paving_and_flagstones(self):
        h = (D / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn('#cb-map[data-biome="town"] { background: url("data:image/svg+xml,', h)      # cobbles, not grey dots
        self.assertNotIn("radial-gradient(circle at 50% 50%, #8d8676 0 38%", h)
        self.assertGreaterEqual(h.count("data:image/svg+xml"), 2)                                   # town paving and dungeon flagstones
        self.assertIn("#cb-map .cb-range { position: absolute; background: rgba(255,225,130,.11);", h)  # range tint no longer hides the floor


class UsabilityBatchTest(unittest.TestCase):
    def test_wiring(self):
        h = (D / "templates" / "index.html").read_text(encoding="utf-8")
        for needle in ('id="now-line"', "function _nowLineTick()", "fetch('/dm/nudge'", "function _rollDice3D(", "function _d3Parse(",
                       'id="dice3d"', "_cbPrevPos[tk.name] = { x: tk.x, y: tk.y };", "translate: '-5px 0'"):
            self.assertIn(needle, h)
        m = (D / "templates" / "menu.html").read_text(encoding="utf-8")
        for needle in ("async function refreshHealth()", 'id="start-big"', "function updateStartBig()", "fetch('/menu/health')"):
            self.assertIn(needle, m)

    def test_dice_parser_picks_the_natural_die(self):
        import shutil, subprocess, tempfile
        node = shutil.which("node")
        if not node:
            self.skipTest("node not installed")
        h = (D / "templates" / "index.html").read_text(encoding="utf-8")
        a = h.index("function _d3Parse(text) {"); b = h.index("function _d3Poly(sides) {")
        js = h[a:b] + """
const out = [_d3Parse('Dagna rolls 1d20+4: [18] +4 = 22 - Fire Bolt'), _d3Parse('Milo rolls 1d20: [14, 6] -> keep 14 (advantage) = 14'),
  _d3Parse('Joris rolls 2d6+3: [4, 5] +3 = 12'), _d3Parse('no dice here')];
console.log(JSON.stringify(out));"""
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
            f.write(js)
        r = subprocess.run([node, f.name], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        import json
        o = json.loads(r.stdout)
        self.assertEqual((o[0]["big"], o[0]["total"], o[0]["d20"]), (18, 22, True))
        self.assertEqual(o[1]["big"], 14)
        self.assertEqual((o[2]["big"], o[2]["total"], o[2]["sides"]), (9, 12, 6))
        self.assertIsNone(o[3])


class DayNightTest(unittest.TestCase):
    def test_wiring(self):
        h = (D / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn("document.body.dataset.tod = tod;", h)
        self.assertIn("ov.className = 'cb-tod';", h)
        self.assertIn("g.className = 'cb-glow';", h)
        self.assertIn("ov.style.maskComposite = 'intersect';", h)


if __name__ == "__main__":
    unittest.main()
