"""Save & exit: ask Claude to save, send every screen to the Main Menu, keep the display running.

Also the narration fade at the top of the story area.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "dnd"
DISPLAY = SKILL / "display"
SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(DISPLAY))

import paths  # noqa: E402


class ServerTest(unittest.TestCase):
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
        spec = importlib.util.spec_from_file_location("display_app_exit", DISPLAY / "dnd-display-app.py")
        cls.mod = importlib.util.module_from_spec(spec)
        sys.modules["display_app_exit"] = cls.mod
        spec.loader.exec_module(cls.mod)
        cls.client = cls.mod.app.test_client()
        cls.beat = Path(cls.mod.rt(".dm-heartbeat"))
        assert str(root) in str(cls.beat), "test would touch the real runtime folder"

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

    def setUp(self):
        for f in (self.beat, Path(self.mod.QUEUE_FILE)):
            if f.exists():
                f.unlink()
        self.mod._exit_requested_at = None
        self.mod._MENU_FIRST = False

    def _listening(self, age=0):
        self.beat.write_text("x", encoding="utf-8")
        t = time.time() - age
        os.utime(self.beat, (t, t))

    def test_it_asks_claude_to_save_and_sends_every_screen_to_the_menu(self):
        self._listening()
        sent = []
        with mock.patch.object(self.mod, "_broadcast", side_effect=sent.append):
            r = self.client.post("/menu/exit")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["asked_claude"])
        self.assertEqual(Path(self.mod.QUEUE_FILE).read_text(encoding="utf-8"), "[Menu]: save-exit\n")
        self.assertIn({"relay": {"t": "go-menu"}}, sent)          # the TV goes too

    def test_the_root_address_opens_the_menu_again_afterwards(self):
        self._listening()
        self.client.post("/menu/exit")
        r = self.client.get("/")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/menu"))

    def test_it_does_not_stop_the_display(self):
        self._listening()
        stopped = mock.Mock()
        with mock.patch.object(self.mod, "_exit_display", stopped):
            self.client.post("/menu/exit")
        stopped.assert_not_called()

    def test_without_a_dungeon_master_nothing_is_queued_but_the_screens_still_leave(self):
        sent = []
        with mock.patch.object(self.mod, "_broadcast", side_effect=sent.append):
            r = self.client.post("/menu/exit")
        self.assertFalse(r.get_json()["asked_claude"])
        self.assertFalse(Path(self.mod.QUEUE_FILE).exists())
        self.assertIn({"relay": {"t": "go-menu"}}, sent)
        self.assertFalse(self.client.get("/menu/campaigns").get_json()["saving"])

    def test_saving_shows_until_claude_is_back_in_its_wait_loop(self):
        self._listening(age=5)
        self.client.post("/menu/exit")
        self.assertTrue(self.client.get("/menu/campaigns").get_json()["saving"])
        self.beat.write_text("again", encoding="utf-8")           # Claude's next wait begins
        os.utime(self.beat, (time.time() + 2, time.time() + 2))
        self.assertFalse(self.client.get("/menu/campaigns").get_json()["saving"])

    def test_saving_gives_up_after_ten_minutes(self):
        self._listening(age=5)
        self.client.post("/menu/exit")
        self.mod._exit_requested_at = time.time() - 700
        self.assertFalse(self.client.get("/menu/campaigns").get_json()["saving"])

    def test_another_device_cannot_do_it(self):
        self._listening()
        r = self.client.post("/menu/exit", environ_overrides={"REMOTE_ADDR": "192.168.77.23"})
        self.assertEqual(r.status_code, 403)
        self.assertFalse(Path(self.mod.QUEUE_FILE).exists())

    def test_the_menu_page_has_a_saving_notice(self):
        html = self.client.get("/menu").get_data(as_text=True)
        self.assertIn('id="saving"', html)
        self.assertIn("Saving your session", html)
        self.assertIn("data.saving", html)


class PageAndSkillTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (DISPLAY / "templates" / "index.html").read_text(encoding="utf-8")
        cls.cmds = (SKILL / "SKILL-commands.md").read_text(encoding="utf-8")

    def test_the_button_sits_in_the_bottom_left_menu_beside_the_cog(self):
        cog = re.search(r"#settings-cog \{[^}]*left: (\d+)px; bottom: (\d+)px;[^}]*width: (\d+)px", self.html, re.S)
        ex = re.search(r"#exit-btn\{ position:fixed; left:(\d+)px; bottom:(\d+)px", self.html)
        self.assertTrue(cog and ex)
        self.assertEqual(ex.group(2), cog.group(2))                                        # same row
        self.assertEqual(int(ex.group(1)) - (int(cog.group(1)) + int(cog.group(3))), 8)    # right next to it

    def test_it_is_on_every_screen_but_the_phone_view(self):
        self.assertNotIn("html.tv-view #exit-btn", self.html)
        self.assertIn("body.input-only #exit-btn{ display:none !important; }", self.html)

    def test_every_screen_follows_the_go_menu_message(self):
        self.assertIn("m.t === 'go-menu'", self.html)
        self.assertIn("fetch('/menu/exit'", self.html)

    def test_claude_is_told_how_to_handle_it(self):
        i = self.cmds.index("`[Menu]: save-exit`")
        step = self.cmds[i:self.cmds.index("\n", i)]
        for needle in ("`/dm:dnd save`", "do not stop the display", "conservative default",
                       "go back to the menu wait", "cannot answer chat questions"):
            self.assertIn(needle, step.replace("**", ""))

    def test_the_narration_fade_is_smooth_and_scales_with_the_screen(self):
        grads = re.findall(r"mask-image:linear-gradient\(([^;]*)\);", self.html)
        self.assertEqual(len(grads), 2)                                   # standard and -webkit-
        for g in grads:
            self.assertIn("vh", g)
            self.assertNotIn("px", g)
            stops = re.findall(r"rgba\(0,0,0,([.\d]+)\)", g)
            self.assertGreaterEqual(len(stops), 5)                        # an eased ramp, not one step
            self.assertEqual(stops, sorted(stops, key=float))             # steadily more visible
            self.assertTrue(g.startswith("to bottom, transparent 0"))


if __name__ == "__main__":
    unittest.main()
