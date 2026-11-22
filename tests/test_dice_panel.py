"""A DM-requested roll opens a visible dice panel (the old drawer was Editorial-only and always hidden in Vellum),
the tracker says whose roll the DM waits for, and the panel closes after the roll was sent."""
import unittest
from pathlib import Path

HTML = (Path(__file__).resolve().parent.parent / "skills" / "dnd" / "display" / "templates" / "index.html").read_text(encoding="utf-8")


class DicePanelTest(unittest.TestCase):
    def test_the_drawer_is_visible_when_open(self):
        self.assertIn("#dice-drawer.ed-open { display: block;", HTML)
        self.assertIn("#dice-drawer.ed-open #dice-pad { display: block; }", HTML)
        self.assertIn('<button id="dice-close"', HTML)
        self.assertIn('id="party-dice-btn"', HTML)                                  # open it by hand from Party Input

    def test_it_closes_after_the_roll_and_starts_clean(self):
        self.assertIn("setTimeout(() => { if (window._edCloseDrawers) window._edCloseDrawers(); }, 1800);", HTML)
        self.assertIn("const _rl = document.getElementById('dp-result-line'); if (_rl) _rl.textContent = '';", HTML)
        self.assertIn("body.ed-drawer-open #dice-pending-badge { display: none !important; }", HTML)

    def test_tracker_names_the_roller(self):
        self.assertIn("window._dicePendingNames = [...new Set(entries.flatMap(e => e.pending || []))];", HTML)
        self.assertIn("'the DM is waiting for ' + rollers.join(' and ')", HTML)


if __name__ == "__main__":
    unittest.main()
