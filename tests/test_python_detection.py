"""Windows ships a "python3" placeholder that fails; the skill must cope."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "dnd" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import install_autosave_hook as hook  # noqa: E402


class HookPythonNameTest(unittest.TestCase):
    def test_uses_python3_when_it_runs(self):
        ok = subprocess.CompletedProcess(args=[], returncode=0)
        with mock.patch.object(hook.subprocess, "run", return_value=ok):
            self.assertEqual(hook._python_name(), "python3")

    def test_falls_back_to_this_interpreter_when_python3_is_a_stub(self):
        stub = subprocess.CompletedProcess(args=[], returncode=9009)
        with mock.patch.object(hook.subprocess, "run", return_value=stub):
            self.assertEqual(hook._python_name(), f'"{sys.executable}"')

    def test_falls_back_when_python3_is_missing(self):
        with mock.patch.object(hook.subprocess, "run", side_effect=FileNotFoundError):
            self.assertEqual(hook._python_name(), f'"{sys.executable}"')


class LauncherScriptTest(unittest.TestCase):
    def test_start_display_picks_python_by_testing_it(self):
        text = (REPO / "skills" / "dnd" / "display" / "start-display.sh").read_text(encoding="utf-8")
        self.assertIn("for cand in python3 python py", text)
        self.assertNotIn('nohup python3', text)
        self.assertIn('nohup "$PY"', text)


if __name__ == "__main__":
    unittest.main()
