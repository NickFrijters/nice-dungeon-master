"""Combat replaces the roleplay screen, stays short and fast, and a stale remembered fight does not open on load."""
import re
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "skills" / "dnd" / "display"
HTML = (D / "templates" / "index.html").read_text(encoding="utf-8")
APP = (D / "dnd-display-app.py").read_text(encoding="utf-8")
SKILL = (ROOT / "skills" / "dnd" / "SKILL.md").read_text(encoding="utf-8")
COMMANDS = (ROOT / "skills" / "dnd" / "SKILL-commands.md").read_text(encoding="utf-8")


def _state_md_combat(folder: Path):
    a = APP.index("_NO_COMBAT_RE = ")
    b = APP.index("def _restore_campaign_display")
    ns = {"re": re, "_find_campaign": lambda name: folder}
    exec(APP[a:b], ns)
    return ns["_state_md_combat"]


class CombatReplacesRoleplayTest(unittest.TestCase):
    def test_page_hides_the_roleplay_screen_during_combat(self):
        for sel in ("#scene-gallery", "#quest-strip", "#mini-tracker", "#sb-npcs", "#sb-quests"):
            self.assertIn("body.combat " + sel, HTML)
        self.assertIn("body.combat #text-scroll { inset: auto 0 62px 0; height: 22vh;", HTML)   # the story is a short strip
        self.assertIn("bottom: calc(22vh + 72px)", HTML)                                          # the tabletop gets the rest
        self.assertNotIn("padding-top: calc(74px + 60vh + 14px)", HTML)

    def test_combat_typing_is_fast(self):
        self.assertIn("if (document.body.classList.contains('combat')) return Math.min(charDelay, 4);", HTML)

    def test_dm_rules_for_short_fast_combat_and_clean_load(self):
        self.assertIn("10 to 40 words", SKILL)
        self.assertIn("--combat '{}' --turn-clear", SKILL)
        self.assertIn("Combat on load", COMMANDS)

    def test_notes_decide_whether_a_remembered_fight_is_real(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            check = _state_md_combat(folder)
            self.assertIsNone(check("x"))                                       # no state.md: unknown, keep what was saved
            for body, want in [("*(none)*", False), ("none", False), ("", False), ("(geen)", False), ("-", False),
                               ("Ronde 2, Old Tunnel, gevecht tegen 2 Rattenzwermen.", True),
                               ("Round 1: goblins in the cellar", True)]:
                (folder / "state.md").write_text("# State\n\n## Active Combat\n" + body + "\n\n## Live State Flags\nx\n", encoding="utf-8")
                self.assertIs(check("x"), want, body)
            (folder / "state.md").write_text("# State\n\n## Active Combat\n*(none)*", encoding="utf-8")   # last section of the file
            self.assertIs(check("x"), False)
            (folder / "state.md").write_text("# State\n\n## Other\nhello\n", encoding="utf-8")            # no such section
            self.assertIsNone(check("x"))

    def test_load_drops_a_stale_fight(self):
        self.assertIn('if _state_md_combat(name) is False:', APP)
        self.assertIn('for k in ("combat", "turn_order"):', APP)


if __name__ == "__main__":
    unittest.main()
