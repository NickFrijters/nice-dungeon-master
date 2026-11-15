"""LAN mode is the default, and only the game's own computer can control it."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
SCRIPTS = REPO / "skills" / "dnd" / "scripts"
SKILL = REPO / "skills" / "dnd"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(DISPLAY))


class LauncherTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sh = (DISPLAY / "start-display.sh").read_text(encoding="utf-8")

    def test_lan_is_the_default_and_local_opts_out(self):
        self.assertIn('LAN_FLAG="--lan"', self.sh)
        self.assertIn('--local) LAN_FLAG="" ;;', self.sh)

    def test_old_callers_that_pass_lan_still_work(self):
        self.assertIn('--lan) LAN_FLAG="--lan"', self.sh)

    def test_the_address_is_found_with_python_first(self):
        i_py = self.sh.index("s.connect(('8.8.8.8',80))")
        self.assertLess(i_py, self.sh.index("ipconfig getifaddr en0"))
        self.assertIn("On the TV / other devices open", self.sh)

    @unittest.skipUnless(shutil.which("bash"), "bash not available")
    def test_the_script_parses(self):
        r = subprocess.run(["bash", "-n", str(DISPLAY / "start-display.sh")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_the_skill_no_longer_asks_about_the_display(self):
        for name in ("SKILL-commands.md", "SKILL.md"):
            text = (SKILL / name).read_text(encoding="utf-8")
            self.assertNotIn("Display & input mode", text, name)
            self.assertNotIn("`Display (local)`", text, name)


class OnlyThisComputerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        camp = root / "campaigns" / "demo" / "characters"
        camp.mkdir(parents=True)
        (camp / "Mira.md").write_text("# Mira", encoding="utf-8")
        (root / "campaigns" / "demo" / "state.md").write_text("**Ruleset:** 2014\n", encoding="utf-8")
        (root / ".runtime").mkdir()
        cls._env = {k: os.environ.get(k) for k in ("DND_CAMPAIGN_ROOT", "DND_RUNTIME_DIR")}
        os.environ["DND_CAMPAIGN_ROOT"] = str(root)
        os.environ["DND_RUNTIME_DIR"] = str(root / ".runtime")
        import paths
        import runtime_paths  # remembers its folder from first import: pin both
        cls._patches = [mock.patch.object(paths, "_root", lambda: root),
                        mock.patch.object(runtime_paths, "RT", str(root / ".runtime"))]
        for p in cls._patches:
            p.start()
        spec = importlib.util.spec_from_file_location("display_app_lan", DISPLAY / "dnd-display-app.py")
        cls.mod = importlib.util.module_from_spec(spec)
        sys.modules["display_app_lan"] = cls.mod
        spec.loader.exec_module(cls.mod)
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

    def setUp(self):
        if os.path.exists(self.mod.QUEUE_FILE):
            os.remove(self.mod.QUEUE_FILE)

    OTHER = {"REMOTE_ADDR": "192.168.77.23"}

    def test_another_device_cannot_load_quit_or_change_settings(self):
        stopped = mock.Mock()
        with mock.patch.object(self.mod, "_QUIT_DELAY", 0), \
             mock.patch.object(self.mod, "_exit_display", stopped):
            calls = [
                ("/menu/load", {"campaign": "demo"}),
                ("/menu/quit", {}),
                ("/settings/save", {"flavour": "high"}),
                ("/settings/experience", {"campaign": "demo", "character": "Mira", "experience": "New"}),
            ]
            for url, body in calls:
                r = self.client.post(url, json=body, environ_overrides=self.OTHER)
                self.assertEqual(r.status_code, 403, url)
        stopped.assert_not_called()
        self.assertFalse(os.path.exists(self.mod.QUEUE_FILE))

    def test_the_computer_itself_is_allowed_on_loopback_and_on_its_own_address(self):
        own = sorted(self.mod._local_addresses() - {"127.0.0.1", "::1", "localhost"})
        for addr in ["127.0.0.1"] + own:
            r = self.client.post("/menu/load", json={"campaign": "demo"},
                                 environ_overrides={"REMOTE_ADDR": addr})
            self.assertEqual(r.status_code, 204, addr)

    def test_other_devices_can_still_read_the_pages_and_the_data(self):
        for url in ("/menu", "/menu/campaigns", "/settings", "/settings/data"):
            r = self.client.get(url, environ_overrides=self.OTHER)
            self.assertEqual(r.status_code, 200, url)


if __name__ == "__main__":
    unittest.main()
