#!/usr/bin/env python3
"""Cyan Eclipse Cadence — web humanize chain v1.

Processes a user-supplied AI-generated speech file (mp3/wav/m4a) into a
more human-sounding delivery: pauses reshaped to the Cadence pause
classes (rules-v1: breath 0.35 s, emphasis 0.70 s, act-boundary cap
0.90 s), soft breath cues in long pauses, per-breath-group micro tempo
and level variation inside the rules envelope, de-harshing EQ, gentle
bus compression, and loudness normalization to -16 LUFS / -1.5 dBTP.

No synthesis, no transcription, no external calls. Python stdlib +
ffmpeg only. Design of record: cadence-web-surface-20261114/SPEC.md.
"""
import array
import hashlib
import json
import math
import os
import random
import subprocess
import sys
import wave
from datetime import datetime, timezone

SR = 22050
SILENCE_NOISE_DB = "-38dB"
SILENCE_MIN_DUR = 0.09
MICRO_FLOOR = 0.20

# Cadence rules-v1 pause classes (seconds).
PAUSE_BREATH = 0.35
PAUSE_EMPHASIS = 0.70
PAUSE_ACT_CAP = 0.90
MICRO_MAX = 0.28
BREATH_MIN = 0.28
EMPHASIS_MIN = 0.55
LONG_MIN = 1.20

TEMPO_LO, TEMPO_HI = 0.97, 1.03      # inside rules envelope 0.88-1.12
GAIN_DB = 0.5
BREATH_DB_REL_PEAK = -34.0           # breath cue peak rel. speech peak
TARGET_LUFS = -16.0
TARGET_TP = -1.5

SHARE_LINE = ("Produced with Cyan Eclipse Cadence \u2014 free at "
              "cetech.ai/free-tools")


def _run(cmd):
    return subprocess.run(cmd, check=True, capture_output=True, text=True)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def probe_duration(path):
    out = _run(["ffprobe", "-v", "error", "-show_entries",
                "format=duration", "-of", "json", path]).stdout
    return float(json.loads(out)["format"]["duration"])


def decode(inp, wav_path):
    _run(["ffmpeg", "-y", "-v", "error", "-i", inp,
          "-ac", "1", "-ar", str(SR), wav_path])


def detect_silences(wav_path):
    """Return [(start_s, end_s), ...] from ffmpeg silencedetect."""
    p = subprocess.run(
        ["ffmpeg", "-i", wav_path, "-af",
         f"silencedetect=noise={SILENCE_NOISE_DB}:d={SILENCE_MIN_DUR}",
         "-f", "null", "-"], capture_output=True, text=True)
    sil, start = [], None
    for line in p.stderr.splitlines():
        if "silence_start:" in line:
            start = float(line.split("silence_start:")[1].split()[0])
        elif "silence_end:" in line and start is not None:
            end = float(line.split("silence_end:")[1].split()[0])
            sil.append((start, end))
            start = None
    return sil


def loudness_lufs(path):
    p = subprocess.run(
        ["ffmpeg", "-i", path, "-af", "loudnorm=print_format=json",
         "-f", "null", "-"], capture_output=True, text=True)
    err = p.stderr
    i = err.rfind("[Parsed_loudnorm")
    if i < 0:
        return None, None
    j = json.loads(err[err.index("{", i):err.rindex("}") + 1])
    return float(j["input_i"]), float(j["input_tp"])


def read_pcm(wav_path):
    with wave.open(wav_path, "rb") as w:
        n = w.getnframes()
        raw = w.readframes(n)
    a = array.array("h")
    a.frombytes(raw)
    return a


def write_pcm(wav_path, samples):
    with wave.open(wav_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(samples.tobytes())


def metrics(wav_path):
    dur = probe_duration(wav_path)
    sil = detect_silences(wav_path)
    pcm = read_pcm(wav_path)
    peak = max((abs(s) for s in pcm), default=0) / 32768.0
    pauses = [e - s for s, e in sil if e - s >= SILENCE_MIN_DUR]
    sil_total = sum(pauses)
    n = max(len(pauses), 1)
    lufs, tp = loudness_lufs(wav_path)
    return {
        "duration_s": round(dur, 2),
        "pause_count": len(pauses),
        "pause_mean_s": round(sum(pauses) / n, 3) if pauses else 0.0,
        "pause_micro_share": round(sum(1 for p in pauses if p < MICRO_MAX) / n, 3) if pauses else 0.0,
        "pause_breath_share": round(sum(1 for p in pauses if BREATH_MIN <= p < EMPHASIS_MIN) / n, 3) if pauses else 0.0,
        "pause_emphasis_share": round(sum(1 for p in pauses if EMPHASIS_MIN <= p < LONG_MIN) / n, 3) if pauses else 0.0,
        "pause_long_share": round(sum(1 for p in pauses if p >= LONG_MIN) / n, 3) if pauses else 0.0,
        "voiced_fraction": round(1.0 - sil_total / dur, 3) if dur else 0.0,
        "loudness_lufs": lufs,
        "true_peak_dbtp": tp,
        "peak_dbfs": round(20 * math.log10(peak), 2) if peak > 0 else None,
    }


def _pause_target(dur):
    if dur >= LONG_MIN:
        return PAUSE_ACT_CAP
    if dur >= EMPHASIS_MIN:
        return PAUSE_EMPHASIS
    if dur >= BREATH_MIN:
        return PAUSE_BREATH
    if dur < MICRO_FLOOR:
        # The metronome signature: joins tighter than any human pause
        # (the legacy flat 0.120 s join is the extreme case) are lifted
        # to the 0.20 s floor. Nothing is reclassified upward — without
        # a transcript there is no honest way to know which micro gaps
        # are phrase boundaries.
        return MICRO_FLOOR
    return dur  # natural micro pauses are left exactly as recorded


def _breath_cue(n_samples, peak_lin, rng):
    """Soft band-limited breath burst with an inhale envelope."""
    if n_samples <= 0:
        return array.array("h")
    raw = [rng.gauss(0.0, 1.0) for _ in range(n_samples)]
    # two-pass moving average (cheap lowpass) to take the hiss edge off
    for width in (24, 48):
        acc = 0.0
        out = [0.0] * n_samples
        for i, v in enumerate(raw):
            acc += v
            if i >= width:
                acc -= raw[i - width]
            out[i] = acc / min(i + 1, width)
        raw = out
    peak = max(abs(v) for v in raw) or 1.0
    res = array.array("h")
    for i, v in enumerate(raw):
        t = i / n_samples
        env = math.sin(math.pi * min(t * 1.15, 1.0)) ** 1.5  # inhale swell
        s = int(max(-32767, min(32767, v / peak * peak_lin * env)))
        res.append(s)
    return res


def _resample(samples, factor):
    """Linear-interp resample; factor>1 lengthens (slower), <1 shortens."""
    n = len(samples)
    if n == 0 or abs(factor - 1.0) < 1e-4:
        return array.array("h", samples)
    m = max(1, int(n * factor))
    out = array.array("h", bytes(2 * m))
    for i in range(m):
        x = i / factor
        j = int(x)
        frac = x - j
        a = samples[j]
        b = samples[j + 1] if j + 1 < n else a
        out[i] = int(a + (b - a) * frac)
    return out


def reshape(pcm, sil, rng, mode="audio"):
    total = len(pcm)
    speech_peak = max((abs(s) for s in pcm), default=0) / 32768.0
    breath_peak_lin = speech_peak * (10 ** (BREATH_DB_REL_PEAK / 20.0)) * 32767
    # speech spans between silence runs (samples)
    spans = []  # (kind, start, end) kind in {"speech","silence"}
    cur = 0
    for s, e in sil:
        ss, ee = int(s * SR), min(int(e * SR), total)
        if ss > cur:
            spans.append(("speech", cur, ss))
        if ee > ss:
            spans.append(("silence", ss, ee))
        cur = max(cur, ee)
    if cur < total:
        spans.append(("speech", cur, total))
    if not spans:
        spans = [("speech", 0, total)]

    out = array.array("h")
    tempo = 1.0
    gain_db = 0.0
    for idx, (kind, a, b) in enumerate(spans):
        seg = pcm[a:b]
        if kind == "speech":
            tempo = max(TEMPO_LO, min(TEMPO_HI, tempo + rng.uniform(-0.012, 0.012)))
            gain_db = max(-GAIN_DB, min(GAIN_DB, gain_db + rng.uniform(-0.25, 0.25)))
            seg = _resample(seg, 1.0 / tempo)
            g = 10 ** (gain_db / 20.0)
            if abs(g - 1.0) > 1e-3:
                seg = array.array("h", (max(-32767, min(32767, int(v * g))) for v in seg))
            out.extend(seg)
        else:
            dur = (b - a) / SR
            target = _pause_target(dur)
            if mode == "video":
                # Onset-lock (SPEC section 10): pauses take their class
                # targets, but cumulative drift vs the picture timeline
                # is bounded to +/-0.35 s and the file ends exactly at
                # the original duration.
                if idx == len(spans) - 1:
                    n_keep = max(int(0.05 * SR), total - len(out))
                else:
                    onset_next = spans[idx + 1][1]  # next speech onset
                    room = (onset_next - len(out)) / SR
                    lo = max(0.08, room - 0.35)
                    hi = room + 0.35
                    n_keep = int(min(hi, max(lo, target)) * SR)
            else:
                if idx == 0:
                    target = min(target, 0.15)   # no long lead-in
                if idx == len(spans) - 1:
                    target = min(target, 0.25)   # no long tail
                n_keep = int(target * SR)
            pause = array.array("h", bytes(2 * n_keep))
            if target >= EMPHASIS_MIN - 1e-9 and n_keep > SR * 0.3:
                cue_n = int(min(0.30, (n_keep / SR) * 0.45) * SR)
                cue = _breath_cue(cue_n, breath_peak_lin, rng)
                pause[n_keep - cue_n:] = cue     # breath leads the phrase
            out.extend(pause)
    return out


def humanize(inp_path, out_mp3_path, workdir, mode="audio"):
    os.makedirs(workdir, exist_ok=True)
    in_wav = os.path.join(workdir, "in.wav")
    shaped = os.path.join(workdir, "shaped.wav")
    decode(inp_path, in_wav)
    before = metrics(in_wav)
    pcm = read_pcm(in_wav)
    sil = detect_silences(in_wav)
    seed = int(hashlib.sha256(open(inp_path, "rb").read()).hexdigest()[:8], 16)
    rng = random.Random(seed)
    write_pcm(shaped, reshape(pcm, sil, rng, mode=mode))
    _run(["ffmpeg", "-y", "-v", "error", "-i", shaped, "-af",
          "equalizer=f=3000:t=q:w=1.2:g=-2,"
          "highshelf=f=9000:g=-1.5,"
          "acompressor=threshold=-18dB:ratio=2:attack=20:release=250,"
          f"loudnorm=I={TARGET_LUFS}:TP={TARGET_TP}:LRA=11",
          "-ar", "44100", "-ac", "1", "-b:a", "192k", out_mp3_path])
    out_wav = os.path.join(workdir, "out.wav")
    decode(out_mp3_path, out_wav)
    after = metrics(out_wav)
    receipt = {
        "receipt": "cadence-web-humanize/v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input": {"sha256": _sha256(inp_path),
                  "bytes": os.path.getsize(inp_path),
                  "duration_s": before["duration_s"]},
        "output": {"sha256": _sha256(out_mp3_path),
                   "bytes": os.path.getsize(out_mp3_path),
                   "duration_s": after["duration_s"]},
        "chain": {
            "pause_classes_s": {"breath": PAUSE_BREATH,
                                "emphasis": PAUSE_EMPHASIS,
                                "act_boundary_cap": PAUSE_ACT_CAP,
                                "micro_floor": MICRO_FLOOR},
            "tempo_walk": [TEMPO_LO, TEMPO_HI],
            "gain_walk_db": GAIN_DB,
            "breath_cue_db_rel_peak": BREATH_DB_REL_PEAK,
            "eq": "3kHz -2dB (Q1.2), 9kHz highshelf -1.5dB",
            "loudness_target": {"lufs": TARGET_LUFS, "true_peak": TARGET_TP},
        },
        "metrics": {"before": before, "after": after},
        "share_line": SHARE_LINE,
    }
    return receipt


class Refusal(Exception):
    """A plain-language refusal the service may show the visitor."""


def probe_streams(path):
    out = _run(["ffprobe", "-v", "error", "-show_entries",
                "stream=codec_type,codec_name,width,height,nb_frames",
                "-of", "json", path]).stdout
    return json.loads(out).get("streams", [])


def video_stream_hash(path):
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-map", "0:v:0",
         "-c", "copy", "-f", "hash", "-hash", "sha256", "-"],
        capture_output=True, text=True)
    line = (p.stdout or "").strip()
    return line.split("=", 1)[1] if "=" in line else None


def humanize_video(inp_path, out_path, workdir):
    """Same chain, one more container (SPEC section 10): extract the
    audio track, humanize it, lay it back with the picture stream
    copied untouched."""
    os.makedirs(workdir, exist_ok=True)
    streams = probe_streams(inp_path)
    vids = [s for s in streams if s["codec_type"] == "video"]
    auds = [s for s in streams if s["codec_type"] == "audio"]
    if not vids:
        raise Refusal("That file has no picture stream we can read.")
    if not auds:
        raise Refusal("That video has no voice track to humanize.")
    ext = os.path.splitext(out_path)[1].lower()
    extracted = os.path.join(workdir, "track.wav")
    _run(["ffmpeg", "-y", "-v", "error", "-i", inp_path,
          "-map", "0:a:0", "-ac", "1", "-ar", str(SR), extracted])
    human_mp3 = os.path.join(workdir, "humanized.mp3")
    receipt = humanize(extracted, human_mp3,
                       os.path.join(workdir, "audio-work"), mode="video")
    vhash_before = video_stream_hash(inp_path)
    acodec = (["-c:a", "libopus", "-b:a", "128k"] if ext == ".webm"
              else ["-c:a", "aac", "-b:a", "192k"])
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", inp_path,
           "-i", human_mp3, "-map", "0:v:0", "-map", "1:a:0",
           "-c:v", "copy"] + acodec + [out_path]
    _run(cmd)
    vhash_after = video_stream_hash(out_path)
    v = vids[0]
    receipt["media"] = {
        "kind": "video",
        "container": ext.lstrip("."),
        "video_stream": {
            "codec": v.get("codec_name"),
            "width": v.get("width"),
            "height": v.get("height"),
            "sha256_before": vhash_before,
            "sha256_after": vhash_after,
            "untouched": (vhash_before is not None
                          and vhash_before == vhash_after),
        },
    }
    receipt["receipt"] = "cadence-web-humanize/v1+video"
    receipt["output"]["sha256"] = _sha256(out_path)
    receipt["output"]["bytes"] = os.path.getsize(out_path)
    receipt["output"]["duration_s"] = round(probe_duration(out_path), 2)
    return receipt


if __name__ == "__main__":
    inp, outp, work = sys.argv[1], sys.argv[2], sys.argv[3]
    r = humanize(inp, outp, work)
    print(json.dumps(r, indent=1))
