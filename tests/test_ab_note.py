"""Unit tests for tools/ab_note.py (stdlib unittest).

Fixture: tests/fixtures/acceptance-ep18.json — the real ep.18 acceptance
gate (shipped house take vs Cadence fixture), copied verbatim from the
White Report voice-humanization fixture folder.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
FIXTURE = os.path.join(HERE, "fixtures", "acceptance-ep18.json")


def _load_tool():
    path = os.path.join(ROOT, "tools", "ab_note.py")
    spec = importlib.util.spec_from_file_location("ab_note", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestAbNoteEp18Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ab = _load_tool()
        with open(FIXTURE, encoding="utf-8") as fh:
            cls.acceptance = json.load(fh)
        house = cls.ab._from_acceptance_side(cls.acceptance["shipped"])
        cadence = cls.ab._from_acceptance_side(cls.acceptance["cadence"])
        cls.note = cls.ab.build_note(house, cadence,
                                     cls.ab._episode_label(cls.acceptance))

    def test_title_and_episode(self):
        self.assertIn("(INT — INTERNAL) A/B Listening Note — ep.18",
                      self.note)

    def test_takes_and_durations(self):
        self.assertIn("52.9 s", self.note)   # shipped 52.944
        self.assertIn("67.1 s", self.note)   # cadence 67.144
        self.assertIn("reels/ep18/work/vo-joined.wav", self.note)
        self.assertIn("fixture-ep18/cadence-ep18.wav", self.note)

    def test_table_values(self):
        self.assertIn("5.13", self.note)     # house F0 SD
        self.assertIn("5.50", self.note)     # cadence F0 SD
        self.assertIn("0.361", self.note)    # house pause mean
        self.assertIn("0.479", self.note)    # cadence pause mean
        self.assertIn("70%", self.note)      # house in-band
        self.assertIn("50%", self.note)      # cadence in-band

    def test_what_you_will_hear_rules_fire(self):
        # cadence pause_mean 0.479 > house 0.361
        self.assertIn("longer, classed silences", self.note.lower())
        self.assertIn("breath/emphasis/act-boundary instead of one flat gap",
                      self.note)
        # cadence dur 67.144 > house 52.944
        self.assertIn("The Cadence take runs longer: no global tempo crush",
                      self.note)
        self.assertIn("pace now comes from the script, not the squeeze",
                      self.note)
        # cadence dynamic range 5.3 > house 3.14
        self.assertIn("Wider loudness swings on Cadence", self.note)

    def test_verdict_line(self):
        self.assertIn("Owner picks by ear; Cadence is NOT the shipping "
                      "path unless picked.", self.note)


class TestAbNoteCli(unittest.TestCase):
    def test_acceptance_alone_via_cli(self):
        tool = os.path.join(ROOT, "tools", "ab_note.py")
        with tempfile.TemporaryDirectory() as td:
            out = os.path.join(td, "AB-LISTENING-NOTE.md")
            proc = subprocess.run(
                [sys.executable, tool, "--acceptance", FIXTURE,
                 "--out", out], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            with open(out, encoding="utf-8") as fh:
                note = fh.read()
        self.assertIn("ep.18", note)
        self.assertIn("Owner picks by ear", note)

    def test_no_house_metrics_says_not_measured(self):
        ab = _load_tool()
        cadence = ab._from_acceptance_side(
            {"duration_s": 60.0, "audio": "x.wav",
             "gate": {"in_band_pct": 50.0, "metrics": {}}})
        note = ab.build_note(None, cadence, "ep.19")
        self.assertIn("House take not yet measured", note)
        self.assertIn("ep.19", note)

    def test_default_episode_is_ep19(self):
        self.assertEqual(_load_tool()._episode_label(None), "ep.19")
        self.assertEqual(_load_tool()._episode_label({}), "ep.19")


if __name__ == "__main__":
    unittest.main()
