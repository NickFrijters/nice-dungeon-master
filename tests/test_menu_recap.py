"""Opening a campaign from the Main Menu starts with a recap, not the old history."""
import unittest
from pathlib import Path

CMD = (Path(__file__).resolve().parent.parent / "skills" / "dnd" / "SKILL-commands.md").read_text(encoding="utf-8")


class MenuRecapTest(unittest.TestCase):
    def test_menu_load_skips_the_tail_replay_and_sends_a_recap(self):
        line = next(l for l in CMD.splitlines() if l.lstrip().startswith("- `[Menu]: load <campaign-name>`"))
        self.assertIn("skip the *Session tail replay*", line)
        self.assertIn("opening narration", line)

    def test_tail_replay_step_says_it_is_skipped_for_the_menu(self):
        self.assertIn("skipped when started by `[Menu]: load`", CMD)


if __name__ == "__main__":
    unittest.main()
