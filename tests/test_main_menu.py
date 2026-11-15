"""Main Menu: the web app lists campaigns and asks Claude to load one."""

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


class MainMenuTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        for name, ruleset in (("alpha-quest", "2014"), ("beta-saga", "2024")):
            camp = root / "campaigns" / name
            camp.mkdir(parents=True)
            (camp / "state.md").write_text(f"# State\n**Ruleset:** {ruleset}\n", encoding="utf-8")
        (root / "campaigns" / ".hidden").mkdir()
        (root / "campaigns" / "alpha-quest.backup-20261114-195106").mkdir()
        cls._env = {k: os.environ.get(k) for k in ("DND_CAMPAIGN_ROOT", "DND_RUNTIME_DIR")}
        os.environ["DND_CAMPAIGN_ROOT"] = str(root)
        os.environ["DND_RUNTIME_DIR"] = str(root / ".runtime")
        sys.path.insert(0, str(SCRIPTS))
        sys.path.insert(0, str(DISPLAY))
        import paths  # may already be imported by another test with another root
        import runtime_paths  # remembers its folder from first import: pin it to the temp dir
        (root / ".runtime").mkdir(exist_ok=True)
        cls._rt_patch = mock.patch.object(runtime_paths, "RT", str(root / ".runtime"))
        cls._rt_patch.start()
        cls._root_patch = mock.patch.object(paths, "_root", lambda: root)
        cls._root_patch.start()
        spec = importlib.util.spec_from_file_location("display_app_menu", DISPLAY / "dnd-display-app.py")
        cls.mod = importlib.util.module_from_spec(spec)
        sys.modules["display_app_menu"] = cls.mod
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

    def setUp(self):
        if os.path.exists(self.mod.QUEUE_FILE):
            os.remove(self.mod.QUEUE_FILE)

    def test_root_opens_menu_first_until_a_campaign_is_requested(self):
        self.mod._MENU_FIRST = True
        r = self.client.get("/")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/menu"))
        self.client.post("/menu/load", json={"campaign": "alpha-quest"})
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_menu_page_renders(self):
        r = self.client.get("/menu")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Nice Dungeon Master", r.data)

    def test_lists_campaigns_with_ruleset_and_skips_hidden(self):
        data = self.client.get("/menu/campaigns").get_json()
        names = {c["name"]: c["ruleset"] for c in data["campaigns"]}
        # Other code in the suite may copy a real campaign into the temp root,
        # so check ours are present rather than that they are the only ones.
        self.assertEqual(names["alpha-quest"], "2014")
        self.assertEqual(names["beta-saga"], "2024")
        self.assertNotIn(".hidden", names)
        self.assertFalse([n for n in names if ".backup-" in n])

    def test_load_queues_a_menu_line_for_claude(self):
        r = self.client.post("/menu/load", json={"campaign": "alpha-quest"})
        self.assertEqual(r.status_code, 204)
        with open(self.mod.QUEUE_FILE, encoding="utf-8") as f:
            self.assertEqual(f.read(), "[Menu]: load alpha-quest\n")

    def test_load_keeps_player_actions_already_queued(self):
        with open(self.mod.QUEUE_FILE, "w", encoding="utf-8") as f:
            f.write("[Mira]: I open the door\n")
        self.client.post("/menu/load", json={"campaign": "beta-saga"})
        with open(self.mod.QUEUE_FILE, encoding="utf-8") as f:
            self.assertEqual(f.read(),
                             "[Mira]: I open the door\n[Menu]: load beta-saga\n")

    def test_load_rejects_unknown_or_hostile_names(self):
        for bad in ("nope", "../alpha-quest", "alpha-quest; rm -rf ~", "", ".hidden"):
            r = self.client.post("/menu/load", json={"campaign": bad})
            self.assertEqual(r.status_code, 404, bad)
        self.assertFalse(os.path.exists(self.mod.QUEUE_FILE))


if __name__ == "__main__":
    unittest.main()
