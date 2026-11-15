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
    def test_default_is_kokoro_when_installed_else_pocket(self):
        with mock.patch.dict(os.environ, {}):
            os.environ.pop("DND_TTS_ENGINE", None)
            with mock.patch.object(Path, "exists", return_value=True):
                self.assertEqual(local_fallback.engine(), "kokoro")
            with mock.patch.object(Path, "exists", return_value=False):
                self.assertEqual(local_fallback.engine(), "pocket")
                self.assertEqual(tts._voice_id(1.0), "pocket-alba")
                self.assertEqual(local_fallback.port(), 8766)

    def test_env_forces_the_engine(self):
        with mock.patch.object(Path, "exists", return_value=True):
            with mock.patch.dict(os.environ, {"DND_TTS_ENGINE": "pocket"}):
                self.assertEqual(local_fallback.engine(), "pocket")
                self.assertEqual(tts._voice_id(1.0), "pocket-alba")

    def test_unknown_engine_falls_back_to_the_default(self):
        with mock.patch.object(Path, "exists", return_value=False),              mock.patch.dict(os.environ, {"DND_TTS_ENGINE": "bark"}):
            self.assertEqual(local_fallback.engine(), "pocket")

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

    def test_voice_follows_env_then_settings_then_adam(self):
        import settings
        with mock.patch.dict(os.environ, {"DND_KOKORO_VOICE": "bm_george"}):
            self.assertEqual(tts._kokoro_voice(), "bm_george")
        with mock.patch.dict(os.environ, {}):
            os.environ.pop("DND_KOKORO_VOICE", None)
            with mock.patch.object(settings, "load", return_value={"narrator_voice": "am_onyx"}):
                self.assertEqual(tts._kokoro_voice(), "am_onyx")
            with mock.patch.object(settings, "load", side_effect=OSError):
                self.assertEqual(tts._kokoro_voice(), "am_adam")

    def test_settings_offer_the_four_voices_and_default_to_adam(self):
        import settings
        self.assertEqual(settings.NARRATOR_VOICES, ("am_adam", "am_fenrir", "am_onyx", "bm_george"))
        self.assertEqual(tts.KOKORO_VOICES, settings.NARRATOR_VOICES)
        self.assertEqual(settings.DEFAULTS["narrator_voice"], "am_adam")
        with self.assertRaises(ValueError):
            settings.save({"narrator_voice": "af_heart"})

    def test_cache_identity_includes_engine_and_voice(self):
        pocket = tts._voice_id(1.0, "pocket")
        adam = tts._voice_id(1.0, "kokoro", "am_adam")
        onyx = tts._voice_id(1.0, "kokoro", "am_onyx")
        self.assertEqual(len({pocket, adam, onyx}), 3)
        self.assertNotEqual(tts.cache_key("x", voice=adam), tts.cache_key("x", voice=onyx))
        self.assertNotEqual(tts._voice_id(0.8, "kokoro", "am_adam"), adam)


class FreshImportTest(unittest.TestCase):
    def test_tts_imports_on_its_own(self):
        import subprocess
        r = subprocess.run([sys.executable, "-I", "-c",
                            "import sys; sys.path.insert(0, sys.argv[1]); import tts; print(tts.VOICE_ID)",
                            str(DISPLAY)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


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
        self.assertEqual(fake.call_args.args[3], tts._kokoro_voice())
        with mock.patch("local_fallback.synthesize", fake):
            tts.synthesize_strict("The door creaks open.", "en", "am_onyx")   # another voice: a new clip
        self.assertEqual(fake.call_count, 2)
        self.assertEqual(fake.call_args.args[3], "am_onyx")
        fake.reset_mock()
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
