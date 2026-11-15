"""There is one look only: Vellum. The Editorial style is gone."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "skills" / "dnd" / "display" / "templates" / "index.html").read_text(encoding="utf-8")


class NoEditorialTest(unittest.TestCase):
    def test_no_editorial_style_rules(self):
        self.assertEqual(HTML.count('[data-skin="editorial"]'), 0)

    def test_skin_list_is_vellum_only(self):
        import sys
        sys.path.insert(0, str(ROOT / "skills" / "dnd" / "scripts"))
        import settings
        self.assertEqual(settings.SKINS, ("vellum",))
        self.assertNotIn("id: 'editorial'", HTML)


if __name__ == "__main__":
    unittest.main()
