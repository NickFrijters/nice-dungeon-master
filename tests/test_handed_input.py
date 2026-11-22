"""Input the DM's wait handed over is never lost: if the DM does not answer it on screen (a wait that ran in the
background), the next wait gives it back once. An answer on screen clears it; the Main Menu's own pushes do not."""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
SCRIPTS = REPO / "skills" / "dnd" / "scripts"


def run_wait(rt: Path, seconds_before_queue=None, line=None):
    """Run autorun_wait.py against a runtime folder; optionally drop a queue line first. Returns its stdout."""
    env = dict(os.environ, DND_RUNTIME_DIR=str(rt), DND_CAMPAIGN_ROOT=str(rt.parent), DND_DISPLAY_PORT="5999", PYTHONUTF8="1")
    if line:
        (rt / ".input_queue").write_text(line, encoding="utf-8")
    p = subprocess.run([sys.executable, str(DISPLAY / "autorun_wait.py")], env=env, capture_output=True, text=True, timeout=60)
    return p.stdout


class HandedInputTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.rt = self.root / ".runtime"
        self.rt.mkdir()
        (self.root / "campaigns").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_unanswered_input_comes_back_once(self):
        out = run_wait(self.rt, line="[Menu]: load my-campaign\n")
        self.assertIn("[Menu]: load my-campaign", out)
        handed = json.loads((self.rt / ".input_handed").read_text(encoding="utf-8"))
        self.assertIn("load my-campaign", handed["content"])
        # the DM never answered (its wait ran in the background): the next wait gives it back at once
        out = run_wait(self.rt)
        self.assertIn("[Menu]: load my-campaign", out)
        self.assertIn("handed to you before", out)
        # ...but only once: a third wait does not hand it over again (it would wait; here a new line ends it)
        out = run_wait(self.rt, line="[Dagna]: I attack\n")
        self.assertNotIn("load my-campaign", out)
        self.assertIn("[Dagna]: I attack", out)

    def test_old_handed_input_is_not_given_back(self):
        (self.rt / ".input_handed").write_text(json.dumps({"content": "[Menu]: load old\n", "ts": 1}), encoding="utf-8")
        out = run_wait(self.rt, line="[Dagna]: hello\n")
        self.assertNotIn("load old", out)


class DisplayClearsItTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / "campaigns" / "camp").mkdir(parents=True)
        (root / "campaigns" / "camp" / "state.md").write_text("# State\n", encoding="utf-8")
        cls._env = {k: os.environ.get(k) for k in ("DND_CAMPAIGN_ROOT", "DND_RUNTIME_DIR")}
        os.environ["DND_CAMPAIGN_ROOT"] = str(root)
        os.environ["DND_RUNTIME_DIR"] = str(root / ".runtime")
        sys.path.insert(0, str(SCRIPTS)); sys.path.insert(0, str(DISPLAY))
        import paths
        import runtime_paths
        (root / ".runtime").mkdir(exist_ok=True)
        cls.rt = root / ".runtime"
        cls._rt_patch = mock.patch.object(runtime_paths, "RT", str(cls.rt)); cls._rt_patch.start()
        cls._root_patch = mock.patch.object(paths, "_root", lambda: root); cls._root_patch.start()
        spec = importlib.util.spec_from_file_location("display_app_handed", DISPLAY / "dnd-display-app.py")
        cls.mod = importlib.util.module_from_spec(spec)
        sys.modules["display_app_handed"] = cls.mod
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

    def handed(self):
        (self.rt / ".input_handed").write_text(json.dumps({"content": "[Menu]: load camp\n", "ts": 9e9}), encoding="utf-8")

    def test_a_dm_answer_clears_it_but_the_menu_and_the_wait_do_not(self):
        self.handed()
        self.mod._open_campaign_here("camp")                                    # the Main Menu's own push
        self.client.post("/stats", json={"autorun_waiting": True, "autorun_cycle": {"interval": 60, "ts": 1}})
        self.assertTrue((self.rt / ".input_handed").exists())
        self.client.post("/stats", json={"replace_players": True, "players": [{"name": "Dagna", "hp": {"current": 1, "max": 1}}]})
        self.assertFalse((self.rt / ".input_handed").exists())                 # the DM's load push
        self.handed()
        self.client.post("/chunk", json={"text": "The rain hammers the road."})
        self.assertFalse((self.rt / ".input_handed").exists())                 # any DM block

    def test_dm_rule(self):
        self.assertIn("never in the background", (REPO / "skills" / "dnd" / "SKILL.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
