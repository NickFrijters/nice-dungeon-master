"""The narrator talks at 80% speed, with the pitch unchanged."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
DISPLAY = REPO / "skills" / "dnd" / "display"
sys.path.insert(0, str(DISPLAY))
sys.path.insert(0, str(DISPLAY / "local_tts"))

import tts  # noqa: E402

try:
    import numpy as np
    from stretch import time_stretch
except Exception:   # numpy is optional for the repo's other tests
    np = None


@unittest.skipUnless(np, "numpy not installed")
class StretchTest(unittest.TestCase):
    SR = 24000

    def _tone(self, hz=220.0, seconds=1.0):
        t = np.arange(int(self.SR * seconds)) / self.SR
        return (0.5 * np.sin(2 * np.pi * hz * t)).astype(np.float32)

    def _peak_hz(self, x):
        spectrum = np.abs(np.fft.rfft(x * np.hanning(len(x))))
        return np.argmax(spectrum) * self.SR / len(x)

    def test_80_percent_speed_makes_it_25_percent_longer(self):
        y = time_stretch(self._tone(), 0.8, self.SR)
        self.assertAlmostEqual(len(y) / self.SR, 1.25, delta=0.03)

    def test_pitch_is_kept(self):
        y = time_stretch(self._tone(220.0), 0.8, self.SR)
        self.assertAlmostEqual(self._peak_hz(y), 220.0, delta=6.0)
        # plain slowing would have dropped it to about 176 Hz
        self.assertGreater(self._peak_hz(y), 200.0)

    def test_the_signal_is_not_silenced_or_clipped(self):
        y = time_stretch(self._tone(), 0.8, self.SR)
        self.assertGreater(float(np.abs(y).max()), 0.35)
        self.assertLessEqual(float(np.abs(y).max()), 0.8)
        self.assertTrue(np.isfinite(y).all())

    def test_normal_speed_and_empty_input_are_left_alone(self):
        x = self._tone()
        self.assertTrue(np.array_equal(time_stretch(x, 1.0, self.SR), x))
        self.assertEqual(len(time_stretch(np.zeros(0, dtype=np.float32), 0.8, self.SR)), 0)

    def test_a_very_short_clip_is_returned_rather_than_crashing(self):
        x = self._tone(seconds=0.02)
        self.assertGreater(len(time_stretch(x, 0.8, self.SR)), 0)

    def test_a_ten_second_clip_is_quick(self):
        import time
        x = self._tone(seconds=10.0)
        t = time.time()
        time_stretch(x, 0.8, self.SR)
        self.assertLess(time.time() - t, 3.0)


class SpeedWiringTest(unittest.TestCase):
    def test_default_is_80_percent(self):
        self.assertEqual(tts.SPEED, 0.8)

    def test_speed_is_part_of_the_cache_identity(self):
        self.assertIn("80", tts.VOICE_ID)
        self.assertNotEqual(tts.cache_key("x"), tts.cache_key("x", voice="pocket-alba"))

    def test_speed_reaches_the_voice(self):
        with mock.patch.dict(os.environ, {"DND_TTS_CACHE_MB": "0"}), \
             mock.patch("local_fallback.synthesize", return_value=b"\x01\x02") as syn:
            tts.synthesize_strict("Hello there.")
        syn.assert_called_once_with("Hello there.", "auto", tts.SPEED)

    def test_env_override_is_clamped(self):
        for raw, want in (("0.9", 0.9), ("5", 1.5), ("0.1", 0.5), ("nonsense", 0.8)):
            with mock.patch.dict(os.environ, {"DND_TTS_SPEED": raw}):
                self.assertEqual(tts._speed(), want, raw)

    def test_server_clamps_speed_and_defaults_to_normal(self):
        src = (DISPLAY / "local_tts" / "pocket_server.py").read_text(encoding="utf-8")
        self.assertIn("min(2.0, max(0.5, speed))", src)
        self.assertIn('data.get("speed", 1.0)', src)


if __name__ == "__main__":
    unittest.main()
