"""DM speed: scripts reach the display without the ~2 s Windows 'localhost' delay, the autorun hand-off is quick and
never loses a turn, a whole turn goes out in one turn.py call, and the display times every turn."""
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "skills" / "dnd" / "display"
S = ROOT / "skills" / "dnd" / "scripts"


def read(p):
    return Path(p).read_text(encoding="utf-8")


class NoLocalhostDelayTest(unittest.TestCase):
    def test_scripts_talk_to_127_0_0_1(self):
        for f in (D / "send.py", D / "push_stats.py", D / "autorun_wait.py", S / "dice.py", S / "dice_player.py"):
            src = read(f)
            self.assertNotIn("://localhost", src, f.name)
            self.assertIn("127.0.0.1", src, f.name)
        self.assertIn('host = "0.0.0.0" if _LAN_MODE else "127.0.0.1"', read(D / "dnd-display-app.py"))


class AutorunHandoffTest(unittest.TestCase):
    def test_quick_poll_no_extra_processes_and_retry_on_a_busy_file(self):
        src = read(D / "autorun_wait.py")
        self.assertIn("time.sleep(0.1)", src)
        self.assertNotIn("subprocess.run(", src)                    # no push_stats.py process before handing over
        self.assertIn('_post("/stats", {"autorun_waiting": False})', src)
        i = src.index("except Exception:", src.index("os.replace(QFILE, _taken)"))
        self.assertIn("continue", src[i:i + 300])                   # a file still being written is tried again


class TurnClockTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / "campaigns").mkdir()
        cls._env = {k: os.environ.get(k) for k in ("DND_CAMPAIGN_ROOT", "DND_RUNTIME_DIR")}
        os.environ["DND_CAMPAIGN_ROOT"] = str(root)
        os.environ["DND_RUNTIME_DIR"] = str(root / ".runtime")
        sys.path.insert(0, str(S))
        sys.path.insert(0, str(D))
        import paths
        import runtime_paths
        (root / ".runtime").mkdir(exist_ok=True)
        cls._rt_patch = mock.patch.object(runtime_paths, "RT", str(root / ".runtime"))
        cls._rt_patch.start()
        cls._root_patch = mock.patch.object(paths, "_root", lambda: root)
        cls._root_patch.start()
        spec = importlib.util.spec_from_file_location("display_app_speed", D / "dnd-display-app.py")
        cls.mod = importlib.util.module_from_spec(spec)
        sys.modules["display_app_speed"] = cls.mod
        spec.loader.exec_module(cls.mod)
        cls.client = cls.mod.app.test_client()
        assert str(root) in cls.mod.QUEUE_FILE

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

    def test_go_to_answer_is_timed_and_logged(self):
        c = self.client
        c.post("/stats", {} or None, json={"replace_players": True, "players": [{"name": "Dagna", "hp": {"current": 5, "max": 5}}]})
        r = c.post("/party-input", json={"entries": [{"character": "Dagna", "text": "I attack"}]}, headers={"X-DND-Device": "t"})
        self.assertLess(r.status_code, 300, r.data)
        c.post("/chunk", json={"text": "Dagna attacks.", "player": "Dagna"})      # first block: the echo
        c.post("/chunk", json={"text": "d20+5 = 17", "dice": True})
        c.post("/chunk", json={"text": "The blade bites."})                       # the narration ends the clock
        c.post("/chunk", json={"text": "More narration later."})                  # not a new turn
        rows = [json.loads(l) for l in Path(self.mod.LATENCY_FILE).read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertLessEqual(rows[0]["first"], rows[0]["reply"])
        self.assertLess(rows[0]["reply"], 5)
        import latency
        self.assertIn("within 5 s: 1/1", latency.report(latency.load(self.mod.LATENCY_FILE)))

    def test_page_shows_the_answer_time(self):
        html = read(D / "templates" / "index.html")
        self.assertIn("if (payload.dm_latency) _showDmLatency(payload.dm_latency);", html)
        self.assertIn('id="dm-latency"', html)


class TurnScriptTest(unittest.TestCase):
    def test_one_call_sends_everything_in_order_saves_and_moves_the_turn(self):
        sys.path.insert(0, str(D))
        sys.path.insert(0, str(S))
        import turn
        sent, pushed, saved = [], [], []

        def fake_send_main():
            sent.append((list(sys.argv[1:]), sys.stdin.read()))

        def fake_push_main():
            pushed.append(list(sys.argv[1:]))

        import micro_save
        with mock.patch.object(turn.send, "main", fake_send_main), mock.patch.object(turn.push_stats, "main", fake_push_main), \
                mock.patch.object(micro_save, "main", lambda a: saved.append(a)):
            turn.main(["-c", "camp", "--player", "Dagna: I strike.", "--dice", "d20+5 = 17", "--narrate", "It hits.",
                       "--stat-hp", "Dagna:4:5", "--npc", "Orc: Argh!", "--event", "Dagna hit the orc", "--turn-current", "Orc"])
        self.assertEqual(sent, [(["--player", "Dagna"], "I strike."), (["--dice"], "d20+5 = 17"),
                                (["--stat-hp", "Dagna:4:5"], "It hits."), (["--npc", "Orc"], "Argh!")])
        self.assertEqual(saved, [["-c", "camp", "--event", "Dagna hit the orc"]])
        self.assertEqual(pushed, [["--turn-current", "Orc"]])

    def test_dm_rules(self):
        skill = read(ROOT / "skills" / "dnd" / "SKILL.md")
        self.assertIn("Fast turn (hard rule): aim for 5 seconds", skill)
        self.assertIn("${CLAUDE_SKILL_DIR}/display/turn.py", skill)
        self.assertNotIn("plugins/cache", skill)
        self.assertIn("Fast Turn — `display/turn.py`", read(ROOT / "skills" / "dnd" / "SKILL-scripts.md"))


if __name__ == "__main__":
    unittest.main()
