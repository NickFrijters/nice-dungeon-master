"""Display settings moved from the play screen into the Settings menu.

One value for every screen (PC and TV): stored on the server, seeded into the
page, and pushed live to every open screen when changed.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
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

import paths  # noqa: E402
import settings  # noqa: E402

NODE = shutil.which("node")


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(paths, "_root", lambda: Path(self.tmp.name))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_defaults_match_what_the_play_screen_did(self):
        s = settings.load()
        self.assertEqual((s["theme"], s["skin"], s["type_speed"]), ("dark", "vellum", "normal"))
        self.assertEqual(s["text_size"], 0.75)
        self.assertFalse(s["roll_on_screen"])
        self.assertTrue(s["sound_effects"])        # the TV default was sound on

    def test_valid_changes_are_saved(self):
        settings.save({"theme": "light", "skin": "editorial", "type_speed": "instant",
                       "roll_on_screen": True, "sound_effects": False, "text_size": 1.2})
        s = settings.load()
        self.assertEqual((s["theme"], s["skin"], s["type_speed"]), ("light", "editorial", "instant"))
        self.assertTrue(s["roll_on_screen"])
        self.assertFalse(s["sound_effects"])
        self.assertEqual(s["text_size"], 1.2)

    def test_bad_values_are_rejected(self):
        for bad in ({"theme": "purple"}, {"skin": "neon"}, {"type_speed": "warp"},
                    {"text_size": 5}, {"text_size": 0.1}, {"text_size": True},
                    {"text_size": "big"}, {"roll_on_screen": "yes"}, {"sound_effects": 1}):
            with self.assertRaises(ValueError, msg=str(bad)):
                settings.save(bad)

    def test_text_size_snaps_to_steps(self):
        settings.save({"text_size": 0.77})
        self.assertEqual(settings.load()["text_size"], 0.75)
        settings.save({"text_size": 1.23})
        self.assertEqual(settings.load()["text_size"], 1.25)

    def test_the_play_screen_gets_exactly_the_keys_it_draws_with(self):
        self.assertEqual(set(settings.display_settings()), set(settings.DISPLAY_KEYS))
        self.assertNotIn("flavour", settings.display_settings())


class RouteAndPageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / "campaigns").mkdir()
        (root / ".runtime").mkdir()
        cls._env = {k: os.environ.get(k) for k in ("DND_CAMPAIGN_ROOT", "DND_RUNTIME_DIR")}
        os.environ["DND_CAMPAIGN_ROOT"] = str(root)
        os.environ["DND_RUNTIME_DIR"] = str(root / ".runtime")
        import runtime_paths  # remembers its folder from first import: pin both
        cls._patches = [mock.patch.object(paths, "_root", lambda: root),
                        mock.patch.object(runtime_paths, "RT", str(root / ".runtime"))]
        for p in cls._patches:
            p.start()
        spec = importlib.util.spec_from_file_location("display_app_dsettings", DISPLAY / "dnd-display-app.py")
        cls.mod = importlib.util.module_from_spec(spec)
        sys.modules["display_app_dsettings"] = cls.mod
        spec.loader.exec_module(cls.mod)
        cls.client = cls.mod.app.test_client()
        assert str(root) in cls.mod.QUEUE_FILE, "test would touch the real runtime folder"

    @classmethod
    def tearDownClass(cls):
        for p in cls._patches:
            p.stop()
        for k, v in cls._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        cls.tmp.cleanup()

    def test_changing_a_display_setting_tells_every_open_screen(self):
        sent = []
        with mock.patch.object(self.mod, "_broadcast", side_effect=sent.append):
            r = self.client.post("/settings/save", json={"theme": "light", "text_size": 1.5})
        self.assertEqual(r.status_code, 200)
        shown = [m["display_settings"] for m in sent if "display_settings" in m]
        self.assertEqual(len(shown), 1)
        self.assertEqual(shown[0]["theme"], "light")
        self.assertEqual(shown[0]["text_size"], 1.5)
        self.assertEqual(set(shown[0]), set(settings.DISPLAY_KEYS))

    def test_story_settings_do_not_wake_the_screens(self):
        sent = []
        with mock.patch.object(self.mod, "_broadcast", side_effect=sent.append):
            self.client.post("/settings/save", json={"combat": "low"})
        self.assertEqual([m for m in sent if "display_settings" in m], [])

    def test_sound_effects_switch_applies_on_the_server_at_once(self):
        fake_audio = mock.Mock()
        with mock.patch.object(self.mod, "_audio", fake_audio), \
             mock.patch.object(self.mod, "_broadcast"):
            self.client.post("/settings/save", json={"sound_effects": False})
        fake_audio.set_sfx.assert_called_once_with(False)

    def test_the_page_is_seeded_with_the_saved_settings(self):
        settings.save({"theme": "light", "skin": "editorial", "text_size": 1.1})
        html = self.client.get("/").get_data(as_text=True)
        m = re.search(r"window\._DISPLAY = (\{.*?\});", html, re.S)
        self.assertIsNotNone(m, "the page carries no seeded settings")
        seeded = json.loads(m.group(1))
        self.assertEqual((seeded["theme"], seeded["skin"], seeded["text_size"]),
                         ("light", "editorial", 1.1))

    @unittest.skipUnless(NODE, "node not available")
    def test_every_script_in_the_rendered_page_compiles(self):
        html = self.client.get("/").get_data(as_text=True)
        scripts = re.findall(r"<script(?:\s[^>]*)?>(.*?)</script>", html, re.S)
        self.assertGreaterEqual(len(scripts), 2)
        for i, s in enumerate(scripts):
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
                f.write("new Function(" + json.dumps(s) + ");")
                path = f.name
            try:
                r = subprocess.run([NODE, path], capture_output=True, text=True,
                                   encoding="utf-8", timeout=30)
                self.assertEqual(r.returncode, 0, f"script {i}: {r.stderr[:300]}")
            finally:
                os.unlink(path)

    def test_the_settings_page_data_lists_the_new_options(self):
        d = self.client.get("/settings/data").get_json()
        for key in ("theme", "skin", "type_speed", "text_size"):
            self.assertIn(key, d["options"])
        self.assertEqual(d["options"]["text_size"]["step"], 0.05)


class PlayScreenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (DISPLAY / "templates" / "index.html").read_text(encoding="utf-8")
        cls.page = (DISPLAY / "templates" / "settings.html").read_text(encoding="utf-8")

    def test_the_old_settings_column_is_gone(self):
        for old in ('id="sfx-row"', 'id="theme-row"', 'id="skin-row"', 'id="textsize-row"',
                    'id="speed-row"', 'id="verbosity-row"', 'id="onscreen-dice-row"', 'id="narrate-row"'):
            self.assertNotIn(old, self.html, old)

    def test_there_is_a_cog_that_opens_the_settings_menu_in_the_game(self):
        self.assertIn('id="settings-cog"', self.html)
        self.assertIn("/settings?embedded=1", self.html)
        self.assertNotIn("html.tv-view #settings-cog", self.html)   # every screen has the cog; only the phone view hides it
        self.assertIn("body.input-only #settings-cog { display: none !important; }", self.html)

    def test_the_page_follows_live_changes(self):
        self.assertIn("payload.display_settings", self.html)
        self.assertIn("function _applyDisplaySettings", self.html)

    def test_a_paced_reveal_keeps_the_new_typing_speed(self):
        body = self.html[self.html.index("function _applyDisplaySettings"):]
        body = body[:body.index("_ttsAutoNarrate = ")]
        self.assertIn("_userCharDelay !== null", body)

    def test_the_settings_page_has_the_display_section_and_a_done_button(self):
        self.assertIn("Display (every screen)", self.page)
        for key in ("theme", "skin", "type_speed", "sound_effects", "roll_on_screen"):
            self.assertIn(f'data-key="{key}"', self.page)
        self.assertIn("text-size", self.page)
        self.assertIn("dnd-close-settings", self.page)


if __name__ == "__main__":
    unittest.main()
