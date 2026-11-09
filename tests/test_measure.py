import math
import os
import struct
import sys
import unittest
import wave

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from cadence.measure import classify_pause, measure, pause_distribution


def _write_sine_silence(path, sr=16000):
    """0.6 s of 120 Hz sine, 0.4 s silence, 0.6 s sine (stdlib only)."""
    frames = bytearray()
    for i in range(int(sr * 0.6)):
        v = int(0.5 * 32767 * math.sin(2 * math.pi * 120 * i / sr))
        frames += struct.pack("<h", v)
    frames += b"\x00\x00" * int(sr * 0.4)
    for i in range(int(sr * 0.6)):
        v = int(0.5 * 32767 * math.sin(2 * math.pi * 120 * i / sr))
        frames += struct.pack("<h", v)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(bytes(frames))


class TestPauseClassifier(unittest.TestCase):
    def test_bins(self):
        self.assertEqual(classify_pause(0.05), "micro")
        self.assertEqual(classify_pause(0.24), "micro")
        self.assertEqual(classify_pause(0.25), "breath")
        self.assertEqual(classify_pause(0.40), "breath")
        self.assertEqual(classify_pause(0.55), "breath")
        self.assertEqual(classify_pause(0.56), "emphasis")
        self.assertEqual(classify_pause(1.20), "emphasis")

    def test_distribution(self):
        pauses = [{"class": "micro"}, {"class": "breath"}, {"class": "breath"}]
        dist = pause_distribution(pauses)
        self.assertAlmostEqual(dist["micro"]["share"], 1 / 3)
        self.assertEqual(dist["breath"]["count"], 2)
        self.assertEqual(dist["emphasis"]["count"], 0)


class TestMeasureSynthetic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "_synthetic_test.wav")
        _write_sine_silence(cls.path)
        cls.metrics = measure(cls.path)

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(cls.path):
            os.unlink(cls.path)

    def test_schema_keys(self):
        for key in ("schema", "audio", "duration_s", "pauses", "f0",
                    "rate", "loudness", "spans", "chunks"):
            self.assertIn(key, self.metrics)
        for key in ("list", "count", "mean_s", "max_s", "distribution"):
            self.assertIn(key, self.metrics["pauses"])
        for key in ("mean_hz", "sd_semitones", "voiced_fraction", "per_act"):
            self.assertIn(key, self.metrics["f0"])
        self.assertIn("rate_cv_basis", self.metrics["rate"])
        self.assertIn("dynamic_range_db", self.metrics["loudness"])
        self.assertAlmostEqual(self.metrics["duration_s"], 1.6, places=2)

    def test_f0_120hz_within_5pct(self):
        mean = self.metrics["f0"]["mean_hz"]
        self.assertIsNotNone(mean)
        self.assertLess(abs(mean - 120.0) / 120.0, 0.05, mean)

    def test_pause_detected_as_breath(self):
        pauses = self.metrics["pauses"]["list"]
        self.assertTrue(pauses, "expected the 0.4 s silence to be detected")
        longest = max(p["dur"] for p in pauses)
        self.assertGreater(longest, 0.3)
        self.assertLess(longest, 0.5)


if __name__ == "__main__":
    unittest.main()
