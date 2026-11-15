"""The line under each narration block names the voice engine that is really in use."""
import unittest
from pathlib import Path

D = Path(__file__).resolve().parent.parent / "skills" / "dnd" / "display"
HTML = (D / "templates" / "index.html").read_text(encoding="utf-8")
APP = (D / "dnd-display-app.py").read_text(encoding="utf-8")


class TtsLabelTest(unittest.TestCase):
    def test_label_comes_from_the_server_not_a_fixed_word(self):
        self.assertIn('<meta name="tts-engine" content="{{ tts_engine }}">', HTML)
        self.assertNotIn("textContent = 'Pocket TTS';", HTML)
        self.assertIn("tts_engine=_tts_engine_label()", APP)
        self.assertIn('"Kokoro TTS" if', APP)


if __name__ == "__main__":
    unittest.main()
