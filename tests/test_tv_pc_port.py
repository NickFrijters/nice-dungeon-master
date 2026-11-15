"""The TV/PC display features ported from the owner's patched install.

Covers the server side (relay to the TV, the input gate, which clips are
already stored) and checks the play screen carries the matching controls and
no leftover remote-voice wording.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
SCRIPTS = REPO / "skills" / "dnd" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(DISPLAY))


class PortedRoutesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / "campaigns").mkdir()
        (root / ".runtime").mkdir()
        cls._env = {k: os.environ.get(k) for k in ("DND_CAMPAIGN_ROOT", "DND_RUNTIME_DIR",
                                                   "DND_TTS_CACHE_DIR")}
        os.environ["DND_CAMPAIGN_ROOT"] = str(root)
        os.environ["DND_RUNTIME_DIR"] = str(root / ".runtime")
        os.environ["DND_TTS_CACHE_DIR"] = str(root / "ttscache")
        import runtime_paths  # remembers its folder from first import: pin it
        cls._rt_patch = mock.patch.object(runtime_paths, "RT", str(root / ".runtime"))
        cls._rt_patch.start()
        spec = importlib.util.spec_from_file_location("display_app_port", DISPLAY / "dnd-display-app.py")
        cls.mod = importlib.util.module_from_spec(spec)
        sys.modules["display_app_port"] = cls.mod
        spec.loader.exec_module(cls.mod)
        cls.client = cls.mod.app.test_client()
        assert str(root) in cls.mod.QUEUE_FILE, "test would touch the real runtime folder"

    @classmethod
    def tearDownClass(cls):
        cls._rt_patch.stop()
        for k, v in cls._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        cls.tmp.cleanup()

    def test_relay_forwards_a_control_message_to_every_display(self):
        sent = []
        with mock.patch.object(self.mod, "_broadcast", side_effect=sent.append):
            r = self.client.post("/relay", json={"msg": {"type": "scroll", "y": 120}})
        self.assertEqual(r.status_code, 204)
        self.assertEqual(sent, [{"relay": {"type": "scroll", "y": 120}}])

    def test_relay_rejects_junk_and_oversized_messages(self):
        for bad in ({"msg": "text"}, {"msg": ["x"]}, {}, {"msg": {"blob": "x" * 30000}}):
            self.assertEqual(self.client.post("/relay", json=bad).status_code, 400, str(bad)[:40])

    def test_input_gate_limits_who_may_type_and_can_be_cleared(self):
        with mock.patch.object(self.mod, "_broadcast"):
            r = self.client.post("/input-gate", json={"allowed": ["Mira", "Joris"]})
            self.assertEqual(r.get_json(), {"allowed": ["Mira", "Joris"]})
            r = self.client.post("/input-gate", json={"allowed": None})
        self.assertIsNone(r.get_json()["allowed"])

    def test_tts_cached_reports_which_clips_are_stored(self):
        tts = self.mod._tts
        tts.cache_put(tts.cache_key("A stored line."), b"\x01\x02" * 50)
        r = self.client.post("/tts-cached", json={"texts": ["A stored line.", "Not stored yet."]})
        self.assertEqual(r.get_json(), {"cached": [True, False]})


class PlayScreenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (DISPLAY / "templates" / "index.html").read_text(encoding="utf-8")

    def test_tv_pc_view_controls_are_present(self):
        self.assertIn("tv-view", self.html)
        self.assertRegex(self.html, r"PC View|PC VIEW")

    def test_status_line_and_turn_bar_exist(self):
        for needle in ("tts-status", "turn-bar", "_ttsMakeStatus", "_ttsForceRetry"):
            self.assertIn(needle, self.html)

    def test_no_remote_voice_wording_is_left(self):
        for needle in ("Gemini", "gemini", "API call", "Retry Gemini", "_ttsServerOk"):
            self.assertNotIn(needle, self.html)


if __name__ == "__main__":
    unittest.main()
