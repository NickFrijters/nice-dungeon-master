"""Optional Kokoro engine: default stays pocket; cache key and calls follow the engine."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
sys.path.insert(0, str(REPO / "skills" / "dnd" / "scripts"))
sys.path.insert(0, str(DISPLAY))

sys.path.insert(0, str(DISPLAY / "local_tts"))

import tts  # noqa: E402
import local_fallback  # noqa: E402


class EngineChoiceTest(unittest.TestCase):
    def test_default_is_pocket(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DND_TTS_ENGINE", None)
            self.assertEqual(local_fallback.engine(), "pocket")
            self.assertEqual(tts._voice_id(1.0), "pocket-alba")
            self.assertEqual(local_fallback.port(), 8766)

    def test_unknown_engine_means_pocket(self):
        with mock.patch.dict(os.environ, {"DND_TTS_ENGINE": "bark"}):
            self.assertEqual(local_fallback.engine(), "pocket")
            self.assertEqual(tts._engine(), "pocket")

    def test_kokoro_has_its_own_venv_server_and_port(self):
        with mock.patch.dict(os.environ, {"DND_TTS_ENGINE": "kokoro"}):
            os.environ.pop("DND_LOCAL_TTS_PORT", None)
            self.assertEqual(local_fallback.engine(), "kokoro")
            self.assertIn("tts-kokoro", str(local_fallback.venv_python()))
            self.assertEqual(local_fallback.port(), 8767)
            self.assertTrue((DISPLAY / "local_tts" / "kokoro_server.py").is_file())

    def test_port_override_applies_to_either_engine(self):
        with mock.patch.dict(os.environ, {"DND_TTS_ENGINE": "kokoro", "DND_LOCAL_TTS_PORT": "8790"}):
            self.assertEqual(local_fallback.port(), 8790)

    def test_cache_identity_includes_engine_and_voice(self):
        pocket = tts._voice_id(1.0, "pocket")
        kokoro = tts._voice_id(1.0, "kokoro")
        self.assertNotEqual(pocket, kokoro)
        self.assertIn(tts.KOKORO_VOICE, kokoro)
        self.assertNotEqual(tts.cache_key("x", voice=pocket), tts.cache_key("x", voice=kokoro))
        self.assertNotEqual(tts._voice_id(0.8, "kokoro"), kokoro)


class KokoroSynthWiringTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"DND_TTS_CACHE_DIR": self.tmp.name,
                                                "DND_TTS_CACHE_MB": "8", "DND_TTS_ENGINE": "kokoro"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_voice_is_passed_and_clips_are_cached_per_engine(self):
        fake = mock.Mock(return_value=b"\x01\x02" * 50)
        with mock.patch("local_fallback.synthesize", fake):
            tts.synthesize_strict("The door creaks open.")
            tts.synthesize_strict("The door creaks open.")
        self.assertEqual(fake.call_count, 1)
        self.assertEqual(fake.call_args.args[3], tts.KOKORO_VOICE)
        # the same text under pocket is a different clip
        with mock.patch.dict(os.environ, {"DND_TTS_ENGINE": "pocket"}):
            other = mock.Mock(return_value=b"\x03\x04" * 50)
            with mock.patch("local_fallback.synthesize", other):
                tts.synthesize_strict("The door creaks open.")
            self.assertEqual(other.call_count, 1)
            self.assertEqual(other.call_args.args, ("The door creaks open.", "en", tts.SPEED))


class KokoroServerTest(unittest.TestCase):
    def test_sentence_split_and_voice_sanitised(self):
        import importlib
        ks = importlib.import_module("kokoro_server")
        self.assertEqual(ks.split_sentences("One. Two!  Three?\nFour"), ["One.", "Two!", "Three?", "Four"])

    def test_synth_joins_sentences_with_a_pause_using_a_mocked_model(self):
        import numpy as np
        import kokoro_server as ks
        model = mock.Mock()
        model.create.return_value = (np.full(2400, 0.5, dtype=np.float32), 24000)
        with mock.patch.object(ks, "_load", return_value=model):
            pcm = ks.synth("A. B.", 1.0, "am_adam")
        n = len(pcm) // 2
        self.assertEqual(n, 2400 * 2 + int(24000 * ks.PAUSE_S))
        self.assertEqual(model.create.call_args.kwargs["voice"], "am_adam")
        self.assertEqual(model.create.call_args.kwargs["lang"], "en-us")


if __name__ == "__main__":
    unittest.main()
