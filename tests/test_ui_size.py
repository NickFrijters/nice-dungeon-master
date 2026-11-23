"""Interface text size: its own setting for the character list, Party Input and the combat lists; and the story
follows the typing without mistaking its own scrolling for the reader's."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "skills" / "dnd" / "display"
sys.path.insert(0, str(ROOT / "skills" / "dnd" / "scripts"))
import paths     # noqa: E402
import settings  # noqa: E402

HTML = (D / "templates" / "index.html").read_text(encoding="utf-8")
SETTINGS_HTML = (D / "templates" / "settings.html").read_text(encoding="utf-8")


class UiSizeSettingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = mock.patch.object(paths, "_root", lambda: Path(self.tmp.name))
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def test_default_save_and_limits(self):
        self.assertEqual(settings.load()["ui_size"], 1.0)
        self.assertEqual(settings.save({"ui_size": 1.4})["ui_size"], 1.4)
        self.assertEqual(settings.display_settings()["ui_size"], 1.4)       # every screen gets it
        for bad in (0.5, 3, "big", True):
            with self.assertRaises(ValueError):
                settings.save({"ui_size": bad})
        self.assertEqual(settings.save({"ui_size": 1.33})["ui_size"], 1.35)  # snapped to the 5% step

    def test_settings_page_and_play_screen(self):
        self.assertIn('id="ui-size"', SETTINGS_HTML)
        self.assertIn("Interface text size", SETTINGS_HTML)
        self.assertIn("renderStepper('ui_size', 'ui-size')", SETTINGS_HTML)
        self.assertIn("document.documentElement.style.setProperty('--ui-scale', String(d.ui_size || 1));", HTML)
        self.assertIn("#sidebar, #input-panel, #cb-side { zoom: var(--ui-scale, 1); }", HTML)
        self.assertIn("calc(20px + 210px * var(--ui-scale, 1))", HTML)          # the story moves aside as the list grows


class FollowTheTextTest(unittest.TestCase):
    def test_only_the_reader_stops_the_follow_and_it_follows_instantly(self):
        self.assertIn("else if (Date.now() - _readerScrollAt < 1200) userScrolledUp = true;", HTML)
        self.assertIn("textScroll.scrollTo({ top, behavior: 'instant' });", HTML)        # top = the end (in combat: the newest line)
        self.assertIn("let top = textScroll.scrollHeight;", HTML)
        self.assertIn("(_pageHeld ? 'press Continue or Enter to read on' : 'writing...')", HTML)


if __name__ == "__main__":
    unittest.main()
