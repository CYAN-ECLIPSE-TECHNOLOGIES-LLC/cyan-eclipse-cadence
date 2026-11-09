import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from cadence.compile import compile_file, compile_parsed
from cadence.parse import parse_script, words_of, clean_text
from cadence.rules import RuleSet, load_rules

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "ep18-script.md")
ALLOWED_PAUSES = {0.0, 0.35, 0.70, 0.90}


class TestRules(unittest.TestCase):
    def test_14_rules_with_citations(self):
        data = load_rules()
        rules = data["rules"]
        self.assertEqual(len(rules), 14)
        self.assertEqual([r["id"] for r in rules],
                         ["R%d" % i for i in range(1, 15)])
        for r in rules:
            self.assertTrue(r["citation"], r["id"])
            self.assertTrue(r["name"], r["id"])
            self.assertTrue(r["realisation"], r["id"])
            self.assertTrue(r["parameters"], r["id"])

    def test_pause_map_has_no_flat_join(self):
        rs = RuleSet()
        self.assertEqual(rs.pause_map, {"breath": 0.35, "emphasis": 0.70,
                                        "act-boundary": 0.90, "none": 0.0})
        self.assertNotIn(0.12, rs.pause_map.values())
        self.assertNotIn(0.120, rs.pause_map.values())

    def test_tempo_bounds_and_defaults(self):
        rs = RuleSet()
        self.assertEqual(rs.tempo_bounds, (0.88, 1.12))
        self.assertEqual(rs.act_tempo, {"hook": 1.00, "evidence": 1.05,
                                        "turn": 0.95, "payoff": 0.92})
        self.assertEqual(rs.emphasis_budget_per_minute, 6)


class TestCompileEp18(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.score = compile_file(FIXTURE)

    def test_contract_shape(self):
        s = self.score
        self.assertEqual(s["rule_set"], "rules-v1")
        self.assertEqual(len(s["source_sha256"]), 64)
        self.assertIsNone(s["persona"])
        for span in s["spans"]:
            self.assertEqual(
                set(span.keys()),
                {"span_id", "segment", "act", "text_raw", "text_encoded",
                 "peak", "contour", "pause_after", "tempo", "register",
                 "intensity"})

    def test_five_segments_hook_to_payoff(self):
        segments = []
        for span in self.score["spans"]:
            if span["segment"] not in segments:
                segments.append(span["segment"])
        self.assertGreaterEqual(len(segments), 5)
        self.assertEqual(segments, ["S1", "S2", "S3", "S4", "S5"])
        acts = {}
        for span in self.score["spans"]:
            acts.setdefault(span["segment"], span["act"])
        self.assertEqual(acts["S1"], "hook")
        self.assertEqual(acts["S5"], "payoff")
        for seg in ("S2", "S3", "S4"):
            self.assertIn(acts[seg], ("evidence", "turn"))

    def test_tempos_within_bounds(self):
        for span in self.score["spans"]:
            self.assertGreaterEqual(span["tempo"], 0.88)
            self.assertLessEqual(span["tempo"], 1.12)

    def test_breath_groups_at_most_14_words(self):
        for span in self.score["spans"]:
            self.assertLessEqual(len(words_of(clean_text(span["text_raw"]))), 14,
                                 span["text_raw"])

    def test_pause_classes_and_seconds(self):
        for span in self.score["spans"]:
            self.assertIn(span["pause_after"]["class"],
                          ("breath", "emphasis", "act-boundary", "none"))
            self.assertIn(span["pause_after"]["seconds"], ALLOWED_PAUSES)
            self.assertNotEqual(span["pause_after"]["seconds"], 0.12)

    def test_one_peak_per_sentence_max(self):
        # Spans sharing a sentence end share the sentence-final fall contour;
        # at most one span per sentence may carry the peak: check globally
        # that peaks never repeat back-to-back within a segment sentence run
        # by asserting peak count <= sentence count.
        with open(FIXTURE, encoding="utf-8") as fh:
            text = fh.read()
        parsed = parse_script(text)
        from cadence.parse import split_sentences
        n_sent = sum(len(split_sentences(seg.text_raw)) for seg in parsed.segments)
        n_peaks = sum(1 for sp in self.score["spans"] if sp["peak"])
        self.assertEqual(n_peaks, n_sent)

    def test_series_penultimate_rise(self):
        s5 = [sp for sp in self.score["spans"] if sp["segment"] == "S5"]
        # First S5 sentence is the series sentence (2+ commas).
        self.assertEqual(s5[0]["contour"], "rise")
        self.assertEqual(s5[1]["contour"], "fall")

    def test_antithesis_mirrored(self):
        s1 = [sp for sp in self.score["spans"] if sp["segment"] == "S1"]
        texts = [sp["text_raw"] for sp in s1]
        i = next(i for i, t in enumerate(texts) if t.rstrip().endswith(";"))
        self.assertEqual(s1[i]["contour"], "rise")
        self.assertEqual(s1[i + 1]["contour"], "fall")

    def test_climax_quiet_at_payoff_end(self):
        last = self.score["spans"][-1]
        self.assertEqual(last["act"], "payoff")
        self.assertEqual(last["intensity"], "climax-quiet")
        self.assertEqual(last["pause_after"], {"class": "none", "seconds": 0.0})

    def test_registers_per_act(self):
        regs = {}
        for span in self.score["spans"]:
            regs.setdefault(span["act"], span["register"])
        self.assertEqual(regs["hook"], "medium")
        self.assertEqual(regs["payoff"], "lowered")
        if "turn" in regs:
            self.assertEqual(regs["turn"], "raised")


def _compile_inline(body, persona=None):
    lines = []
    if persona:
        lines.append("> Persona: %s" % persona)
    lines.append("## SCRIPT")
    lines.append(body)
    return compile_parsed(parse_script("\n".join(lines)), "inline", "0" * 64)


class TestMarkupAndPersona(unittest.TestCase):
    def test_author_markup_tier3_honoured(self):
        score = _compile_inline(
            "[S1] The market moved on **volume** today and kept moving.\n"
            "[S2] Plain closing words for the final segment here.")
        peaks = [sp["peak"] for sp in score["spans"] if sp["peak"]]
        self.assertIn({"word": "volume", "tier": 3}, peaks)
        enc = [sp["text_encoded"] for sp in score["spans"]
               if sp["peak"] == {"word": "volume", "tier": 3}]
        self.assertIn("VOLUME", enc[0])

    def test_author_markup_tier2_and_caps(self):
        score = _compile_inline(
            "[S1] A *quiet* bid held while DANGER built in the book.\n"
            "[S2] Closing segment words go here for the payoff act.")
        peaks = [sp["peak"] for sp in score["spans"] if sp["peak"]]
        self.assertIn({"word": "DANGER", "tier": 3}, peaks)

    def test_emphasis_pause_token(self):
        score = _compile_inline(
            "[S1] Hold || now. Then go on speaking plainly here today.\n"
            "[S2] Closing segment words go here for the payoff act.")
        classes = [sp["pause_after"]["class"] for sp in score["spans"]]
        self.assertIn("emphasis", classes)
        sp = next(sp for sp in score["spans"]
                  if sp["pause_after"]["class"] == "emphasis")
        self.assertEqual(sp["pause_after"]["seconds"], 0.70)

    def test_persona_header(self):
        score = _compile_inline(
            "[S1] Opening words for the hook segment go here now.\n"
            "[S2] Closing segment words go here for the payoff act.",
            persona="The Analyst | a skeptical friend | to warn them early")
        self.assertEqual(score["persona"],
                         {"speaker": "The Analyst", "to": "a skeptical friend",
                          "want": "to warn them early"})

    def test_series_synthetic_penultimate_rise(self):
        score = _compile_inline(
            "[S1] Opening words for the hook segment go here now.\n"
            "[S2] Alpha one, bravo two, charlie three, delta four, echo five, "
            "foxtrot six, golf seven, hotel eight.\n"
            "[S3] Closing segment words go here for the payoff act.")
        s2 = [sp for sp in score["spans"] if sp["segment"] == "S2"]
        self.assertGreaterEqual(len(s2), 2)
        self.assertEqual(s2[-2]["contour"], "rise")
        self.assertEqual(s2[-1]["contour"], "fall")


if __name__ == "__main__":
    unittest.main()
