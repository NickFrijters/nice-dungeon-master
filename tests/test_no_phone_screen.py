"""There is no phone screen: every device gets the same display.

Guards against the phone controller coming back (Phone Mode button, the character-select
first screen, the input-only layout, per-character stream binding, phone-targeted routing).
"""

from __future__ import annotations

import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
HTML = (DISPLAY / "templates" / "index.html").read_text(encoding="utf-8")
SERVER = (DISPLAY / "dnd-display-app.py").read_text(encoding="utf-8")

PAGE_STRINGS = (
    "phone-mode-btn", "phone-mode-menu", "phone-settings", "phone-status", "char-select",
    "input-only", "full-mode-btn", "dp-tabs", "character-pane", "data-tab-section",
    "data-active-tab", "view=input", "_initCharSelect", "_initInputTabs", "_setPhoneStatus",
)
SERVER_STRINGS = ("_phone_present", "_client_chars", "/roll-pref", "/character/<character>")


class NoPhoneScreenTest(unittest.TestCase):
    def test_the_page_has_no_phone_ui(self):
        for s in PAGE_STRINGS:
            self.assertNotIn(s, HTML, s)

    def test_the_page_does_not_read_a_character_from_the_address(self):
        self.assertNotIn("get('char')", HTML)
        self.assertNotIn("get('character')", HTML)
        self.assertIn("new EventSource('/stream')", HTML)

    def test_the_server_has_no_phone_routing(self):
        for s in SERVER_STRINGS:
            self.assertNotIn(s, SERVER, s)

    def test_dice_requests_still_open_the_on_screen_roller(self):
        self.assertIn('"onscreen_targets": [c for c in chars if c.lower() != "any"]', SERVER)
        self.assertIn("_onscreenDiceOn()", HTML)
        self.assertIn("window._edOpenDrawer('dice-drawer')", HTML)


if __name__ == "__main__":
    unittest.main()
