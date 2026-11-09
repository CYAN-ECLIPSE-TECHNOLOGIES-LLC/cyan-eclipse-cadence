"""Helio engine adapter (M2): score span text -> synthesised wav.

Helio is the VM-local `/opt/hatch/bin/tts` CLI (Meta Shortwave bridge).
The adapter's call text is the span's `text_encoded` exactly as the
compiler emitted it - the compiler owns typography encoding (R3), the
adapter never re-encodes.

Route detection (once per process, then cached):
- Primary: `--format wav`. This bridge prepends a small ID3 tag to the
  RIFF stream, which strict readers reject; the tag is stripped in
  place and the result validated with the stdlib wave reader.
- Fallback: `--format mp3` + ffmpeg decode to wav, if the wav route
  errors or yields an unreadable file. The route actually used is
  returned per span and recorded in the render log.

`--speed` is NEVER passed: it is hard-blocked on this VM by design.
Pacing is the renderer's per-unit atempo, never the engine's.
"""
import os
import subprocess
import wave

TTS_CLI = "/opt/hatch/bin/tts"
VOICE = "avocado_v2:MAI_03"
TIMEOUT_SECS = 180

_route = None  # "wav" | "mp3-fallback"; detected once, then reused


def _run(argv):
    return subprocess.run(argv, capture_output=True, text=True,
                          timeout=TIMEOUT_SECS)


def _strip_id3(path):
    """Remove a leading ID3v2 tag if present. Returns True if stripped."""
    with open(path, "rb") as fh:
        head = fh.read(10)
    if len(head) < 10 or head[:3] != b"ID3":
        return False
    size = ((head[6] & 0x7F) << 21) | ((head[7] & 0x7F) << 14) \
        | ((head[8] & 0x7F) << 7) | (head[9] & 0x7F)
    total = 10 + size
    if head[5] & 0x10:  # footer present
        total += 10
    with open(path, "rb") as fh:
        data = fh.read()
    if data[total:total + 4] != b"RIFF":
        return False
    with open(path, "wb") as fh:
        fh.write(data[total:])
    return True


def _valid_wav(path):
    try:
        with wave.open(path, "rb") as w:
            return w.getnframes() > 0
    except (wave.Error, EOFError, OSError):
        return False


def synth_span(text, out_path):
    """Synthesise one span's encoded text to a wav at `out_path`.

    Returns {"route": "wav"|"mp3-fallback", "path": out_path,
    "id3_stripped": bool}.
    """
    global _route
    parent = os.path.dirname(os.path.abspath(out_path))
    os.makedirs(parent, exist_ok=True)
    if _route != "mp3-fallback":
        proc = _run([TTS_CLI, "speak", "--text", text, "--voice", VOICE,
                     "--format", "wav", "--output", out_path])
        if proc.returncode == 0 and os.path.exists(out_path):
            stripped = _strip_id3(out_path)
            if _valid_wav(out_path):
                _route = "wav"
                return {"route": "wav", "path": out_path,
                        "id3_stripped": stripped}
        _route = "mp3-fallback"
    mp3_path = out_path + ".mp3"
    proc = _run([TTS_CLI, "speak", "--text", text, "--voice", VOICE,
                 "--format", "mp3", "--output", mp3_path])
    if proc.returncode != 0 or not os.path.exists(mp3_path):
        raise RuntimeError("helio synth failed (mp3 route): %s"
                           % (proc.stderr or proc.stdout)[-400:])
    dec = _run(["ffmpeg", "-y", "-v", "error", "-i", mp3_path,
                "-c:a", "pcm_s16le", out_path])
    if dec.returncode != 0 or not _valid_wav(out_path):
        raise RuntimeError("helio mp3 decode failed: %s" % dec.stderr[-400:])
    return {"route": "mp3-fallback", "path": out_path, "id3_stripped": False}
