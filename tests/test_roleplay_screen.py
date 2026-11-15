"""Roleplay screen: NPCs here, the scene picture column, the active quest strip."""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "skills" / "dnd" / "display" / "templates" / "index.html").read_text(encoding="utf-8")
PUSH = (ROOT / "skills" / "dnd" / "display" / "push_stats.py").read_text(encoding="utf-8")
APP = (ROOT / "skills" / "dnd" / "display" / "dnd-display-app.py").read_text(encoding="utf-8")
CMD = (ROOT / "skills" / "dnd" / "SKILL-commands.md").read_text(encoding="utf-8")


class RoleplayScreenTest(unittest.TestCase):
    def test_page_has_the_three_new_regions(self):
        for needle in ('id="sb-npcs"', 'id="scene-gallery"', 'id="quest-strip"', "function _galleryAdd(im)"):
            self.assertIn(needle, HTML)

    def test_the_gallery_keeps_only_the_newest_three(self):
        self.assertIn("items.length - 3", HTML)
        self.assertIn("_galleryAdd(im);", HTML)

    def test_npcs_travel_from_push_stats_to_the_display(self):
        self.assertIn('"--npcs"', PUSH)
        self.assertIn('payload["npcs"]', PUSH)
        self.assertIn('_current_stats["npcs"]', APP)

    def test_the_dm_is_told_to_push_npcs(self):
        self.assertIn("push_stats.py --npcs", CMD)


if __name__ == "__main__":
    unittest.main()
