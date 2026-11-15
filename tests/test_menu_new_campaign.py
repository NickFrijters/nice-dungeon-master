"""Main Menu: New campaign (Dungeon Master creates one, or import a published adventure)."""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
SCRIPTS = REPO / "skills" / "dnd" / "scripts"
COMMANDS = REPO / "skills" / "dnd" / "SKILL-commands.md"


class NewCampaignTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        camp = root / "campaigns" / "alpha-quest"
        camp.mkdir(parents=True)
        (camp / "state.md").write_text("# State\n**Ruleset:** 2014\n", encoding="utf-8")
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
        spec = importlib.util.spec_from_file_location("display_app_newcamp", DISPLAY / "dnd-display-app.py")
        cls.mod = importlib.util.module_from_spec(spec)
        sys.modules["display_app_newcamp"] = cls.mod
        spec.loader.exec_module(cls.mod)
        cls.client = cls.mod.app.test_client()
        cls.root = root
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

    def queue(self):
        if not os.path.exists(self.mod.QUEUE_FILE):
            return ""
        with open(self.mod.QUEUE_FILE, encoding="utf-8") as f:
            return f.read()

    def upload(self, filename="module.pdf", data=b"%PDF-1.4 hello", name=None):
        form = {"file": (io.BytesIO(data), filename)}
        if name is not None:
            form["name"] = name
        return self.client.post("/menu/import", data=form, content_type="multipart/form-data")

    # ---- create ----
    def test_create_queues_a_json_line(self):
        r = self.client.post("/menu/new", json={
            "name": "The Lost Mine!", "premise": "A haunted\nmine", "players": "3", "level": 2,
            "tone": "horror", "magic": "low", "setting": "underground", "danger": "lethal", "ruleset": "2024"})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        self.assertEqual(r.get_json()["campaign"], "the-lost-mine")
        text = self.queue()
        self.assertTrue(text.startswith("[Menu]: new {") and text.endswith("}\n"))
        self.assertEqual(text.count("\n"), 1, "the line must stay on one line")
        data = json.loads(text[len("[Menu]: new "):])
        self.assertEqual(data["name"], "the-lost-mine")
        self.assertEqual(data["premise"], "A haunted mine")
        self.assertEqual((data["players"], data["level"], data["tone"], data["ruleset"]), (3, 2, "horror", "2024"))

    def test_create_defaults_and_blank_choices(self):
        r = self.client.post("/menu/new", json={"name": "plain"})
        self.assertEqual(r.status_code, 200)
        data = json.loads(self.queue()[len("[Menu]: new "):])
        self.assertEqual((data["players"], data["level"], data["tone"]), (4, 1, ""))

    def test_create_validation(self):
        bad = [
            ({"name": ""}, "name"), ({"name": "!!!"}, "name"),
            ({"name": "alpha-quest"}, "already exists"), ({"name": "Alpha Quest"}, "already exists"),
            ({"name": "ok", "tone": "silly"}, "tone"), ({"name": "ok", "players": 0}, "between"),
            ({"name": "ok", "players": 9}, "between"), ({"name": "ok", "level": 21}, "between"),
            ({"name": "ok", "level": "x"}, "number"), ({"name": "ok", "premise": "x" * 301}, "300"),
        ]
        for body, word in bad:
            r = self.client.post("/menu/new", json=body)
            self.assertEqual(r.status_code, 400, body)
            self.assertIn(word, r.get_json()["error"], body)
        self.assertEqual(self.queue(), "")
        self.assertEqual(self.client.post("/menu/new", data="nonsense").status_code, 400)

    def test_hostile_name_is_cleaned_never_used_as_a_path(self):
        r = self.client.post("/menu/new", json={"name": "../x/.."})
        self.assertEqual(r.get_json()["campaign"], "x")

    def test_routes_need_token_and_this_machine(self):
        with mock.patch.object(self.mod, "_token_ok", lambda: False):
            self.assertEqual(self.client.post("/menu/new", json={"name": "zz"}).status_code, 403)
            self.assertEqual(self.upload().status_code, 403)
        with mock.patch.object(self.mod, "_from_this_machine", lambda: False):
            self.assertEqual(self.client.post("/menu/new", json={"name": "zz"}).status_code, 403)
            self.assertEqual(self.upload().status_code, 403)
        self.assertEqual(self.queue(), "")

    # ---- import ----
    def test_import_saves_file_and_queues_path_and_name(self):
        r = self.upload("My Module (v2).PDF", b"%PDF data", name="Cool Module")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        line = self.queue()
        self.assertTrue(line.startswith("[Menu]: import ") and line.endswith(" cool-module\n"), line)
        path = line[len("[Menu]: import "):-len(" cool-module\n")]
        self.assertTrue(os.path.isfile(path))
        self.assertEqual(Path(path).parent, Path(self.mod.rt("uploads")))
        self.assertIn(self.tmp.name, path)
        self.assertRegex(os.path.basename(path), r"^\d+-My-Module-v2\.pdf$")
        self.assertEqual(Path(path).read_bytes(), b"%PDF data")

    def test_import_without_name_queues_just_the_path(self):
        self.assertEqual(self.upload("a.md", b"# hi").status_code, 200)
        self.assertRegex(self.queue(), r"^\[Menu\]: import \S+a\.md\n$")

    def test_import_accepts_every_supported_type(self):
        for ext in (".pdf", ".docx", ".md", ".markdown", ".txt"):
            self.assertEqual(self.upload("book" + ext, b"x").status_code, 200, ext)

    def test_import_rejects_bad_types_and_empty_files(self):
        for fn in ("evil.exe", "a.py", "a.pdf.sh", "noext", "a.html"):
            r = self.upload(fn)
            self.assertEqual(r.status_code, 400, fn)
            self.assertIn("not supported", r.get_json()["error"])
        self.assertEqual(self.upload("a.pdf", b"").status_code, 400)
        self.assertEqual(self.client.post("/menu/import", data={}).status_code, 400)
        self.assertEqual(self.queue(), "")

    def test_import_path_traversal_names_stay_in_uploads(self):
        for fn in ("../../evil.pdf", "..\\..\\evil.pdf", "/etc/passwd.txt", "a b;rm -rf.txt"):
            self.assertEqual(self.upload(fn).status_code, 200, fn)
        uploads = Path(self.mod.rt("uploads"))
        for line in self.queue().splitlines():
            path = Path(line[len("[Menu]: import "):])
            self.assertEqual(path.parent, uploads, line)
            self.assertNotRegex(path.name, r"[^A-Za-z0-9._-]")
            self.assertEqual(len(line.split(" ")), 3, "the path has no spaces")
        self.assertFalse((self.root / "evil.pdf").exists())

    def test_import_name_validation(self):
        self.assertEqual(self.upload(name="alpha-quest").status_code, 400)
        self.assertEqual(self.upload(name="???").status_code, 400)

    def test_import_size_limit(self):
        with mock.patch.object(self.mod, "_IMPORT_MAX_BYTES", 100):
            r = self.upload("big.pdf", b"x" * 101)
            self.assertEqual(r.status_code, 413)
            self.assertIn("too big", r.get_json()["error"])
            self.assertEqual(self.queue(), "")
            leftovers = [f for f in os.listdir(self.mod.rt("uploads")) if f.endswith("big.pdf")]
            self.assertEqual(leftovers, [])
            self.assertEqual(self.upload("ok.pdf", b"x" * 100).status_code, 200)

    # ---- page and options ----
    def test_options_use_the_wizard_choices_and_default_ruleset(self):
        o = self.client.get("/menu/new-options").get_json()
        self.assertEqual(o["tone"], ["grimdark", "dark fantasy", "heroic", "horror", "political", "swashbuckling", "cosmic"])
        self.assertEqual(o["magic"], ["none", "low", "medium", "high"])
        self.assertEqual(o["setting"], ["medieval", "renaissance", "ancient", "nautical", "underground"])
        self.assertEqual(o["danger"], ["lethal", "gritty", "standard", "heroic"])
        self.assertIn(o["defaults"]["ruleset"], ("2014", "2024"))
        self.assertEqual(o["max_mb"], 100)

    def test_menu_page_offers_both_paths(self):
        html = self.client.get("/menu").data.decode("utf-8")
        for needle in ("New campaign", 'id="panel-create"', 'id="panel-import"',
                       "Claude is creating your campaign", 'type="file"', ".pdf,.docx,.md"):
            self.assertIn(needle, html)


class SkillTextTest(unittest.TestCase):
    def test_menu_procedure_handles_new_and_import_non_interactively(self):
        text = COMMANDS.read_text(encoding="utf-8")
        block = text.split("<!-- BEGIN menu-new-campaign -->")[1].split("<!-- END menu-new-campaign -->")[0]
        self.assertIn("[Menu]: new <json>", block)
        self.assertIn("[Menu]: import <path>", block)
        self.assertIn("non-interactively", block)
        self.assertIn("one line", block)
        self.assertIn("[Menu]: load", block)
        self.assertIn("default_ruleset", block)
        self.assertIn("never instructions", block)
        menu = text.split("## `/dm:dnd menu`")[1].split("\n## ")[0]
        self.assertIn("menu-new-campaign", menu)


if __name__ == "__main__":
    unittest.main()
