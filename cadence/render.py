"""Renderer (M2): performance score -> joined wav + timings + render log.

Per span, in order:
  synth (Helio adapter, call text = span text_encoded)
  -> decode/normalise to 48 kHz mono wav
  -> PER-SPAN tempo via ffmpeg atempo=<span.tempo> (per-unit tempo is
     the whole point of R11; a global atempo pass over the joined file
     is FORBIDDEN - the legacy pipeline's F1 defect)
  -> per-span level from the intensity map (R4/R12):
     reduced -> 0.79 (-2 dB) · climax-quiet -> 0.71 (-3 dB) · normal -> 1.0
  -> silence of span.pause_after.seconds appended (skipped after the
     final span; classes from the rule set - the flat 0.12 s join of
     the legacy pipeline appears nowhere)
  -> concat of all parts (same-format wavs) -> out wav, 48 kHz mono.

Emits beside the output:
  <out>.timings.json   [{span_id, segment, act, start_s, end_s,
                         pause_after_s}] measured from the actual
                         assembled parts
  <out>.renderlog.json per-span synth route / raw dur / tempo / out
                         dur, plus joined duration vs expected.

ffmpeg is the primary audio path. A stdlib-only fallback (wave module;
tempo by linear resampling, which is duration-exact) covers hosts
without ffmpeg - notably the build droplet, where the unit tests run
with synthesis stubbed. Both paths produce identical durations.
"""
import array
import json
import math
import os
import shutil
import subprocess
import wave

from . import helio

SAMPLE_RATE = 48000
INTENSITY_GAIN = {"reduced": 0.79, "climax-quiet": 0.71, "normal": 1.0}


def _ffmpeg():
    return shutil.which("ffmpeg")


def wav_duration(path):
    with wave.open(path, "rb") as w:
        return w.getnframes() / float(w.getframerate())


def _run(argv):
    proc = subprocess.run(argv, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("command failed %s: %s"
                           % (argv[0], proc.stderr[-400:]))
    return proc


# --- stdlib fallback primitives (16-bit PCM wav) ---------------------------

def _read_samples(path):
    with wave.open(path, "rb") as w:
        params = (w.getnchannels(), w.getsampwidth(), w.getframerate())
        frames = w.readframes(w.getnframes())
    if params[1] != 2:
        raise RuntimeError("stdlib fallback needs 16-bit PCM, got %r"
                           % (params,))
    samples = array.array("h")
    samples.frombytes(frames)
    if params[0] == 2:  # downmix to mono
        mono = array.array("h", bytes(2 * (len(samples) // 2)))
        for i in range(0, len(samples) - 1, 2):
            mono[i // 2] = int((samples[i] + samples[i + 1]) / 2)
        samples = mono
    elif params[0] != 1:
        raise RuntimeError("unsupported channel count %r" % (params,))
    return samples, params[2]


def _write_samples(path, samples, rate=SAMPLE_RATE):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())


def _resample(samples, src_rate, dst_len):
    n = len(samples)
    if n == 0 or dst_len <= 0:
        return array.array("h")
    out = array.array("h", bytes(2 * dst_len))
    for i in range(dst_len):
        pos = i * (n - 1) / max(1, dst_len - 1) if dst_len > 1 else 0.0
        lo = int(math.floor(pos))
        hi = min(lo + 1, n - 1)
        frac = pos - lo
        out[i] = int(samples[lo] * (1.0 - frac) + samples[hi] * frac)
    return out


# --- per-span processing ----------------------------------------------------

def _normalise(raw_path, norm_path):
    if _ffmpeg():
        _run(["ffmpeg", "-y", "-v", "error", "-i", raw_path,
              "-ar", str(SAMPLE_RATE), "-ac", "1",
              "-c:a", "pcm_s16le", norm_path])
    else:
        samples, rate = _read_samples(raw_path)
        if rate != SAMPLE_RATE:
            samples = _resample(samples, rate,
                                int(round(len(samples) * SAMPLE_RATE / rate)))
        _write_samples(norm_path, samples)
    return norm_path


def _tempo_gain(norm_path, proc_path, tempo, gain):
    if _ffmpeg():
        _run(["ffmpeg", "-y", "-v", "error", "-i", norm_path,
              "-af", "atempo=%.4f,volume=%.4f" % (tempo, gain),
              "-ar", str(SAMPLE_RATE), "-ac", "1",
              "-c:a", "pcm_s16le", proc_path])
    else:
        samples, rate = _read_samples(norm_path)
        out = _resample(samples, rate, int(round(len(samples) / tempo)))
        if gain != 1.0:
            for i in range(len(out)):
                v = int(out[i] * gain)
                out[i] = max(-32768, min(32767, v))
        _write_samples(proc_path, out)
    return proc_path


def _silence(path, seconds):
    n = int(round(SAMPLE_RATE * seconds))
    _write_samples(path, array.array("h", bytes(2 * n)))
    return path


def _concat(parts, out_path):
    if _ffmpeg():
        list_path = out_path + ".concat.txt"
        with open(list_path, "w", encoding="utf-8") as fh:
            for part in parts:
                fh.write("file '%s'\n" % os.path.abspath(part))
        try:
            _run(["ffmpeg", "-y", "-v", "error", "-f", "concat",
                  "-safe", "0", "-i", list_path,
                  "-c:a", "pcm_s16le", out_path])
        finally:
            os.unlink(list_path)
    else:
        out = array.array("h")
        for part in parts:
            samples, _rate = _read_samples(part)
            out.extend(samples)
        _write_samples(out_path, out)
    return out_path


# --- render -----------------------------------------------------------------

def render_score(score, out_path, workdir):
    os.makedirs(workdir, exist_ok=True)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    spans = score["spans"]
    parts, timings, log = [], [], []
    silences = {}
    clock = 0.0
    for idx, span in enumerate(spans):
        sid = span["span_id"]
        raw = os.path.join(workdir, sid + ".raw.wav")
        norm = os.path.join(workdir, sid + ".norm.wav")
        proc = os.path.join(workdir, sid + ".proc.wav")
        info = helio.synth_span(span["text_encoded"], raw)
        route = info.get("route", "unknown") if isinstance(info, dict) \
            else "unknown"
        _normalise(raw, norm)
        raw_dur = wav_duration(norm)
        gain = INTENSITY_GAIN[span["intensity"]]
        _tempo_gain(norm, proc, float(span["tempo"]), gain)
        out_dur = wav_duration(proc)
        is_final = idx == len(spans) - 1
        pause = 0.0 if is_final else float(span["pause_after"]["seconds"])
        timings.append({
            "span_id": sid,
            "segment": span["segment"],
            "act": span["act"],
            "start_s": round(clock, 3),
            "end_s": round(clock + out_dur, 3),
            "pause_after_s": pause,
        })
        log.append({
            "span_id": sid,
            "synth_route": route,
            "raw_dur_s": round(raw_dur, 3),
            "tempo": float(span["tempo"]),
            "intensity": span["intensity"],
            "gain": gain,
            "out_dur_s": round(out_dur, 3),
            "pause_after_s": pause,
        })
        parts.append(proc)
        if pause > 0:
            if pause not in silences:
                silences[pause] = _silence(
                    os.path.join(workdir, "silence-%s.wav" % pause), pause)
            parts.append(silences[pause])
        clock += out_dur + pause
    _concat(parts, out_path)
    joined_dur = wav_duration(out_path)
    timings_path = out_path + ".timings.json"
    with open(timings_path, "w", encoding="utf-8") as fh:
        json.dump(timings, fh, indent=2)
        fh.write("\n")
    renderlog = {
        "out": os.path.basename(out_path),
        "duration_s": round(joined_dur, 3),
        "expected_duration_s": round(clock, 3),
        "sample_rate": SAMPLE_RATE,
        "channels": 1,
        "spans": log,
    }
    renderlog_path = out_path + ".renderlog.json"
    with open(renderlog_path, "w", encoding="utf-8") as fh:
        json.dump(renderlog, fh, indent=2)
        fh.write("\n")
    return {"out": out_path, "duration_s": joined_dur,
            "expected_duration_s": clock, "timings": timings_path,
            "renderlog": renderlog_path, "spans": len(spans)}


def render_file(score_path, out_path, workdir):
    with open(score_path, encoding="utf-8") as fh:
        score = json.load(fh)
    return render_score(score, out_path, workdir)
