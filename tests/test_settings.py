"""Main Menu settings: storage, validation, what Claude is told, and the routes."""

from __future__ import annotations

import importlib.util
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
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(DISPLAY))

import paths  # noqa: E402
import settings  # noqa: E402


class SettingsCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patch = mock.patch.object(paths, "_root", lambda: self.root)
        self.patch.start()
        camp = self.root / "campaigns" / "demo" / "characters"
        camp.mkdir(parents=True)
        for n in ("Mira", "Joris"):
            (camp / f"{n}.md").write_text("# " + n, encoding="utf-8")

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()


class StorageTest(SettingsCase):
    def test_defaults_when_nothing_saved(self):
        s = settings.load()
        self.assertEqual((s["flavour"], s["combat"]), ("medium", "medium"))
        self.assertFalse(s["explain_dutch"])

    def test_save_and_reload(self):
        settings.save({"flavour": "high", "explain_dutch": True})
        s = settings.load()
        self.assertEqual(s["flavour"], "high")
        self.assertTrue(s["explain_dutch"])
        self.assertEqual(s["combat"], "medium")

    def test_rejects_unknown_keys_and_bad_values(self):
        for bad in ({"nope": 1}, {"flavour": "huge"}, {"explain_dutch": "yes"},
                    {"default_ruleset": "2019"}, {"combat": None}):
            with self.assertRaises(ValueError, msg=str(bad)):
                settings.save(bad)
        self.assertFalse(settings.settings_path().exists())

    def test_corrupt_file_falls_back_to_defaults(self):
        settings.settings_path().write_text("{not json", encoding="utf-8")
        self.assertEqual(settings.load(), settings.DEFAULTS)

    def test_bad_saved_value_is_ignored_not_trusted(self):
        settings.settings_path().write_text(json.dumps({"flavour": "enormous", "combat": "high"}),
                                            encoding="utf-8")
        s = settings.load()
        self.assertEqual(s["flavour"], "medium")
        self.assertEqual(s["combat"], "high")


class ExperienceTest(SettingsCase):
    def test_everyone_starts_experienced(self):
        self.assertEqual(settings.load_experience("demo"),
                         {"Joris": "Experienced", "Mira": "Experienced"})

    def test_save_experience(self):
        settings.save_experience("demo", "Mira", "New")
        self.assertEqual(settings.load_experience("demo")["Mira"], "New")
        self.assertEqual(settings.load_experience("demo")["Joris"], "Experienced")

    def test_rejects_unknown_character_or_level(self):
        with self.assertRaises(ValueError):
            settings.save_experience("demo", "Nobody", "New")
        with self.assertRaises(ValueError):
            settings.save_experience("demo", "Mira", "Expert")


class DirectiveTest(SettingsCase):
    def _text(self, campaign=None):
        return "\n".join(settings.directive_lines(campaign))

    def test_word_limits_follow_flavour(self):
        for level, words in (("low", 100), ("medium", 250), ("high", 500)):
            settings.save({"flavour": level})
            self.assertIn(f"at most {words} words", self._text())

    def test_dutch_line_only_when_on(self):
        self.assertNotIn("Dutch", self._text())
        settings.save({"explain_dutch": True})
        self.assertIn("Dutch", self._text())
        self.assertIn("Never shorten or replace the English", self._text())

    def test_combat_none_is_spelled_out(self):
        settings.save({"combat": "none"})
        self.assertIn("no combat encounters", self._text())

    def test_experience_line_names_new_players_only_when_someone_is_new(self):
        self.assertNotIn("Experience", self._text("demo"))
        settings.save_experience("demo", "Mira", "New")
        text = self._text("demo")
        self.assertIn("Experience New (Mira)", text)
        self.assertIn("Experience Experienced (Joris)", text)

    def test_directives_are_safe_for_any_console(self):
        settings.save({"explain_dutch": True})
        settings.save_experience("demo", "Mira", "New")
        self._text("demo").encode("ascii")


class RouteTest(SettingsCase):
    def setUp(self):
        super().setUp()
        self._env = {k: os.environ.get(k) for k in ("DND_CAMPAIGN_ROOT", "DND_RUNTIME_DIR")}
        os.environ["DND_CAMPAIGN_ROOT"] = str(self.root)
        os.environ["DND_RUNTIME_DIR"] = str(self.root / ".runtime")
        (self.root / "campaigns" / "demo" / "state.md").write_text("**Ruleset:** 2014\n", encoding="utf-8")
        import runtime_paths  # remembers its folder from first import: pin it to the temp dir
        (self.root / ".runtime").mkdir(exist_ok=True)
        self._rt_patch = mock.patch.object(runtime_paths, "RT", str(self.root / ".runtime"))
        self._rt_patch.start()
        spec = importlib.util.spec_from_file_location("display_app_settings", DISPLAY / "dnd-display-app.py")
        self.mod = importlib.util.module_from_spec(spec)
        sys.modules["display_app_settings"] = self.mod
        spec.loader.exec_module(self.mod)
        self.client = self.mod.app.test_client()
        assert str(self.root) in self.mod.rt("narration_target"), "test would touch the real runtime folder"

    def tearDown(self):
        self._rt_patch.stop()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        super().tearDown()

    def test_page_and_data(self):
        self.assertEqual(self.client.get("/settings").status_code, 200)
        d = self.client.get("/settings/data").get_json()
        self.assertEqual(d["settings"]["flavour"], "medium")
        demo = [c for c in d["campaigns"] if c["name"] == "demo"][0]
        self.assertEqual({c["name"] for c in demo["characters"]}, {"Mira", "Joris"})

    def test_save_updates_settings_and_narration_target(self):
        r = self.client.post("/settings/save", json={"flavour": "low"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(settings.load()["flavour"], "low")
        self.assertEqual(Path(self.mod.rt("narration_target")).read_text(encoding="utf-8"), "100")

    def test_save_rejects_garbage(self):
        self.assertEqual(self.client.post("/settings/save", json={"flavour": "huge"}).status_code, 400)
        self.assertEqual(self.client.post("/settings/save", json={"x": 1}).status_code, 400)

    def test_experience_route(self):
        r = self.client.post("/settings/experience",
                             json={"campaign": "demo", "character": "Mira", "experience": "New"})
        self.assertEqual(r.status_code, 204)
        self.assertEqual(settings.load_experience("demo")["Mira"], "New")
        bad = self.client.post("/settings/experience",
                               json={"campaign": "../demo", "character": "Mira", "experience": "New"})
        self.assertEqual(bad.status_code, 404)


if __name__ == "__main__":
    unittest.main()
