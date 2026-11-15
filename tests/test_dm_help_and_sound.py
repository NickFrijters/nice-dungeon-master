"""DM Help always answers, and narration never waits on sound the browser has locked."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
SCRIPTS = REPO / "skills" / "dnd" / "scripts"
HTML = DISPLAY / "templates" / "index.html"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(DISPLAY))

import paths  # noqa: E402


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, DISPLAY / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class DmHelpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / ".runtime").mkdir()
        cls._env = {k: os.environ.get(k) for k in ("DND_CAMPAIGN_ROOT", "DND_RUNTIME_DIR")}
        os.environ["DND_CAMPAIGN_ROOT"] = str(root)
        os.environ["DND_RUNTIME_DIR"] = str(root / ".runtime")
        cls.mod = _load("dm_help_under_test", "dm_help.py")

    @classmethod
    def tearDownClass(cls):
        for k, v in cls._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        cls.tmp.cleanup()

    def test_it_runs_the_real_claude_file_and_sends_the_question_on_stdin(self):
        done = subprocess.CompletedProcess(args=[], returncode=0, stdout="A hint.\n", stderr="")
        with mock.patch.object(self.mod.shutil, "which", return_value=r"C:\npm\claude.CMD"), \
             mock.patch.object(self.mod.subprocess, "run", return_value=done) as run:
            out = self.mod.call_claude("scene", "state", "session", "", "")
        self.assertEqual(out, "A hint.")
        cmd, kwargs = run.call_args.args[0], run.call_args.kwargs
        self.assertEqual(cmd[0], r"C:\npm\claude.CMD")             # not the bare name Windows cannot start
        self.assertIn("--system-prompt-file", cmd)
        self.assertIn("scene", kwargs["input"])                    # the long text is not on the command line
        self.assertFalse(any("RECENT SCENE" in part for part in cmd))

    def test_a_missing_claude_command_is_a_readable_reason(self):
        with mock.patch.object(self.mod.shutil, "which", return_value=None):
            with self.assertRaises(self.mod.HintError) as e:
                self.mod.call_claude("scene", "", "", "", "")
        self.assertIn("not found", str(e.exception))

    def test_claude_failing_or_timing_out_is_a_readable_reason(self):
        bad = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="no auth")
        with mock.patch.object(self.mod.shutil, "which", return_value="claude"), \
             mock.patch.object(self.mod.subprocess, "run", return_value=bad):
            with self.assertRaises(self.mod.HintError) as e:
                self.mod.call_claude("scene", "", "", "", "")
        self.assertIn("no auth", str(e.exception))
        with mock.patch.object(self.mod.shutil, "which", return_value="claude"), \
             mock.patch.object(self.mod.subprocess, "run", side_effect=subprocess.TimeoutExpired("claude", 90)):
            with self.assertRaises(self.mod.HintError) as e:
                self.mod.call_claude("scene", "", "", "", "")
        self.assertIn("too long", str(e.exception))

    def _main(self, **patches):
        sent = []
        base = dict(get_recent_display=lambda n: "scene", get_campaign_state=lambda c: "state",
                    get_session_context=lambda c: "", get_arc_context=lambda c: "",
                    get_graph_context=lambda c: "", send_tutor=sent.append, release_lock=mock.Mock())
        base.update(patches)
        with mock.patch.multiple(self.mod, **base), mock.patch.object(sys, "argv", ["dm_help.py", "--campaign", "demo"]):
            self.mod.main()
        return sent, base["release_lock"]

    def test_a_hint_is_shown(self):
        sent, release = self._main(call_claude=lambda *a: "Look at the maps.")
        self.assertEqual(sent, ["Look at the maps."])
        release.assert_called_once()

    def test_nothing_to_add_is_said_out_loud(self):
        sent, _ = self._main(call_claude=lambda *a: "SKIP")
        self.assertEqual(len(sent), 1)
        self.assertIn("Nothing to add", sent[0])

    def test_a_failure_is_shown_with_its_reason_and_the_lock_is_still_released(self):
        def boom(*a):
            raise self.mod.HintError("it took too long")
        sent, release = self._main(call_claude=boom)
        self.assertIn("could not come up with a hint (it took too long)", sent[0])
        release.assert_called_once()

    def test_an_unexpected_error_is_shown_too(self):
        def boom(*a):
            raise RuntimeError("surprise")
        sent, release = self._main(call_claude=boom)
        self.assertIn("ran into a problem", sent[0])
        release.assert_called_once()

    def test_no_context_yet_is_explained(self):
        sent, _ = self._main(get_recent_display=lambda n: "", get_campaign_state=lambda c: "",
                             call_claude=lambda *a: "never called")
        self.assertIn("nothing to go on yet", sent[0].lower())


class HelpRouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / "campaigns").mkdir()
        (root / ".runtime").mkdir()
        cls._env = {k: os.environ.get(k) for k in ("DND_CAMPAIGN_ROOT", "DND_RUNTIME_DIR")}
        os.environ["DND_CAMPAIGN_ROOT"] = str(root)
        os.environ["DND_RUNTIME_DIR"] = str(root / ".runtime")
        import runtime_paths
        cls._patches = [mock.patch.object(paths, "_root", lambda: root),
                        mock.patch.object(runtime_paths, "RT", str(root / ".runtime"))]
        for p in cls._patches:
            p.start()
        cls.mod = _load("display_app_help", "dnd-display-app.py")
        cls.client = cls.mod.app.test_client()
        assert str(root) in cls.mod.HELP_LOCK, "test would touch the real runtime folder"
        Path(cls.mod.CAMP_FILE).write_text("demo", encoding="utf-8")

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
        if os.path.exists(self.mod.HELP_LOCK):
            os.remove(self.mod.HELP_LOCK)

    def test_a_request_starts_the_helper(self):
        with mock.patch.object(self.mod.subprocess, "Popen") as popen:
            r = self.client.post("/help-request")
        self.assertEqual(r.status_code, 202)
        self.assertIn("dm_help.py", popen.call_args.args[0][1])

    def test_a_second_click_while_one_is_running_is_told_so(self):
        Path(self.mod.HELP_LOCK).write_text("", encoding="utf-8")
        self.assertEqual(self.client.post("/help-request").status_code, 409)

    def test_a_lock_left_by_a_killed_helper_does_not_block_for_ever(self):
        Path(self.mod.HELP_LOCK).write_text("", encoding="utf-8")
        old = time.time() - 600
        os.utime(self.mod.HELP_LOCK, (old, old))
        with mock.patch.object(self.mod.subprocess, "Popen"):
            self.assertEqual(self.client.post("/help-request").status_code, 202)

    def test_a_helper_that_cannot_start_does_not_leak_the_lock(self):
        with mock.patch.object(self.mod.subprocess, "Popen", side_effect=OSError("no")):
            self.assertEqual(self.client.post("/help-request").status_code, 500)
        self.assertFalse(os.path.exists(self.mod.HELP_LOCK))


class PageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = HTML.read_text(encoding="utf-8")

    def test_the_help_button_never_resets_silently(self):
        self.assertIn("function _dmHelpTimeout()", self.html)
        self.assertIn("No hint came back. Try DM Help again.", self.html)
        self.assertNotIn("setTimeout(_dmHelpReset, 20000)", self.html)

    def test_locked_sound_releases_the_text_asks_for_a_click_and_plays_afterwards(self):
        self.assertIn("function _ttsSoundLocked(", self.html)
        self.assertIn("await Promise.race([resumeP, new Promise(r => setTimeout(r, 600))]);", self.html)
        self.assertIn("if (ctx.state !== 'running') { _ttsSoundLocked(btn, blockEl, text); return; }", self.html)
        body = self.html[self.html.index("function _ttsSoundLocked("):]
        body = body[:body.index("async function _ttsPlayBlock(")]
        for needle in ("blockEl._syncFail()", "id = 'sound-lock'", "pointerdown", "_ttsPlayBlock(bar ||"):
            self.assertIn(needle, body)

    def test_the_helper_scripts_can_follow_a_second_display_port(self):
        for name in ("send.py", "push_stats.py", "autorun_wait.py"):
            self.assertIn("DND_DISPLAY_PORT", (DISPLAY / name).read_text(encoding="utf-8"), name)


if __name__ == "__main__":
    unittest.main()
