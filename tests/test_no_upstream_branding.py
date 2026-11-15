"""The shipped app carries no upstream branding.

LICENSE and NOTICE.md must keep naming the upstream project (the licence
requires it), and CHANGELOG.md is history, so those are exempt.
"""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXEMPT = {"LICENSE", "NOTICE.md", "CHANGELOG.md", "tests/test_no_upstream_branding.py"}
SKIP_DIRS = {".git", ".claude", "__pycache__", "node_modules"}
BRAND = ("neural initiative", "neuralinitiative", "neural core", "neural-initiative")
TEXT = {".py", ".md", ".json", ".html", ".js", ".css", ".txt", ".sh", ".yml", ".yaml", ".toml", ".svg"}


def shipped_files():
    for p in ROOT.rglob("*"):
        rel = p.relative_to(ROOT)
        if p.is_file() and not (set(rel.parts) & SKIP_DIRS):
            yield p, rel.as_posix()


class NoBrandingTests(unittest.TestCase):
    def test_no_brand_string_in_text_files(self):
        hits = []
        for p, rel in shipped_files():
            if rel in EXEMPT or p.suffix.lower() not in TEXT:
                continue
            text = p.read_text(encoding="utf-8", errors="ignore").lower()
            hits += [f"{rel}: {b}" for b in BRAND if b in text]
        self.assertEqual(hits, [])

    def test_old_brand_art_is_gone(self):
        self.assertFalse((ROOT / "skills/dnd/display/icons/lockup_badge_round.png").exists())
        self.assertFalse((ROOT / "design-mockups").exists())

    def test_notice_credits_upstream_and_license_is_kept(self):
        notice = (ROOT / "NOTICE.md").read_text(encoding="utf-8")
        self.assertIn("claude-dnd-skill", notice)
        self.assertIn("AGPL", notice)
        self.assertIn("modified", notice)
        self.assertTrue((ROOT / "LICENSE").exists())
        self.assertIn("NOTICE.md", (ROOT / "README.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
