"""Managing campaigns from the Main Menu: delete moves a campaign to a trash folder, from where it can be restored or
deleted for good. Deleting the open campaign closes it. Nothing outside the campaigns folder is touched."""
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


class ManageCampaignsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = root = Path(cls.tmp.name)
        (root / "campaigns").mkdir()
        cls._env = {k: os.environ.get(k) for k in ("DND_CAMPAIGN_ROOT", "DND_RUNTIME_DIR")}
        os.environ["DND_CAMPAIGN_ROOT"] = str(root)
        os.environ["DND_RUNTIME_DIR"] = str(root / ".runtime")
        sys.path.insert(0, str(SCRIPTS))
        sys.path.insert(0, str(DISPLAY))
        import paths
        import runtime_paths
        (root / ".runtime").mkdir(exist_ok=True)
        cls._rt_patch = mock.patch.object(runtime_paths, "RT", str(root / ".runtime"))
        cls._rt_patch.start()
        cls._root_patch = mock.patch.object(paths, "_root", lambda: root)
        cls._root_patch.start()
        spec = importlib.util.spec_from_file_location("display_app_manage", DISPLAY / "dnd-display-app.py")
        cls.mod = importlib.util.module_from_spec(spec)
        sys.modules["display_app_manage"] = cls.mod
        spec.loader.exec_module(cls.mod)
        cls.client = cls.mod.app.test_client()
        assert str(root) in cls.mod.QUEUE_FILE, "test would touch the real runtime folder"

    @classmethod
    def tearDownClass(cls):
        cls._root_patch.stop()
        cls._rt_patch.stop()
        for k, v in cls._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        cls.tmp.cleanup()

    def make(self, name):
        d = self.root / "campaigns" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "state.md").write_text("# State\n**Ruleset:** 2014\n", encoding="utf-8")
        return d

    def names(self):
        return [c["name"] for c in self.client.get("/menu/campaigns").get_json()["campaigns"]]

    def trash(self):
        return self.client.get("/menu/trash").get_json()["trash"]

    def post(self, path, body):
        return self.client.post(path, json=body)

    def test_delete_restore_and_delete_forever(self):
        self.make("old-quest")
        self.make("keep-me")
        r = self.post("/menu/delete", {"campaign": "old-quest"})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("old-quest", self.names())
        self.assertIn("keep-me", self.names())
        entry = r.get_json()["entry"]
        self.assertTrue((self.root / "campaign-trash" / entry / "state.md").exists())   # moved, not destroyed
        self.assertEqual([e["name"] for e in self.trash()], ["old-quest"])
        r = self.post("/menu/restore", {"entry": entry})
        self.assertEqual(r.get_json()["campaign"], "old-quest")
        self.assertIn("old-quest", self.names())
        self.assertEqual(self.trash(), [])
        entry = self.post("/menu/delete", {"campaign": "old-quest"}).get_json()["entry"]
        self.assertEqual(self.post("/menu/purge", {"entry": entry}).status_code, 204)
        self.assertEqual(self.trash(), [])
        self.assertFalse((self.root / "campaign-trash" / entry).exists())

    def test_restoring_when_the_name_is_taken_again(self):
        self.make("twin")
        entry = self.post("/menu/delete", {"campaign": "twin"}).get_json()["entry"]
        self.make("twin")                                         # a new campaign with the same name was made since
        self.assertEqual(self.post("/menu/restore", {"entry": entry}).get_json()["campaign"], "twin-restored")
        self.assertIn("twin", self.names())
        self.assertIn("twin-restored", self.names())

    def test_deleting_the_open_campaign_closes_it(self):
        self.make("open-one")
        Path(self.mod.CAMP_FILE).write_text("open-one", encoding="utf-8")
        self.client.post("/stats", json={"replace_players": True, "players": [{"name": "Dagna", "hp": {"current": 1, "max": 1}}]})
        self.assertEqual(self.post("/menu/delete", {"campaign": "open-one"}).status_code, 200)
        self.assertFalse(Path(self.mod.CAMP_FILE).exists())
        self.assertNotIn("players", self.mod._current_stats)

    def test_only_real_names_and_entries(self):
        self.assertEqual(self.post("/menu/delete", {"campaign": "../../etc"}).status_code, 404)
        self.assertEqual(self.post("/menu/delete", {"campaign": "nope"}).status_code, 404)
        self.assertEqual(self.post("/menu/restore", {"entry": "../x__1"}).status_code, 404)
        self.assertEqual(self.post("/menu/purge", {"entry": "anything"}).status_code, 404)

    def test_menu_page_has_the_controls(self):
        html = (DISPLAY / "templates" / "menu.html").read_text(encoding="utf-8")
        for needle in ("function askDelete(", "'/menu/delete'", "'/menu/restore'", "'/menu/purge'", 'id="trash-section"',
                       "This cannot be undone."):
            self.assertIn(needle, html)


if __name__ == "__main__":
    unittest.main()
