"""Narration uses the local Pocket TTS voice only: cache, errors, route, page."""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
SCRIPTS = REPO / "skills" / "dnd" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(DISPLAY))

import tts  # noqa: E402


class CacheAndSynthTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"DND_TTS_CACHE_DIR": self.tmp.name,
                                                "DND_TTS_CACHE_MB": "8"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_second_play_comes_from_the_cache_not_the_model(self):
        fake = mock.Mock(return_value=b"\x01\x02" * 100)
        with mock.patch("local_fallback.synthesize", fake):
            first = tts.synthesize_strict("The door creaks open.")
            second = tts.synthesize_strict("The door creaks open.")
        self.assertEqual(first, second)
        self.assertEqual(fake.call_count, 1)

    def test_different_text_is_synthesized_again(self):
        fake = mock.Mock(return_value=b"\x01\x02" * 100)
        with mock.patch("local_fallback.synthesize", fake):
            tts.synthesize_strict("One.")
            tts.synthesize_strict("Two.")
        self.assertEqual(fake.call_count, 2)

    def test_model_failure_raises_instead_of_switching_voice(self):
        with mock.patch("local_fallback.synthesize", side_effect=RuntimeError("server did not start")):
            with self.assertRaises(tts.TtsError):
                tts.synthesize_strict("Hello.")
            self.assertIsNone(tts.synthesize("Hello."))

    def test_empty_text_is_rejected(self):
        with self.assertRaises(tts.TtsError):
            tts.synthesize_strict("   ")

    def test_cache_is_bounded(self):
        with mock.patch.dict(os.environ, {"DND_TTS_CACHE_MB": "0.0003"}):  # ~300 bytes
            with mock.patch("local_fallback.synthesize", side_effect=lambda t, lang="auto", speed=1.0: b"x" * 200):
                for i in range(5):
                    tts.synthesize_strict(f"line {i}")
            total = sum(f.stat().st_size for f in Path(self.tmp.name).glob("*.pcm"))
            self.assertLessEqual(total, 300)

    def test_warm_up_loads_only_the_english_model_and_never_raises(self):
        fake = mock.Mock(return_value=b"\x01\x02" * 10)
        with mock.patch("local_fallback.enabled", return_value=True), \
             mock.patch("local_fallback.synthesize", fake):
            tts.warm_up()
        self.assertEqual(fake.call_count, 1)
        self.assertEqual(fake.call_args.args[1], "en")
        with mock.patch("local_fallback.enabled", return_value=True), \
             mock.patch("local_fallback.synthesize", side_effect=RuntimeError("down")):
            tts.warm_up()   # must not raise

    def test_warm_up_does_nothing_when_the_voice_is_not_installed(self):
        fake = mock.Mock()
        with mock.patch("local_fallback.enabled", return_value=False),              mock.patch("local_fallback.synthesize", fake):
            tts.warm_up()
        fake.assert_not_called()

    def test_only_english_is_spoken_whatever_language_is_asked_for(self):
        fake = mock.Mock(return_value=b"\x01\x02" * 20)
        with mock.patch("local_fallback.synthesize", fake):
            tts.synthesize_strict("Goedemorgen allemaal.", "nl")
            tts.synthesize_strict("Goedemorgen allemaal.", "en")      # same clip: already stored
        self.assertEqual(fake.call_count, 1)
        self.assertEqual(fake.call_args.args[1], "en")
        self.assertEqual(tts.cache_key("x", lang="nl"), tts.cache_key("x", lang="en"))

    def test_auto_keeps_the_key_clips_were_stored_under_before_languages(self):
        import hashlib
        h = hashlib.sha256()
        for part in (tts.VOICE_ID, "Old clip."):
            h.update(part.encode("utf-8")); h.update(b"\x00")
        self.assertEqual(tts.cache_key("Old clip."), h.hexdigest())
        self.assertEqual(tts.cache_key("Old clip.", lang="auto"), h.hexdigest())

    def test_any_language_maps_to_english_for_now(self):
        for asked in ("klingon", "nl", "NL", "auto", None, ""):
            self.assertEqual(tts.clean_lang(asked), "en", asked)
        self.assertEqual(tts.SUPPORTED_LANGUAGES, ("en",))

    def test_stop_voice_asks_a_running_server_to_exit_and_never_raises(self):
        import local_fallback
        with mock.patch.object(local_fallback, "_up", return_value=True),              mock.patch("urllib.request.urlopen") as opened:
            tts.stop_voice()
        self.assertEqual(opened.call_count, 1)
        self.assertTrue(opened.call_args[0][0].full_url.endswith("/shutdown"))
        with mock.patch.object(local_fallback, "_up", return_value=False),              mock.patch("urllib.request.urlopen") as opened:
            tts.stop_voice()
        opened.assert_not_called()
        with mock.patch.object(local_fallback, "_up", side_effect=RuntimeError("x")):
            tts.stop_voice()   # must not raise

    def test_no_remote_service_in_the_module(self):
        src = (DISPLAY / "tts.py").read_text(encoding="utf-8").lower()
        for needle in ("gemini", "googleapis", "api_key", "urllib.request"):
            self.assertNotIn(needle, src)


class RouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        (root / "campaigns").mkdir()
        cls._env = {k: os.environ.get(k) for k in ("DND_CAMPAIGN_ROOT", "DND_RUNTIME_DIR")}
        os.environ["DND_CAMPAIGN_ROOT"] = str(root)
        os.environ["DND_RUNTIME_DIR"] = str(root / ".runtime")
        import runtime_paths  # remembers its folder from first import: pin it to the temp dir
        (root / ".runtime").mkdir(exist_ok=True)
        cls._rt_patch = mock.patch.object(runtime_paths, "RT", str(root / ".runtime"))
        cls._rt_patch.start()
        spec = importlib.util.spec_from_file_location("display_app_tts", DISPLAY / "dnd-display-app.py")
        cls.mod = importlib.util.module_from_spec(spec)
        sys.modules["display_app_tts"] = cls.mod
        spec.loader.exec_module(cls.mod)
        cls.client = cls.mod.app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls._rt_patch.stop()
        for k, v in cls._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        cls.tmp.cleanup()

    def test_returns_pcm_from_the_local_voice(self):
        with mock.patch.object(self.mod._tts, "available", return_value=True), \
             mock.patch.object(self.mod._tts, "synthesize_strict", return_value=b"\x00\x01" * 50):
            r = self.client.post("/tts", json={"text": "Hello there."})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data, b"\x00\x01" * 50)
        self.assertIn("audio/L16", r.headers["Content-Type"])

    def test_503_when_the_local_voice_is_not_installed(self):
        with mock.patch.object(self.mod._tts, "available", return_value=False):
            r = self.client.post("/tts", json={"text": "Hello there."})
        self.assertEqual(r.status_code, 503)

    def test_502_when_the_local_model_fails(self):
        with mock.patch.object(self.mod._tts, "available", return_value=True), \
             mock.patch.object(self.mod._tts, "synthesize_strict",
                               side_effect=self.mod._tts.TtsError("boom")):
            r = self.client.post("/tts", json={"text": "Hello there."})
        self.assertEqual(r.status_code, 502)

    def test_route_passes_the_language_through(self):
        with mock.patch.object(self.mod._tts, "available", return_value=True),              mock.patch.object(self.mod._tts, "synthesize_strict", return_value=b"\x00\x01") as syn:
            self.client.post("/tts", json={"text": "Hallo daar.", "lang": "nl"})
        syn.assert_called_once_with("Hallo daar.", "nl")

    def test_empty_text_is_400(self):
        self.assertEqual(self.client.post("/tts", json={"text": " "}).status_code, 400)

    def test_old_voice_route_is_gone(self):
        self.assertEqual(self.client.post("/voice", json={"voice": "Enceladus"}).status_code, 404)


class PageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (DISPLAY / "templates" / "index.html").read_text(encoding="utf-8")

    def test_page_plays_local_audio_only(self):
        self.assertIn("fetch('/tts'", self.html)
        self.assertNotIn("speechSynthesis", self.html)
        self.assertNotIn("SpeechSynthesisUtterance", self.html)

    def test_no_voice_picker_for_remote_voices(self):
        for name in ("_TTS_VOICES", "tts-voice-menu", "fetch('/voice'"):
            self.assertNotIn(name, self.html)

    def test_a_failure_is_shown_not_hidden_by_another_voice(self):
        self.assertIn("No local voice", self.html)


if __name__ == "__main__":
    unittest.main()
