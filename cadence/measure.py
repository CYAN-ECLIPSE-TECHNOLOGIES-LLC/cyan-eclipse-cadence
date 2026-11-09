"""Prosody measurer (M3): pauses, F0, rate, loudness dynamics for a render.

Runtime deps are lazy: numpy is used when importable (VM venv); otherwise a
pure-stdlib fallback path runs (slower, decimated F0) so the module stays
importable and testable on the droplet, which has neither numpy nor ffmpeg.
ffmpeg is used when present for decode + silencedetect; otherwise WAV input
is read with the stdlib wave module and pauses come from frame-energy
silence detection with the same -40 dB / 0.08 s parameters (labelled).
"""
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import wave

TARGET_SR = 48000
SILENCE_DB = -40.0
SILENCE_MIN_S = 0.08

PAUSE_MICRO_MAX = 0.25
PAUSE_BREATH_MAX = 0.55


def classify_pause(dur_s):
    """micro <0.25 s · breath 0.25-0.55 s · emphasis >0.55 s."""
    if dur_s < PAUSE_MICRO_MAX:
        return "micro"
    if dur_s <= PAUSE_BREATH_MAX:
        return "breath"
    return "emphasis"


def pause_distribution(pauses):
    counts = {"micro": 0, "breath": 0, "emphasis": 0}
    for p in pauses:
        counts[p["class"]] += 1
    total = len(pauses)
    return {
        cls: {"count": n, "share": (n / total if total else 0.0)}
        for cls, n in counts.items()
    }


def _words(text):
    return len([w for w in re.split(r"\s+", (text or "").strip()) if w])


# ---------------------------------------------------------------- audio IO

def _read_wav_stdlib(path):
    """Return (samples list[float] in [-1,1], sample_rate) for PCM WAV."""
    with wave.open(path, "rb") as wf:
        nch, width, sr, nframes = (wf.getnchannels(), wf.getsampwidth(),
                                   wf.getframerate(), wf.getnframes())
        raw = wf.readframes(nframes)
    if width == 2:
        import array
        a = array.array("h")
        a.frombytes(raw)
        if nch > 1:
            a = a[::nch]
        return [s / 32768.0 for s in a], sr
    if width == 1:
        vals = [(b - 128) / 128.0 for b in raw]
        return (vals[::nch] if nch > 1 else vals), sr
    raise ValueError("unsupported WAV sample width %d" % width)


def _decode(path):
    """Decode to (samples, sr, notes). Prefers ffmpeg->48k mono when the
    input is not already a readable WAV; falls back to stdlib WAV reads."""
    notes = []
    is_wav = path.lower().endswith(".wav")
    if is_wav:
        try:
            samples, sr = _read_wav_stdlib(path)
            if sr == TARGET_SR:
                return samples, sr, notes
            if not shutil.which("ffmpeg"):
                notes.append("ffmpeg absent: analysed at native rate %d Hz" % sr)
                return samples, sr, notes
        except Exception:
            pass
    if shutil.which("ffmpeg"):
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.close()
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-i", path, "-ac", "1",
                 "-ar", str(TARGET_SR), "-sample_fmt", "s16", tmp.name],
                check=True)
            samples, sr = _read_wav_stdlib(tmp.name)
            return samples, sr, notes
        finally:
            os.unlink(tmp.name)
    raise RuntimeError("cannot decode %s: not a readable WAV and no ffmpeg" % path)


def _to_numpy(samples):
    import numpy as np
    return np.asarray(samples, dtype=np.float64)


# ---------------------------------------------------------------- pauses

_SIL_START = re.compile(r"silence_start:\s*([0-9.]+)")
_SIL_END = re.compile(r"silence_end:\s*([0-9.]+)")


def _pauses_ffmpeg(path, duration_s):
    proc = subprocess.run(
        ["ffmpeg", "-i", path, "-af",
         "silencedetect=noise=%gdB:d=%g" % (SILENCE_DB, SILENCE_MIN_S),
         "-f", "null", "-"],
        capture_output=True, text=True)
    starts, pauses = [], []
    for line in proc.stderr.splitlines():
        m = _SIL_START.search(line)
        if m:
            starts.append(float(m.group(1)))
        m = _SIL_END.search(line)
        if m and starts:
            end = float(m.group(1))
            start = starts.pop(0)
            pauses.append({"start": round(start, 3),
                           "dur": round(end - start, 3)})
    for start in starts:  # trailing silence runs to EOF
        pauses.append({"start": round(start, 3),
                       "dur": round(duration_s - start, 3)})
    return [p for p in pauses if p["dur"] >= SILENCE_MIN_S]


def _pauses_energy(samples, sr):
    """Stdlib/numpy fallback: 10 ms frames under -40 dBFS, runs >= 0.08 s."""
    frame = max(1, int(sr * 0.01))
    pauses, run_start = [], None
    nframes = len(samples) // frame
    for i in range(nframes):
        seg = samples[i * frame:(i + 1) * frame]
        rms = math.sqrt(sum(x * x for x in seg) / len(seg)) if seg else 0.0
        db = 20 * math.log10(rms) if rms > 0 else -120.0
        silent = db < SILENCE_DB
        t = i * frame / sr
        if silent and run_start is None:
            run_start = t
        elif not silent and run_start is not None:
            dur = t - run_start
            if dur >= SILENCE_MIN_S:
                pauses.append({"start": round(run_start, 3), "dur": round(dur, 3)})
            run_start = None
    if run_start is not None:
        dur = len(samples) / sr - run_start
        if dur >= SILENCE_MIN_S:
            pauses.append({"start": round(run_start, 3), "dur": round(dur, 3)})
    return pauses


# ---------------------------------------------------------------- F0

def _f0_autocorr_frame(frame, sr, fmin=50.0, fmax=400.0, thresh=0.35):
    """Normalised-autocorrelation pitch for one frame (list[float]).
    Returns Hz or None. Pure python; callers decimate long frames."""
    n = len(frame)
    energy = sum(x * x for x in frame)
    if energy <= 1e-9:
        return None
    lo = max(1, int(sr / fmax))
    hi = min(n - 1, int(sr / fmin))
    best_lag, best_val = None, -1.0
    for lag in range(lo, hi + 1):
        s = 0.0
        for i in range(n - lag):
            s += frame[i] * frame[i + lag]
        val = s / energy
        if val > best_val:
            best_val, best_lag = val, lag
    if best_lag is None or best_val < thresh:
        return None
    return sr / best_lag


def track_f0(samples, sr):
    """Frame 40 ms, hop 20 ms. Returns [(t_center, hz|None), ...].
    Uses numpy when available; else decimates x4 and runs stdlib autocorr."""
    frame_len = int(sr * 0.04)
    hop = int(sr * 0.02)
    out = []
    try:
        import numpy as np
        x = _to_numpy(samples)
        lo = max(1, int(sr / 400.0))
        hi = min(frame_len - 1, int(sr / 50.0))
        for start in range(0, max(0, len(x) - frame_len + 1), hop):
            fr = x[start:start + frame_len]
            if float(np.sqrt(np.mean(fr * fr))) < 0.003:
                out.append((start / sr, None))
                continue
            fr = fr - fr.mean()
            ac = np.correlate(fr, fr, mode="full")[frame_len - 1:]
            if ac[0] <= 0:
                out.append((start / sr, None))
                continue
            acn = ac / ac[0]
            seg = acn[lo:hi + 1]
            lag = lo + int(np.argmax(seg))
            out.append((start / sr, sr / lag if seg.max() >= 0.35 else None))
        return out
    except ImportError:
        pass
    factor = 4 if sr >= 32000 else 1
    if factor > 1:
        ds = []
        for i in range(0, len(samples) - factor + 1, factor):
            ds.append(sum(samples[i:i + factor]) / factor)
        samples, sr = ds, sr // factor
        frame_len = int(sr * 0.04)
        hop = int(sr * 0.02)
    for start in range(0, max(0, len(samples) - frame_len + 1), hop):
        fr = samples[start:start + frame_len]
        rms = math.sqrt(sum(v * v for v in fr) / len(fr)) if fr else 0.0
        if rms < 0.003:
            out.append((start / sr, None))
            continue
        mean = sum(fr) / len(fr)
        out.append((start / sr, _f0_autocorr_frame([v - mean for v in fr], sr)))
    return out


def _sd_semitones(freqs):
    if len(freqs) < 2:
        return None
    med = sorted(freqs)[len(freqs) // 2]
    if med <= 0:
        return None
    vals = [12.0 * math.log2(f / med) for f in freqs if f > 0]
    mean = sum(vals) / len(vals)
    return math.sqrt(sum((v - mean) ** 2 for v in vals) / len(vals))


def _percentile(vals, q):
    if not vals:
        return None
    s = sorted(vals)
    idx = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[idx]


def _cv(vals):
    if len(vals) < 2:
        return None
    mean = sum(vals) / len(vals)
    if mean == 0:
        return None
    var = sum((v - mean) ** 2 for v in vals) / len(vals)
    return math.sqrt(var) / mean


def _rms_db(samples, sr, start_s, end_s):
    a = samples[int(start_s * sr):int(end_s * sr)]
    if not a:
        return None
    rms = math.sqrt(sum(x * x for x in a) / len(a))
    return round(20 * math.log10(rms), 2) if rms > 0 else -120.0


# ---------------------------------------------------------------- spans

def load_timings(path):
    """Normalise timings JSON to [{span_id, start, end}] sorted by start.
    Accepts {spans:[...]}, a bare list, or {span_id: {start,end}}; field
    aliases start_s/start_sec/begin and end_s/end_sec, dur_s/duration."""
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    rows = data.get("spans", data) if isinstance(data, dict) else data
    out = []
    if isinstance(rows, dict):
        rows = [dict(v, span_id=k) for k, v in rows.items()]
    for r in rows:
        start = r.get("start", r.get("start_s", r.get("start_sec", r.get("begin"))))
        end = r.get("end", r.get("end_s", r.get("end_sec")))
        if end is None and start is not None:
            dur = r.get("dur_s", r.get("duration", r.get("dur")))
            end = start + dur if dur is not None else None
        if start is None or end is None:
            continue
        out.append({"span_id": r.get("span_id"), "start": float(start),
                    "end": float(end)})
    return sorted(out, key=lambda r: r["start"])


def load_score(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _chunks_from_pauses(pauses, duration_s):
    """Silence-delimited chunks split at pauses >= 0.25 s."""
    bounds = [0.0]
    for p in pauses:
        if p["dur"] >= 0.25:
            bounds.append(p["start"])
            bounds.append(p["start"] + p["dur"])
    bounds.append(duration_s)
    chunks = []
    for i in range(0, len(bounds) - 1, 2):
        a, b = bounds[i], bounds[i + 1]
        if b - a >= 0.05:
            chunks.append({"start": a, "end": b, "dur": b - a})
    return chunks


# ---------------------------------------------------------------- measure

def measure(audio_path, timings_path=None, score_path=None):
    samples, sr, notes = _decode(audio_path)
    duration_s = len(samples) / sr
    if shutil.which("ffmpeg") and audio_path.lower().endswith((".wav", ".mp3", ".m4a", ".ogg")):
        try:
            pauses = _pauses_ffmpeg(audio_path, duration_s)
            pause_source = "ffmpeg-silencedetect"
        except Exception:
            pauses = _pauses_energy(samples, sr)
            pause_source = "energy-fallback"
    else:
        pauses = _pauses_energy(samples, sr)
        pause_source = "energy-fallback"
    for p in pauses:
        p["class"] = classify_pause(p["dur"])
    durs = [p["dur"] for p in pauses]

    frames = track_f0(samples, sr)
    voiced = [f for _, f in frames if f]
    f0_block = {
        "mean_hz": round(sum(voiced) / len(voiced), 2) if voiced else None,
        "median_hz": round(sorted(voiced)[len(voiced) // 2], 2) if voiced else None,
        "sd_semitones": (round(_sd_semitones(voiced), 3)
                         if _sd_semitones(voiced) is not None else None),
        "voiced_fraction": round(len(voiced) / len(frames), 3) if frames else 0.0,
        "per_act": None,
    }

    score = load_score(score_path) if score_path else None
    timings = load_timings(timings_path) if timings_path else None
    span_rows, per_span_wpm = [], []
    act_freqs = {}
    if timings:
        score_by_id = {}
        if score:
            score_by_id = {s["span_id"]: s for s in score.get("spans", [])}
        for i, t in enumerate(timings):
            nxt = timings[i + 1]["start"] if i + 1 < len(timings) else duration_s
            span_score = score_by_id.get(t["span_id"], {})
            words = _words(span_score.get("text_raw", "")) if span_score else 0
            dur = t["end"] - t["start"]
            wpm = (words / dur * 60.0) if words and dur > 0 else None
            if wpm:
                per_span_wpm.append(wpm)
            f0s = [f for tc, f in frames
                   if t["start"] <= tc < t["end"] and f]
            act = span_score.get("act")
            if act:
                act_freqs.setdefault(act, []).extend(f0s)
            span_rows.append({
                "span_id": t["span_id"], "act": act,
                "start_s": round(t["start"], 3), "end_s": round(t["end"], 3),
                "dur_s": round(dur, 3),
                "pause_after_s": round(max(0.0, nxt - t["end"]), 3),
                "words": words, "wpm": round(wpm, 1) if wpm else None,
                "rms_db": _rms_db(samples, sr, t["start"], t["end"]),
                "f0_sd_semitones": (round(_sd_semitones(f0s), 3)
                                    if _sd_semitones(f0s) is not None else None),
            })
        if act_freqs:
            f0_block["per_act"] = {
                act: {"sd_semitones": (round(_sd_semitones(fs), 3)
                                       if _sd_semitones(fs) is not None else None),
                      "mean_hz": round(sum(fs) / len(fs), 2) if fs else None}
                for act, fs in act_freqs.items()
            }

    chunks = _chunks_from_pauses(pauses, duration_s)
    chunk_durs = [c["dur"] for c in chunks]
    chunk_rms = [_rms_db(samples, sr, c["start"], c["end"]) for c in chunks]
    chunk_rms = [v for v in chunk_rms if v is not None]

    total_words = None
    if score:
        total_words = sum(_words(s.get("text_raw", ""))
                          for s in score.get("spans", []))
    overall_wpm = (round(total_words / duration_s * 60.0, 1)
                   if total_words and duration_s > 0 else None)

    if span_rows and per_span_wpm:
        rate_basis, local_cv = "spans", _cv(per_span_wpm)
        rms_vals = [r["rms_db"] for r in span_rows if r["rms_db"] is not None]
    else:
        rate_basis, local_cv = "chunks", _cv(chunk_durs)
        rms_vals = chunk_rms
    p90, p10 = _percentile(rms_vals, 0.9), _percentile(rms_vals, 0.1)
    dyn = round(p90 - p10, 2) if p90 is not None and p10 is not None else None

    return {
        "schema": "cadence-metrics-v1",
        "audio": os.path.basename(audio_path),
        "duration_s": round(duration_s, 3),
        "sample_rate": sr,
        "pause_source": pause_source,
        "pauses": {
            "list": pauses,
            "count": len(pauses),
            "mean_s": round(sum(durs) / len(durs), 3) if durs else None,
            "max_s": round(max(durs), 3) if durs else None,
            "distribution": pause_distribution(pauses),
        },
        "f0": f0_block,
        "rate": {
            "overall_wpm": overall_wpm,
            "total_words": total_words,
            "local_cv": round(local_cv, 4) if local_cv is not None else None,
            "rate_cv_basis": rate_basis,
            "per_span_wpm": [r["wpm"] for r in span_rows] or None,
            "chunk_cv": (round(_cv(chunk_durs), 4)
                         if _cv(chunk_durs) is not None else None),
            "chunk_count": len(chunks),
        },
        "loudness": {
            "dynamic_range_db": dyn,
            "basis": rate_basis,
            "per_unit_rms_db": rms_vals,
            "chunk_dynamic_range_db": (
                round(_percentile(chunk_rms, 0.9) - _percentile(chunk_rms, 0.1), 2)
                if chunk_rms else None),
        },
        "spans": span_rows or None,
        "chunks": [{"start_s": round(c["start"], 3), "end_s": round(c["end"], 3),
                    "dur_s": round(c["dur"], 3)} for c in chunks],
        "notes": notes,
    }


# ---------------------------------------------------------------- bands gate

def evaluate_bands(metrics, bands):
    """Per-metric in/out against bands['metrics'] = {key: [lo, hi]}.
    Like-for-like: rate CV always compared on the chunk basis."""
    table = {}

    def check(key, value):
        rng = bands.get("metrics", {}).get(key)
        if rng is None or value is None:
            table[key] = {"value": value, "band": rng, "in_band": None}
            return
        table[key] = {"value": value, "band": rng,
                      "in_band": bool(rng[0] <= value <= rng[1])}

    check("f0_mean_hz", metrics["f0"]["mean_hz"])
    check("f0_sd_semitones", metrics["f0"]["sd_semitones"])
    check("voiced_fraction", metrics["f0"]["voiced_fraction"])
    check("pause_mean_s", metrics["pauses"]["mean_s"])
    check("pause_max_s", metrics["pauses"]["max_s"])
    for cls in ("micro", "breath", "emphasis"):
        check("pause_%s_share" % cls,
              metrics["pauses"]["distribution"][cls]["share"])
    check("rate_overall_wpm", metrics["rate"]["overall_wpm"])
    check("rate_cv_chunks", metrics["rate"]["chunk_cv"])
    check("dynamic_range_db", metrics["loudness"]["chunk_dynamic_range_db"])
    judged = [v for v in table.values() if v["in_band"] is not None]
    in_n = sum(1 for v in judged if v["in_band"])
    return {
        "metrics": table,
        "in_band_count": in_n,
        "judged_count": len(judged),
        "in_band_pct": round(100.0 * in_n / len(judged), 1) if judged else None,
    }
