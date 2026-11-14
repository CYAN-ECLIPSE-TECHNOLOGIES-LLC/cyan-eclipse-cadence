#!/usr/bin/env python3
"""Cyan Eclipse Cadence — web captions transcription worker.

Runs ONLY as a subprocess under the captions venv
(/opt/cadence-whisper-venv); the service process itself stays stdlib.
The audio is decoded with ffmpeg to float32 PCM and handed to
faster-whisper as a numpy array — PyAV is deliberately NOT on the
runtime path (faster-whisper 1.2.1's PyAV decode call is incompatible
with the installed PyAV 19; ffmpeg is the estate's media tool anyway).

The model is a pre-seeded local snapshot — no network at job time.

Usage: transcribe.py <audio-path> <out-json-path>
Env:   CADENCE_WHISPER_MODEL_PATH (default the seeded base snapshot)
       CADENCE_WHISPER_MODEL_ID   (name recorded in the output)
Exit:  0 = JSON written (segments may be empty); 2 = engine error.
"""
import json
import os
import subprocess
import sys

import numpy as np

MODEL_PATH = os.environ.get(
    "CADENCE_WHISPER_MODEL_PATH",
    "/opt/cadence-whisper-models/faster-whisper-base")
MODEL_ID = os.environ.get("CADENCE_WHISPER_MODEL_ID",
                          "Systran/faster-whisper-base")
# Same binary pin as humanize.py (env inherited from the service
# via captions.py); default keeps the bare name.
FFMPEG_BIN = os.environ.get("CADENCE_FFMPEG_BIN", "ffmpeg")
SR = 16000


def decode_pcm(path):
    p = subprocess.run(
        [FFMPEG_BIN, "-v", "error", "-i", path, "-ac", "1",
         "-ar", str(SR), "-f", "f32le", "-"],
        capture_output=True)
    if p.returncode != 0:
        raise RuntimeError("ffmpeg decode failed")
    return np.frombuffer(p.stdout, dtype=np.float32)


def main():
    audio_path, out_path = sys.argv[1], sys.argv[2]
    from faster_whisper import WhisperModel
    model = WhisperModel(MODEL_PATH, device="cpu", compute_type="int8")
    audio = decode_pcm(audio_path)
    segments, info = model.transcribe(audio, vad_filter=True)
    segs = [{"start": round(float(s.start), 3),
             "end": round(float(s.end), 3),
             "text": s.text.strip()}
            for s in segments if s.text and s.text.strip()]
    with open(out_path, "w") as f:
        json.dump({"language": info.language,
                   "language_probability":
                       round(float(info.language_probability), 3),
                   "model": MODEL_ID,
                   "segments": segs}, f)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # engine failure is a named exit, not a trace
        print(f"transcribe failed: {type(e).__name__}: {e}",
              file=sys.stderr)
        sys.exit(2)
