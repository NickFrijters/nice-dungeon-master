"""The sidebar matches the character files: an empty party pushed for a campaign that has characters is replaced by
the characters on disk. An empty story screen always says what is going on."""
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


class PartyFromDiskTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = cls.root = Path(cls.tmp.name)
        for camp in ("with-milo", "empty-camp"):
            (root / "campaigns" / camp / "characters").mkdir(parents=True)
            (root / "campaigns" / camp / "state.md").write_text("# State\n", encoding="utf-8")
        (root / "campaigns" / "with-milo" / "characters" / "Milo Hearthglow.md").write_text(
            "# Milo Hearthglow\n- **Race:** Halfling | **Class:** Bard | **Level:** 1\n- **HP:** 9 / 9\n", encoding="utf-8")
        cls._env = {k: os.environ.get(k) for k in ("DND_CAMPAIGN_ROOT", "DND_RUNTIME_DIR")}
        os.environ["DND_CAMPAIGN_ROOT"] = str(root)
        os.environ["DND_RUNTIME_DIR"] = str(root / ".runtime")
        sys.path.insert(0, str(SCRIPTS)); sys.path.insert(0, str(DISPLAY))
        import paths
        import runtime_paths
        (root / ".runtime").mkdir(exist_ok=True)
        cls._rt_patch = mock.patch.object(runtime_paths, "RT", str(root / ".runtime")); cls._rt_patch.start()
        cls._root_patch = mock.patch.object(paths, "_root", lambda: root); cls._root_patch.start()
        spec = importlib.util.spec_from_file_location("display_app_party_disk", DISPLAY / "dnd-display-app.py")
        cls.mod = importlib.util.module_from_spec(spec)
        sys.modules["display_app_party_disk"] = cls.mod
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

    def names(self):
        return [p["name"] for p in self.mod._current_stats.get("players", [])]

    def test_an_empty_party_for_a_campaign_with_characters_shows_the_files(self):
        self.client.post("/stats", json={"campaign": "with-milo", "replace_players": True, "players": []})
        self.assertEqual(self.names(), ["Milo Hearthglow"])

    def test_a_campaign_without_characters_stays_empty(self):
        self.client.post("/stats", json={"campaign": "empty-camp", "replace_players": True, "players": []})
        self.assertEqual(self.names(), [])

    def test_a_real_party_push_is_left_alone(self):
        self.client.post("/stats", json={"campaign": "with-milo", "replace_players": True,
                                         "players": [{"name": "Someone Else", "hp": {"current": 1, "max": 1}}]})
        self.assertEqual(self.names(), ["Someone Else"])

    def test_empty_story_screen_says_what_to_do(self):
        html = (DISPLAY / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn('<div id="story-empty">', html)
        self.assertIn("#text-content:empty + #story-empty { display: block; }", html)
        self.assertIn("if (stats && 'players' in stats) _storyEmptyHint(stats.players);", html)
        self.assertIn("No characters yet", html)
        cmds = (REPO / "skills" / "dnd" / "SKILL-commands.md").read_text(encoding="utf-8")
        self.assertIn("Read the party from disk on every load", cmds)
        self.assertIn("never wait silently on an empty screen", cmds)


if __name__ == "__main__":
    unittest.main()
