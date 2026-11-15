"""Dutch is an addition to the English, never a replacement.

Regression for: a campaign carrying `language: nl` and a "narration stays
Dutch" style note made the whole game Dutch, when the Settings menu's
"Explain in Dutch" is meant to add a short Dutch summary to English play.
"""

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
SKILL = REPO / "skills" / "dnd"
DISPLAY = SKILL / "display"
SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(DISPLAY))

import paths  # noqa: E402
import settings  # noqa: E402


class SkillTextTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        cls.cmds = (SKILL / "SKILL-commands.md").read_text(encoding="utf-8")

    def test_english_is_the_one_fixed_language(self):
        self.assertIn("**Language is always English.**", self.skill)
        self.assertIn("never a replacement", self.skill)

    def test_old_campaign_files_cannot_override_it(self):
        # the rule names each way an old campaign could still ask for Dutch
        for needle in ("`language: nl`", "`## Glossary`", "`## DM Style Notes`", "hybrid language"):
            self.assertIn(needle, self.skill)
        self.assertIn("wins over anything older in a campaign file", self.skill)

    def test_a_new_campaign_is_no_longer_asked_for_a_language(self):
        self.assertNotIn('"Language?"', self.cmds)
        self.assertNotIn("Nederlands (Dutch)", self.cmds)
        self.assertNotIn("Hybrid language (Nick's rule", self.cmds)

    def test_loading_cleans_up_the_old_language_mode(self):
        i = self.cmds.index("2b. **Language check.**")
        step = self.cmds[i:self.cmds.index("\n", i)]
        for needle in ("delete the `language:` line", "replace the style note",
                       "English narration", "recap in English"):
            self.assertIn(needle, step)
        self.assertNotIn("run the whole session in that language", self.cmds)

    def test_no_instruction_says_to_write_in_another_language(self):
        for text in (self.skill, self.cmds):
            self.assertNotIn("run the whole session in that language", text)
            self.assertNotIn("ALL player-facing campaign content", text)
            self.assertNotIn("Narration, NPC speech and explanations stay Dutch", text)


@unittest.skipUnless((SKILL / "data" / "dnd5e_srd.json").exists(), "SRD dataset not built")
class PopupLookupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        camp = root / "campaigns" / "demo"
        camp.mkdir(parents=True)
        # the old language mode, as an existing campaign would still carry it
        (camp / "state.md").write_text("# Campaign: demo\n**Ruleset:** 2014\n\n## Session Flags\nlanguage: nl\n",
                                       encoding="utf-8")
        (root / ".runtime").mkdir()
        (root / ".runtime" / ".campaign").write_text("demo", encoding="utf-8")
        nl = {"fireball": {"cat": "spell", "meta": ["Type: Spreuk"], "desc": "Een knallende vuurbal."}}
        cls.nl_file = root / "srd_nl.json"
        cls.nl_file.write_text(json.dumps(nl), encoding="utf-8")
        cls._env = {k: os.environ.get(k) for k in ("DND_CAMPAIGN_ROOT", "DND_RUNTIME_DIR")}
        os.environ["DND_CAMPAIGN_ROOT"] = str(root)
        os.environ["DND_RUNTIME_DIR"] = str(root / ".runtime")
        import runtime_paths  # remembers its folder from first import: pin both
        cls._patches = [mock.patch.object(paths, "_root", lambda: root),
                        mock.patch.object(runtime_paths, "RT", str(root / ".runtime"))]
        for p in cls._patches:
            p.start()
        spec = importlib.util.spec_from_file_location("display_app_dutch", DISPLAY / "dnd-display-app.py")
        cls.mod = importlib.util.module_from_spec(spec)
        sys.modules["display_app_dutch"] = cls.mod
        spec.loader.exec_module(cls.mod)
        cls.mod._NL_FILE = str(cls.nl_file)
        cls.mod._nl_cache.update(mtime=None, data={})
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

    def _lookup(self):
        r = self.client.get("/srd-lookup?name=fireball&category=spell")
        self.assertEqual(r.status_code, 200)
        return r.get_json()["text"]

    def test_a_campaign_with_language_nl_no_longer_turns_the_popup_dutch(self):
        settings.save({"explain_dutch": False})
        text = self._lookup()
        self.assertNotIn("Nederlands", text)
        self.assertNotIn("vuurbal", text)
        self.assertNotIn("Nog geen Nederlandse uitleg", text)
        self.assertIn("Fireball", text)

    def test_explain_in_dutch_adds_dutch_under_the_english(self):
        settings.save({"explain_dutch": True})
        text = self._lookup()
        self.assertIn("Fireball", text)
        self.assertIn("## Nederlands", text)
        self.assertIn("Een knallende vuurbal.", text)
        self.assertLess(text.index("Fireball"), text.index("## Nederlands"))   # English first

    def test_without_a_dutch_entry_it_is_just_the_english(self):
        settings.save({"explain_dutch": True})
        r = self.client.get("/srd-lookup?name=Bless&category=spell")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("Nederlands", r.get_json()["text"])


if __name__ == "__main__":
    unittest.main()
