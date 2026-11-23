"""Beat by beat: the DM pauses after a step, every screen shows Continue, pressing it queues [Continue] for the DM."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "skills" / "dnd" / "display"


class BeatByBeatTest(unittest.TestCase):
    def test_wiring(self):
        app = (D / "dnd-display-app.py").read_text(encoding="utf-8")
        self.assertIn('@app.route("/beat/pause", methods=["POST"])', app)
        self.assertIn('f.write(f"[Continue]: {info[', app)
        html = (D / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn('<button id="beat-continue"', html)
        self.assertIn("if ('beat_pause' in payload) _showBeatPause(payload.beat_pause);", html)
        self.assertIn('ap.add_argument("--pause"', (D / "turn.py").read_text(encoding="utf-8"))
        self.assertIn("Beat by beat (hard rule)", (ROOT / "skills" / "dnd" / "SKILL.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
