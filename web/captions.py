#!/usr/bin/env python3
"""Cyan Eclipse Cadence — web captions bridge (stdlib only).

Owns: the transcription subprocess call (the whisper engine lives in
transcribe.py under its own venv — the service process never imports
it), segment hygiene (whisper segment ends can overshoot the media
duration; segments are clamped to it, never extended), and the SRT /
WebVTT renderings. Design: cadence-captions-20261114/STATE.md.
"""
import json
import os
import subprocess

WHISPER_PYTHON = os.environ.get(
    "CADENCE_WHISPER_PYTHON", "/opt/cadence-whisper-venv/bin/python")
TRANSCRIBE_SCRIPT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "transcribe.py")

# Measured on the service host (Shard E, 2 cores, faster-whisper base
# int8): ~0.26x realtime including model load — a capped 180 s video
# transcribes in ~60 s worst case. 600 s is ~10x headroom.
# COORDINATION NOTE (hardening lane): this call does NOT go through
# humanize._run — it carries its own timeout here. Any global
# subprocess budget placed on the video path must sit ABOVE ~60 s of
# transcription plus the mux, or captions will be cut off mid-job.
TRANSCRIBE_TIMEOUT_S = 600


class CaptionError(Exception):
    """Transcription failed; the conversion itself still succeeds."""


def transcribe(audio_path, workdir):
    """Transcribe an audio file; return the engine's JSON dict with
    keys language, language_probability, model, segments[]."""
    out_json = os.path.join(workdir, "transcript.json")
    env = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    try:
        p = subprocess.run(
            [WHISPER_PYTHON, TRANSCRIBE_SCRIPT, audio_path, out_json],
            capture_output=True, text=True,
            timeout=TRANSCRIBE_TIMEOUT_S, env=env)
    except subprocess.TimeoutExpired:
        raise CaptionError("transcription timed out")
    except OSError as e:
        raise CaptionError(f"transcription engine unavailable: "
                           f"{type(e).__name__}")
    if p.returncode != 0 or not os.path.exists(out_json):
        raise CaptionError(f"transcription engine failed "
                           f"(rc {p.returncode})")
    with open(out_json) as f:
        return json.load(f)


def clean_segments(segments, duration_s):
    """Strip/collapse text, drop empties, clamp ends to the media
    duration, drop segments that start beyond it. Timings come from
    the humanized audio; nothing is invented or extended."""
    out = []
    for s in segments:
        text = " ".join((s.get("text") or "").split())
        if not text:
            continue
        start = max(0.0, float(s["start"]))
        end = float(s["end"])
        if duration_s:
            if start >= duration_s:
                continue
            end = min(end, duration_s)
        if end <= start:
            continue
        out.append({"start": round(start, 3), "end": round(end, 3),
                    "text": text})
    out.sort(key=lambda s: s["start"])
    return out


def _ts(t, sep):
    ms = int(round(t * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def to_srt(segments):
    blocks = []
    for i, s in enumerate(segments, 1):
        blocks.append(f"{i}\n{_ts(s['start'], ',')} --> "
                      f"{_ts(s['end'], ',')}\n{s['text']}")
    return "\n\n".join(blocks) + "\n"


def to_vtt(segments):
    blocks = ["WEBVTT"]
    for s in segments:
        blocks.append(f"{_ts(s['start'], '.')} --> "
                      f"{_ts(s['end'], '.')}\n{s['text']}")
    return "\n\n".join(blocks) + "\n"
