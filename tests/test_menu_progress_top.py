"""Main Menu: the progress line and pills are at the top of the page, right under the title."""
import unittest
from pathlib import Path

MENU = (Path(__file__).resolve().parent.parent / "skills" / "dnd" / "display" / "templates" / "menu.html").read_text(encoding="utf-8")


class MenuProgressTopTest(unittest.TestCase):
    def test_progress_comes_before_the_campaign_list(self):
        self.assertLess(MENU.index('id="steps"'), MENU.index('<h2>Campaigns</h2>'))
        self.assertLess(MENU.index('id="status"'), MENU.index('<h2>Campaigns</h2>'))
        self.assertEqual(MENU.count('id="steps"'), 1)
        self.assertIn("#progress:has(#status:empty):has(#steps:empty) { display:none; }", MENU)



import importlib.util, os, sys, tempfile                      # noqa: E401,E402
from unittest import mock                                       # noqa: E402

REPO = Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"


class StoryReadyTest(unittest.TestCase):
    """When the DM has opened the active campaign and its story is on screen, the menu offers it, Continue goes
    straight in, and the story page no longer bounces back to the menu."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / "campaigns" / "camp").mkdir(parents=True)
        (root / "campaigns" / "camp" / "state.md").write_text("# State\n", encoding="utf-8")
        cls._env = {k: os.environ.get(k) for k in ("DND_CAMPAIGN_ROOT", "DND_RUNTIME_DIR")}
        os.environ["DND_CAMPAIGN_ROOT"] = str(root)
        os.environ["DND_RUNTIME_DIR"] = str(root / ".runtime")
        sys.path.insert(0, str(REPO / "skills" / "dnd" / "scripts")); sys.path.insert(0, str(DISPLAY))
        import paths
        import runtime_paths
        (root / ".runtime").mkdir(exist_ok=True)
        cls._rt_patch = mock.patch.object(runtime_paths, "RT", str(root / ".runtime")); cls._rt_patch.start()
        cls._root_patch = mock.patch.object(paths, "_root", lambda: root); cls._root_patch.start()
        spec = importlib.util.spec_from_file_location("display_app_story_ready", DISPLAY / "dnd-display-app.py")
        cls.mod = importlib.util.module_from_spec(spec)
        sys.modules["display_app_story_ready"] = cls.mod
        spec.loader.exec_module(cls.mod)
        cls.client = cls.mod.app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls._root_patch.stop(); cls._rt_patch.stop()
        for k, v in cls._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        cls.tmp.cleanup()

    def test_story_ready_stops_the_bounce_back_to_the_menu(self):
        self.mod._MENU_FIRST = True
        Path(self.mod.CAMP_FILE).write_text("camp", encoding="utf-8")
        with self.mod._text_log_lock:
            self.mod._text_log.clear()
        self.assertEqual(self.client.get("/").status_code, 302)                     # nothing on screen yet: the menu
        self.assertFalse(self.client.get("/menu/campaigns").get_json()["story_ready"])
        self.client.post("/chunk", json={"text": "Greenest is burning."})          # the DM's opening
        self.assertTrue(self.client.get("/menu/campaigns").get_json()["story_ready"])
        self.assertEqual(self.client.get("/").status_code, 200)                     # the story page stays

    def test_last_sessions_story_on_disk_is_not_a_live_story(self):
        # The real situation: the display is restarted with the menu, the old story is still in the log for the
        # active campaign, and the new DM is waiting at the Main Menu.
        self.mod._MENU_FIRST = True
        self.mod._story_live = False
        Path(self.mod.CAMP_FILE).write_text("camp", encoding="utf-8")
        with self.mod._text_log_lock:
            self.mod._text_log.clear()
            self.mod._text_log.append({"text": "Old story from yesterday.", "_camp": "camp"})
        self.assertEqual(self.client.get("/").status_code, 302)                            # the address bar shows the menu
        self.assertFalse(self.client.get("/menu/campaigns").get_json()["story_ready"])     # no "Go to the story", Continue loads
        self.client.post("/chunk", json={"text": "Welcome back."})
        self.assertTrue(self.client.get("/menu/campaigns").get_json()["story_ready"])     # the DM answered: now it is live
        self.client.post("/menu/exit", json={})                                              # Save & exit goes back to the menu
        self.assertFalse(self.client.get("/menu/campaigns").get_json()["story_ready"])
        self.assertEqual(self.client.get("/").status_code, 302)

    def test_menu_offers_the_story_and_keeps_its_buttons(self):
        self.assertIn('id="story-ready"', MENU)
        self.assertIn("(c.active && window.lastStoryReady) ? (location.href = '/')", MENU)
        self.assertIn("if (!confirming && key !== window._listKey)", MENU)          # no redraw (and lost click) every poll


if __name__ == "__main__":
    unittest.main()
