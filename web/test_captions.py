#!/usr/bin/env python3
"""Cadence web captions — service suite (unittest, run from web/:
python3 -m unittest test_captions -v).

Covers the captions contract: the embedded mov_text track is present
and selectable (mp4), sidecars parse with sane timings, the picture
stream hash is identical before/after, the WebM fallback is
sidecar-only as documented, the no-speech case is named honestly,
and the audio path carries no captions at all.

Fixtures are built in-suite from the firm's Paloma TTS acceptance
sample (tests/fixtures/paloma-sample.mp3, trimmed to 24 s) with
ffmpeg lavfi picture; transcription runs the deployed engine
(faster-whisper base, see requirements-captions.txt).
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import captions  # noqa: E402
import humanize  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE_MP3 = os.path.join(HERE, "tests", "fixtures", "paloma-sample.mp3")
SRT_TS = re.compile(r"(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> "
                    r"(\d{2}):(\d{2}):(\d{2}),(\d{3})")


def _run(cmd):
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def _probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,codec_name:stream_tags=language,title",
         "-of", "json", path], capture_output=True, text=True,
        check=True).stdout
    return json.loads(out).get("streams", [])


def _ts_to_s(h, m, s, ms):
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_srt(text):
    cues = []
    for block in text.strip().split("\n\n"):
        lines = block.strip().splitlines()
        if len(lines) < 3:
            raise AssertionError(f"malformed SRT block: {block!r}")
        m = SRT_TS.match(lines[1])
        if not m:
            raise AssertionError(f"bad SRT timing line: {lines[1]!r}")
        cues.append((_ts_to_s(*m.groups()[:4]), _ts_to_s(*m.groups()[4:]),
                     " ".join(lines[2:])))
    return cues


class TestRenderers(unittest.TestCase):
    SEGS = [{"start": 0.0, "end": 1.5, "text": "Hello there."},
            {"start": 61.25, "end": 63.0, "text": "Second line."}]

    def test_srt_format(self):
        out = captions.to_srt(self.SEGS)
        self.assertIn("1\n00:00:00,000 --> 00:00:01,500\nHello there.",
                      out)
        self.assertIn("2\n00:01:01,250 --> 00:01:03,000\nSecond line.",
                      out)
        self.assertEqual(len(parse_srt(out)), 2)

    def test_vtt_format(self):
        out = captions.to_vtt(self.SEGS)
        self.assertTrue(out.startswith("WEBVTT\n"))
        self.assertIn("00:00:00.000 --> 00:00:01.500", out)
        self.assertIn("00:01:01.250 --> 00:01:03.000", out)


class TestCleanSegments(unittest.TestCase):
    def test_clamp_drop_collapse(self):
        raw = [{"start": 1.0, "end": 99.0, "text": "  over   long "},
               {"start": 50.0, "end": 51.0, "text": "beyond"},
               {"start": 2.0, "end": 2.5, "text": "   "},
               {"start": 3.0, "end": 2.0, "text": "inverted"}]
        out = captions.clean_segments(raw, 41.09)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["end"], 41.09)   # clamped, not extended
        self.assertEqual(out[0]["text"], "over long")


class VideoBase(unittest.TestCase):
    """Builds the video fixtures once per class."""

    TRIM_S = 24

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="cadence-cap-test-")
        cls.trim_wav = os.path.join(cls.tmp, "trim.wav")
        _run(["ffmpeg", "-y", "-v", "error", "-i", FIXTURE_MP3,
              "-t", str(cls.TRIM_S), "-ac", "1", "-ar", "22050",
              cls.trim_wav])
        cls.in_mp4 = os.path.join(cls.tmp, "in.mp4")
        _run(["ffmpeg", "-y", "-v", "error",
              "-f", "lavfi", "-i",
              f"color=c=0x0d1b2a:s=426x240:r=10:d={cls.TRIM_S}",
              "-i", cls.trim_wav, "-c:v", "libx264",
              "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
              cls.in_mp4])
        cls.in_webm = os.path.join(cls.tmp, "in.webm")
        _run(["ffmpeg", "-y", "-v", "error",
              "-f", "lavfi", "-i",
              f"color=c=0x0d1b2a:s=426x240:r=10:d={cls.TRIM_S}",
              "-i", cls.trim_wav, "-c:v", "libvpx", "-b:v", "200k",
              "-c:a", "libopus", "-shortest", cls.in_webm])
        cls.in_silent = os.path.join(cls.tmp, "silent.mp4")
        _run(["ffmpeg", "-y", "-v", "error",
              "-f", "lavfi", "-i", "color=c=0x0d1b2a:s=426x240:r=10:d=6",
              "-f", "lavfi", "-i",
              "anullsrc=channel_layout=mono:sample_rate=22050",
              "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
              "-shortest", cls.in_silent])

    def _convert(self, inp, name):
        job = os.path.join(self.tmp, name)
        os.makedirs(job)
        out = os.path.join(job, "humanized" + os.path.splitext(inp)[1])
        receipt = humanize.humanize_video(
            inp, out, os.path.join(job, "work"))
        return job, out, receipt


class TestVideoCaptionsMP4(VideoBase):
    def test_embedded_track_sidecars_and_untouched_picture(self):
        job, out, r = self._convert(self.in_mp4, "job-mp4")
        cap = r["captions"]
        self.assertEqual(cap["status"], "ok")
        self.assertTrue(cap["embedded"])
        self.assertGreaterEqual(cap["segments"], 3)
        self.assertEqual(cap["language"], "en")
        # picture provably untouched
        self.assertTrue(r["media"]["video_stream"]["untouched"])
        self.assertEqual(r["media"]["video_stream"]["sha256_before"],
                         r["media"]["video_stream"]["sha256_after"])
        # embedded track present + selectable (mov_text, language eng)
        subs = [s for s in _probe(out) if s["codec_type"] == "subtitle"]
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0]["codec_name"], "mov_text")
        self.assertEqual(subs[0].get("tags", {}).get("language"), "eng")
        # sidecars exist, parse, timings sane, words are the sample's
        srt_path = os.path.join(job, "captions.srt")
        vtt_path = os.path.join(job, "captions.vtt")
        self.assertTrue(os.path.exists(srt_path))
        self.assertTrue(os.path.exists(vtt_path))
        with open(srt_path) as f:
            cues = parse_srt(f.read())
        self.assertEqual(len(cues), cap["segments"])
        starts = [c[0] for c in cues]
        self.assertEqual(starts, sorted(starts))
        dur = humanize.probe_duration(out)
        for start, end, _text in cues:
            self.assertGreater(end, start)
            self.assertLessEqual(end, dur + 0.6)
        self.assertIn("Bitcoin", " ".join(c[2] for c in cues))
        with open(vtt_path) as f:
            self.assertTrue(f.read().startswith("WEBVTT"))


class TestVideoCaptionsWebM(VideoBase):
    def test_sidecar_only_fallback(self):
        job, out, r = self._convert(self.in_webm, "job-webm")
        cap = r["captions"]
        self.assertEqual(cap["status"], "ok")
        self.assertFalse(cap["embedded"])   # documented fallback
        self.assertEqual(cap["sidecars"], ["srt", "vtt"])
        subs = [s for s in _probe(out) if s["codec_type"] == "subtitle"]
        self.assertEqual(subs, [])          # nothing embedded in webm
        self.assertTrue(r["media"]["video_stream"]["untouched"])
        self.assertTrue(os.path.exists(os.path.join(job, "captions.srt")))
        self.assertTrue(os.path.exists(os.path.join(job, "captions.vtt")))


class TestNoSpeech(VideoBase):
    def test_silent_video_named_no_speech(self):
        job, out, r = self._convert(self.in_silent, "job-silent")
        self.assertEqual(r["captions"]["status"], "no-speech")
        self.assertFalse(r["captions"]["embedded"])
        self.assertFalse(os.path.exists(os.path.join(job, "captions.srt")))
        subs = [s for s in _probe(out) if s["codec_type"] == "subtitle"]
        self.assertEqual(subs, [])
        self.assertTrue(r["media"]["video_stream"]["untouched"])


class TestAudioPathUnchanged(unittest.TestCase):
    def test_audio_receipt_has_no_captions(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "humanized.mp3")
            r = humanize.humanize(FIXTURE_MP3, out,
                                  os.path.join(tmp, "work"))
            self.assertNotIn("captions", r)
            self.assertTrue(os.path.exists(out))


if __name__ == "__main__":
    unittest.main()
