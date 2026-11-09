"""M2 renderer tests: synthesis stubbed (no tts, no engine).

The stub writes a 48 kHz mono sine wav via the stdlib wave module,
0.30 s per word, so every expected duration is known exactly:
out_dur = raw_dur / tempo, joined = sum(out_durs) + sum(pauses).
"""
import array
import json
import math
import os
import sys
import tempfile
import unittest
import wave

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from cadence import helio, render

RATE = 48000
SECS_PER_WORD = 0.30


def stub_synth(text, out_path):
    words = max(1, len(text.split()))
    n = int(RATE * SECS_PER_WORD * words)
    samples = array.array("h", bytes(2 * n))
    for i in range(n):
        samples[i] = int(8000 * math.sin(2 * math.pi * 440 * i / RATE))
    with wave.open(out_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(samples.tobytes())
    return {"route": "stub", "path": out_path}


def span(sid, segment, act, tempo, intensity, pause_class, seconds, words):
    return {
        "span_id": sid, "segment": segment, "act": act,
        "text_raw": " ".join(["word"] * words),
        "text_encoded": " ".join(["word"] * words),
        "peak": None, "contour": "fall",
        "pause_after": {"class": pause_class, "seconds": seconds},
        "tempo": tempo, "register": "medium", "intensity": intensity,
    }


SCORE = {"script_id": "stub", "source_sha256": "0" * 64, "persona": None,
         "rule_set": "rules-v1",
         "spans": [
             span("sp001", "S1", "hook", 1.00, "normal", "breath", 0.35, 10),
             span("sp002", "S2", "evidence", 1.05, "reduced", "emphasis", 0.70, 10),
             span("sp003", "S3", "turn", 0.95, "normal", "act-boundary", 0.90, 8),
             span("sp004", "S5", "payoff", 0.92, "climax-quiet", "none", 0.0, 6),
         ]}

EXPECTED_SPAN_OUT = [3.0 / 1.00, 3.0 / 1.05, 2.4 / 0.95, 1.8 / 0.92]
EXPECTED_PAUSES = [0.35, 0.70, 0.90, 0.0]
EXPECTED_TOTAL = sum(EXPECTED_SPAN_OUT) + sum(EXPECTED_PAUSES)


class TestRender(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._real_synth = helio.synth_span
        helio.synth_span = stub_synth
        cls._tmp = tempfile.mkdtemp(prefix="cadence-render-test-")
        cls.out = os.path.join(cls._tmp, "joined.wav")
        cls.result = render.render_score(
            SCORE, cls.out, os.path.join(cls._tmp, "work"))
        with open(cls.out + ".timings.json", encoding="utf-8") as fh:
            cls.timings = json.load(fh)
        with open(cls.out + ".renderlog.json", encoding="utf-8") as fh:
            cls.renderlog = json.load(fh)

    @classmethod
    def tearDownClass(cls):
        helio.synth_span = cls._real_synth

    def test_joined_duration_matches_tempo_and_pauses(self):
        dur = render.wav_duration(self.out)
        self.assertAlmostEqual(dur, EXPECTED_TOTAL,
                               delta=0.05 * EXPECTED_TOTAL)

    def test_no_flat_join_constant(self):
        pauses = [t["pause_after_s"] for t in self.timings]
        self.assertEqual(pauses, EXPECTED_PAUSES)
        self.assertNotIn(0.12, pauses)
        self.assertNotIn(0.120, pauses)

    def test_timings_monotonic_and_measured(self):
        prev_end = None
        for t, span_out, pause in zip(self.timings, EXPECTED_SPAN_OUT,
                                      EXPECTED_PAUSES):
            self.assertLess(t["start_s"], t["end_s"])
            self.assertAlmostEqual(t["end_s"] - t["start_s"], span_out,
                                   delta=0.05 * span_out)
            self.assertEqual(t["pause_after_s"], pause)
            if prev_end is not None:
                self.assertGreaterEqual(t["start_s"], prev_end)
            prev_end = t["end_s"]

    def test_renderlog_records_route_tempo_durations(self):
        self.assertEqual(len(self.renderlog["spans"]), 4)
        for entry, span_out in zip(self.renderlog["spans"],
                                   EXPECTED_SPAN_OUT):
            self.assertEqual(entry["synth_route"], "stub")
            self.assertAlmostEqual(entry["out_dur_s"], span_out,
                                   delta=0.05 * span_out)
            self.assertAlmostEqual(
                entry["out_dur_s"],
                entry["raw_dur_s"] / entry["tempo"],
                delta=0.05 * span_out)

    def test_intensity_gain_map(self):
        self.assertEqual(render.INTENSITY_GAIN,
                         {"reduced": 0.79, "climax-quiet": 0.71,
                          "normal": 1.0})
        gains = {e["span_id"]: e["gain"] for e in self.renderlog["spans"]}
        self.assertEqual(gains, {"sp001": 1.0, "sp002": 0.79,
                                 "sp003": 1.0, "sp004": 0.71})


if __name__ == "__main__":
    unittest.main()
