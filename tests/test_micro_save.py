"""micro_save.py: the cheap per-turn save writes only the changed lines of state.md."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "dnd" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import micro_save  # noqa: E402
import paths  # noqa: E402

TEMPLATE = (REPO / "skills" / "dnd" / "templates" / "state.md").read_text(encoding="utf-8")


class MicroSaveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        d = self.root / "campaigns" / "ms-test"
        d.mkdir(parents=True)
        self.state = d / "state.md"
        self.state.write_text(TEMPLATE, encoding="utf-8")
        p = mock.patch.object(paths, "_root", lambda: self.root)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def run_ms(self, *args):
        return micro_save.main(["-c", "ms-test", *args])

    def test_event_replaces_the_placeholder_and_appends(self):
        self.assertEqual(self.run_ms("--event", "Mira picks the lock."), 0)
        self.assertEqual(self.run_ms("--event", "Hobb sees it."), 0)
        text = self.state.read_text(encoding="utf-8")
        self.assertIn("## Recent Events\n- Mira picks the lock.\n- Hobb sees it.", text)
        self.assertNotIn("Session 1 pending", text)
        self.assertIn("## Active Combat", text)            # the next section is untouched

    def test_only_the_last_twelve_events_are_kept(self):
        for i in range(15):
            self.run_ms("--event", f"beat {i}")
        text = self.state.read_text(encoding="utf-8")
        self.assertNotIn("beat 2\n", text)
        self.assertIn("beat 3\n", text)
        self.assertIn("beat 14\n", text)

    def test_flag_is_set_replaced_and_removed(self):
        self.run_ms("--flag", "NPC Hobb: hostile, wants 5 gp")
        self.run_ms("--flag", "NPC Hobb: friendly again")
        text = self.state.read_text(encoding="utf-8")
        self.assertEqual(text.count("NPC Hobb:"), 1)
        self.assertIn("- NPC Hobb: friendly again", text)
        self.assertIn("**Faction stances**", text)          # the rest of the section is intact
        self.run_ms("--unflag", "NPC Hobb")
        self.assertNotIn("NPC Hobb", self.state.read_text(encoding="utf-8"))

    def test_file_is_otherwise_byte_identical_and_no_temp_left(self):
        self.run_ms("--event", "x")
        text = self.state.read_text(encoding="utf-8")
        self.assertEqual(text.replace("- x\n", "*(Session 1 pending)*\n", 1), TEMPLATE)
        self.assertEqual([p.name for p in self.state.parent.iterdir() if p.name.endswith(".tmp")], [])

    def test_unknown_campaign_fails_without_creating_anything(self):
        self.assertEqual(micro_save.main(["-c", "nope", "--event", "x"]), 1)


if __name__ == "__main__":
    unittest.main()
