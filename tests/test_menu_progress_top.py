"""Main Menu: the progress line and pills are at the top of the page, right under the title."""
import unittest
from pathlib import Path

MENU = (Path(__file__).resolve().parent.parent / "skills" / "dnd" / "display" / "templates" / "menu.html").read_text(encoding="utf-8")


class MenuProgressTopTest(unittest.TestCase):
    def test_progress_comes_before_the_campaign_list(self):
        self.assertLess(MENU.index('id="steps"'), MENU.index('<h2>Campaigns</h2>'))
        self.assertLess(MENU.index('id="status"'), MENU.index('<h2>Campaigns</h2>'))
        self.assertEqual(MENU.count('id="steps"'), 1)
        self.assertIn("#progress:has(#status:empty):has(#steps:empty) { display:none; }", MENU)


if __name__ == "__main__":
    unittest.main()
