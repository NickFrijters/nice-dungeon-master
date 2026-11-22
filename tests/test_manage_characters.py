"""Manage characters: every party and the roster, view a sheet, add a roster character to a campaign, delete to a
trash folder, restore, delete for good. Only files the listing shows can be touched."""
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

SHEET = """# {name}
- **Race:** Dwarf | **Class:** Cleric | **Level:** 3
- **HP:** 20 / 27 | **Temp HP:** 0
- **AC:** 18 | **Speed:** 25 ft
"""


class ManageCharactersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = root = Path(self.tmp.name)
        (root / "campaigns").mkdir()
        self._env = {k: os.environ.get(k) for k in ("DND_CAMPAIGN_ROOT", "DND_RUNTIME_DIR")}
        os.environ["DND_CAMPAIGN_ROOT"] = str(root)
        os.environ["DND_RUNTIME_DIR"] = str(root / ".runtime")
        sys.path.insert(0, str(SCRIPTS))
        sys.path.insert(0, str(DISPLAY))
        import paths
        import runtime_paths
        (root / ".runtime").mkdir(exist_ok=True)
        self._rt_patch = mock.patch.object(runtime_paths, "RT", str(root / ".runtime"))
        self._rt_patch.start()
        self._root_patch = mock.patch.object(paths, "_root", lambda: root)
        self._root_patch.start()
        spec = importlib.util.spec_from_file_location("display_app_chars", DISPLAY / "dnd-display-app.py")
        self.mod = importlib.util.module_from_spec(spec)
        sys.modules["display_app_chars"] = self.mod
        spec.loader.exec_module(self.mod)
        self.client = self.mod.app.test_client()
        assert str(root) in self.mod.QUEUE_FILE
        for camp in ("iron-vault", "sea-hunt"):
            d = root / "campaigns" / camp
            (d / "characters").mkdir(parents=True)
            (d / "state.md").write_text("# State\n**Ruleset:** 2014\n", encoding="utf-8")
        self.write(root / "campaigns" / "iron-vault" / "characters" / "Dagna.md", "Dagna")
        (root / "characters").mkdir()
        self.write(root / "characters" / "Dagna.md", "Dagna")
        self.write(root / "characters" / "Milo.md", "Milo")

    def tearDown(self):
        self._root_patch.stop()
        self._rt_patch.stop()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def write(self, path, name):
        path.write_text(SHEET.format(name=name), encoding="utf-8")

    def lst(self):
        return self.client.get("/characters/list").get_json()

    def post(self, path, body):
        return self.client.post(path, json=body)

    def test_list_shows_parties_and_roster_with_details(self):
        d = self.lst()
        self.assertEqual([c["name"] for c in d["roster"]], ["Dagna", "Milo"])
        vault = next(c for c in d["campaigns"] if c["campaign"] == "iron-vault")
        dagna = vault["characters"][0]
        self.assertEqual((dagna["name"], dagna["level"], dagna["hp"]["max"], dagna["ac"]), ("Dagna", 3, 27, 18))
        self.assertEqual(next(c for c in d["campaigns"] if c["campaign"] == "sea-hunt")["characters"], [])

    def test_view_a_sheet(self):
        r = self.client.get("/characters/view?scope=roster&file=Milo.md")
        self.assertIn("# Milo", r.get_json()["text"])
        self.assertEqual(self.client.get("/characters/view?scope=roster&file=../secret.md").status_code, 404)
        self.assertEqual(self.client.get("/characters/view?scope=nowhere&file=Milo.md").status_code, 404)

    def test_add_a_roster_character_to_a_campaign(self):
        r = self.post("/characters/copy", {"file": "Milo.md", "campaign": "sea-hunt"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue((self.root / "campaigns" / "sea-hunt" / "characters" / "Milo.md").exists())
        self.assertTrue((self.root / "characters" / "Milo.md").exists())            # the roster keeps its copy
        self.assertEqual(self.post("/characters/copy", {"file": "Milo.md", "campaign": "sea-hunt"}).status_code, 409)
        self.assertEqual(self.post("/characters/copy", {"file": "Milo.md", "campaign": "roster"}).status_code, 404)

    def test_delete_restore_and_delete_forever(self):
        r = self.post("/characters/delete", {"scope": "iron-vault", "file": "Dagna.md"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse((self.root / "campaigns" / "iron-vault" / "characters" / "Dagna.md").exists())
        trash = self.lst()["trash"]
        self.assertEqual([(e["scope"], e["name"]) for e in trash], [("iron-vault", "Dagna")])
        r = self.post("/characters/restore", {"entry": trash[0]["entry"]})
        self.assertEqual(r.get_json(), {"scope": "iron-vault", "file": "Dagna.md"})
        self.assertTrue((self.root / "campaigns" / "iron-vault" / "characters" / "Dagna.md").exists())
        entry = self.post("/characters/delete", {"scope": "roster", "file": "Milo.md"}).get_json()["entry"]
        self.assertEqual(self.post("/characters/purge", {"entry": entry}).status_code, 204)
        self.assertEqual(self.lst()["trash"], [])

    def test_restore_when_the_name_is_taken_or_the_campaign_is_gone(self):
        entry = self.post("/characters/delete", {"scope": "roster", "file": "Dagna.md"}).get_json()["entry"]
        self.write(self.root / "characters" / "Dagna.md", "Dagna")                    # a new Dagna appeared since
        self.assertEqual(self.post("/characters/restore", {"entry": entry}).get_json()["file"], "Dagna-restored.md")
        entry = self.post("/characters/delete", {"scope": "iron-vault", "file": "Dagna.md"}).get_json()["entry"]
        import shutil
        shutil.rmtree(self.root / "campaigns" / "iron-vault")                          # the campaign itself is gone
        r = self.post("/characters/restore", {"entry": entry}).get_json()
        self.assertEqual(r["scope"], "roster")
        self.assertTrue(r["file"].startswith("Dagna"))

    def test_deleting_from_the_open_campaign_leaves_the_sidebar(self):
        Path(self.mod.CAMP_FILE).write_text("iron-vault", encoding="utf-8")
        self.client.post("/stats", json={"replace_players": True, "players": [{"name": "Dagna", "hp": {"current": 1, "max": 1}}]})
        self.post("/characters/delete", {"scope": "iron-vault", "file": "Dagna.md"})
        self.assertEqual(self.mod._current_stats.get("players"), [])

    def test_only_listed_files(self):
        self.assertEqual(self.post("/characters/delete", {"scope": "roster", "file": "../../state.md"}).status_code, 404)
        self.assertEqual(self.post("/characters/delete", {"scope": "roster", "file": "Nobody.md"}).status_code, 404)
        self.assertEqual(self.post("/characters/restore", {"entry": "x"}).status_code, 404)
        self.assertEqual(self.post("/characters/purge", {"entry": "x"}).status_code, 404)

    def test_menu_links_to_the_page(self):
        menu = (DISPLAY / "templates" / "menu.html").read_text(encoding="utf-8")
        self.assertIn("location.href='/characters'", menu)
        self.assertNotIn("Coming soon", menu)
        self.assertIn("Manage characters", self.client.get("/characters").get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
