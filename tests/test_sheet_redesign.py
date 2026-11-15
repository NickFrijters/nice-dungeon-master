"""The character sheet pop-up is a card: boxed numbers, big text, long text folded."""
import unittest
from pathlib import Path

HTML = (Path(__file__).resolve().parent.parent / "skills" / "dnd" / "display" / "templates" / "index.html").read_text(encoding="utf-8")


class SheetRedesignTest(unittest.TestCase):
    def test_numbers_are_boxed_and_large(self):
        self.assertIn("#sheet-panel .sm-stat-cell { border:2px solid", HTML)
        self.assertIn("#sheet-panel .sm-stat-val { font-size:28px", HTML)

    def test_long_feature_text_is_folded_until_clicked(self):
        self.assertIn("-webkit-line-clamp:3", HTML)
        self.assertIn("el.classList.toggle('open')", HTML)


if __name__ == "__main__":
    unittest.main()
