"""The small pop-ups (DM Help hint block, tracker, toast, Save & exit dialog) share the parchment look."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

HTML = (Path(__file__).resolve().parent.parent / "skills" / "dnd" / "display" / "templates" / "index.html").read_text(encoding="utf-8")


def rule(selector: str) -> str:
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", HTML)
    assert m, selector
    return m.group(1)


class PopupStyleTest(unittest.TestCase):
    def test_no_native_browser_dialogs_on_the_play_screen(self):
        code = re.sub(r"//[^\n]*", "", HTML)
        self.assertNotRegex(code, r"(?<![\w.])(confirm|alert|prompt)\(")

    def test_save_exit_uses_the_styled_dialog(self):
        self.assertIn("window._dndDialog = function", HTML)
        self.assertIn("Could not save and exit", HTML)
        self.assertIn("fetch('/menu/exit'", HTML)

    def test_dialog_closes_with_x_escape_and_outside_click(self):
        self.assertIn("e.key === 'Escape'", HTML[HTML.index("window._dndDialog"):])
        self.assertIn("aria-label', 'Close'", HTML)
        self.assertIn("e.target === ov", HTML)

    def test_toast_is_the_vellum_card_with_a_close_button(self):
        css = rule("#toast")
        self.assertIn("var(--pp-bg)", css)
        self.assertIn("var(--pp-font)", css)
        self.assertNotIn("Syne", css)
        self.assertIn("pp-close", HTML[HTML.index("function _showToast"):][:700])

    def test_tutor_block_is_gold_not_green_and_readable(self):
        css = rule(".tutor-label")
        self.assertIn("'Cinzel'", css)
        size = int(re.search(r"font-size:\s*(\d+)px", css).group(1))
        self.assertGreaterEqual(size, 14)
        self.assertNotIn("130, 190, 130", HTML)

    def test_tracker_uses_tokens_and_a_title(self):
        css = rule("#mini-tracker")
        self.assertIn("var(--pp-bg)", css)
        self.assertIn("mt-title", HTML)

    def test_light_theme_swaps_the_tokens(self):
        self.assertIn(':root[data-theme="light"] {\n    --pp-bg', HTML)


if __name__ == "__main__":
    unittest.main()
