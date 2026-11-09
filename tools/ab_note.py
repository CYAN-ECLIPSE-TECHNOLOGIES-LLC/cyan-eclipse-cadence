#!/usr/bin/env python3
"""A/B listening-note generator (Cadence M4, stdlib only).

Compares two takes of one episode — the house (standing pipeline) take and
the Cadence shadow take — from their measured prosody, and emits ONE page
of markdown for the owner's by-ear pick.

Inputs (any combination; at least one take source required):
  --house-metrics   cadence-metrics-v1 JSON (cadence/measure.py output)
  --cadence-metrics cadence-metrics-v1 JSON
  --acceptance      cadence-acceptance-v1 JSON (gate table per take;
                    keys "shipped"/"house" and "cadence") — enough on its
                    own when per-take metrics files are absent
  --house-dur / --cadence-dur   duration overrides (seconds)
  --house-path / --cadence-path audio paths as printed in the note

Cadence is NOT the shipping path unless the owner picks it by ear; the
note says so on its face.
"""
import argparse
import json
import sys


# ------------------------------------------------------------------ takes

def _from_metrics(m):
    """cadence-metrics-v1 -> unified take dict."""
    if not m:
        return None
    pauses = m.get("pauses") or {}
    dist = pauses.get("distribution") or {}
    loud = m.get("loudness") or {}
    rate = m.get("rate") or {}
    f0 = m.get("f0") or {}
    return {
        "duration_s": m.get("duration_s"),
        "audio": m.get("audio"),
        "f0_sd_semitones": f0.get("sd_semitones"),
        "voiced_fraction": f0.get("voiced_fraction"),
        "pause_mean_s": pauses.get("mean_s"),
        "pause_micro_share": (dist.get("micro") or {}).get("share"),
        "pause_breath_share": (dist.get("breath") or {}).get("share"),
        "pause_emphasis_share": (dist.get("emphasis") or {}).get("share"),
        # like-for-like across takes: chunk-basis CV + chunk dynamic range
        "rate_cv": rate.get("chunk_cv", rate.get("local_cv")),
        "dynamic_range_db": loud.get("chunk_dynamic_range_db",
                                     loud.get("dynamic_range_db")),
        "in_band_pct": None,
    }


def _from_acceptance_side(side):
    """One side ("shipped"/"cadence") of cadence-acceptance-v1 -> take."""
    if not side:
        return None
    gate = side.get("gate") or {}
    gm = gate.get("metrics") or {}

    def val(key):
        row = gm.get(key) or {}
        return row.get("value")

    return {
        "duration_s": side.get("duration_s"),
        "audio": side.get("audio"),
        "f0_sd_semitones": val("f0_sd_semitones"),
        "voiced_fraction": val("voiced_fraction"),
        "pause_mean_s": val("pause_mean_s"),
        "pause_micro_share": val("pause_micro_share"),
        "pause_breath_share": val("pause_breath_share"),
        "pause_emphasis_share": val("pause_emphasis_share"),
        "rate_cv": val("rate_cv_chunks"),
        "dynamic_range_db": val("dynamic_range_db"),
        "in_band_pct": gate.get("in_band_pct"),
    }


def _merge(base, overlay):
    """Overlay non-None fields of `overlay` onto `base` (either may be None)."""
    if base is None:
        return overlay
    if overlay is None:
        return base
    out = dict(base)
    for k, v in overlay.items():
        if v is not None:
            out[k] = v
    return out


def _episode_label(acceptance):
    ep = (acceptance or {}).get("episode") or "ep19"
    ep = str(ep).strip()
    if ep.startswith("ep") and ep[2:].isdigit():
        return "ep." + ep[2:]
    if ep.startswith("ep."):
        return ep
    return ep


# -------------------------------------------------------------- formatting

def _n(v, nd=3):
    return "—" if v is None else ("%.*f" % (nd, v))


def _pct(v):
    return "—" if v is None else ("%.0f%%" % (100.0 * v))


def _dur(v):
    return "not yet measured" if v is None else ("%.1f s" % v)


def _cmp(a, b):
    """-1/0/+1 comparing cadence (b) against house (a); None if either missing."""
    if a is None or b is None:
        return None
    if abs(b - a) < 1e-9:
        return 0
    return 1 if b > a else -1


# ------------------------------------------------------------------- note

def build_note(house, cadence, episode="ep.19",
               house_path=None, cadence_path=None):
    house = house or {}
    cadence = cadence or {}
    hp = house_path or house.get("audio") or "<house take path>"
    cp = cadence_path or cadence.get("audio") or "<cadence take path>"
    lines = []
    lines.append("# (INT — INTERNAL) A/B Listening Note — %s" % episode)
    lines.append("")
    lines.append("Two takes of the same script. Measured differences only; "
                 "the pick is by ear.")
    lines.append("")
    lines.append("## The takes")
    lines.append("")
    if house:
        lines.append("- **House take** (standing pipeline) — %s · `%s`"
                     % (_dur(house.get("duration_s")), hp))
    else:
        lines.append("- **House take** (standing pipeline) — not yet "
                     "measured · `%s`" % hp)
    if cadence:
        lines.append("- **Cadence take** (shadow) — %s · `%s`"
                     % (_dur(cadence.get("duration_s")), cp))
    else:
        lines.append("- **Cadence take** (shadow) — not yet measured · `%s`"
                     % cp)
    if not house:
        lines.append("")
        lines.append("House take not yet measured — the table and notes "
                     "below cover the Cadence take alone until it lands.")
    lines.append("")
    lines.append("## Measured differences")
    lines.append("")
    lines.append("| Metric | House | Cadence |")
    lines.append("|---|---|---|")

    def row(label, key, fmt):
        lines.append("| %s | %s | %s |" % (
            label, fmt(house.get(key)), fmt(cadence.get(key))))

    row("F0 SD (semitones)", "f0_sd_semitones", lambda v: _n(v, 2))
    row("Pause mean (s)", "pause_mean_s", lambda v: _n(v, 3))
    lines.append("| Pause class shares (micro / breath / emphasis) | %s | %s |"
                 % (" / ".join(_pct(house.get(k)) for k in
                               ("pause_micro_share", "pause_breath_share",
                                "pause_emphasis_share")),
                    " / ".join(_pct(cadence.get(k)) for k in
                               ("pause_micro_share", "pause_breath_share",
                                "pause_emphasis_share"))))
    row("Rate CV (chunk basis)", "rate_cv", lambda v: _n(v, 3))
    row("Dynamic range (dB, chunk basis)", "dynamic_range_db",
        lambda v: _n(v, 2))
    row("Voiced fraction", "voiced_fraction", lambda v: _n(v, 3))
    row("In-band vs human reference (%)", "in_band_pct",
        lambda v: "—" if v is None else ("%.0f%%" % v))
    lines.append("")
    lines.append("## What you will hear")
    lines.append("")
    lines.extend(_what_you_will_hear(house, cadence))
    lines.append("")
    lines.append("## Verdict")
    lines.append("")
    lines.append("Owner picks by ear; Cadence is NOT the shipping path "
                 "unless picked.")
    lines.append("")
    return "\n".join(lines)


def _what_you_will_hear(house, cadence):
    out = []
    if not cadence:
        return ["- Cadence take not yet measured — nothing to compare yet."]
    if not house:
        out.append("- Cadence take alone so far: pause mean %s s across "
                   "micro / breath / emphasis shares %s / %s / %s; dynamic "
                   "range %s dB; F0 SD %s semitones."
                   % (_n(cadence.get("pause_mean_s"), 3),
                      _pct(cadence.get("pause_micro_share")),
                      _pct(cadence.get("pause_breath_share")),
                      _pct(cadence.get("pause_emphasis_share")),
                      _n(cadence.get("dynamic_range_db"), 2),
                      _n(cadence.get("f0_sd_semitones"), 2)))
        return out

    c = _cmp(house.get("pause_mean_s"), cadence.get("pause_mean_s"))
    if c == 1:
        out.append("- Longer, classed silences — breath/emphasis/"
                   "act-boundary instead of one flat gap (pause mean "
                   "%s s house vs %s s Cadence)."
                   % (_n(house.get("pause_mean_s"), 3),
                      _n(cadence.get("pause_mean_s"), 3)))
    elif c == -1:
        out.append("- Tighter silences on the Cadence take (pause mean "
                   "%s s house vs %s s Cadence)."
                   % (_n(house.get("pause_mean_s"), 3),
                      _n(cadence.get("pause_mean_s"), 3)))
    elif c == 0:
        out.append("- Pause length is a wash (mean %s s on both takes) — "
                   "any difference is in the pause-class mix, not the gaps."
                   % _n(cadence.get("pause_mean_s"), 3))

    c = _cmp(house.get("duration_s"), cadence.get("duration_s"))
    if c == 1:
        out.append("- The Cadence take runs longer: no global tempo crush, "
                   "per-unit tempo ≤±12%% + real pauses — pace now comes "
                   "from the script, not the squeeze (%s house vs %s "
                   "Cadence)."
                   % (_dur(house.get("duration_s")),
                      _dur(cadence.get("duration_s"))))
    elif c == -1:
        out.append("- The Cadence take runs shorter (%s house vs %s "
                   "Cadence) — its pacing is brisker overall, still with "
                   "per-unit tempo held to ≤±12%%."
                   % (_dur(house.get("duration_s")),
                      _dur(cadence.get("duration_s"))))

    c = _cmp(house.get("dynamic_range_db"), cadence.get("dynamic_range_db"))
    if c == 1:
        out.append("- Wider loudness swings on Cadence (dynamic range "
                   "%s dB house vs %s dB) — emphasis lands louder against "
                   "quieter connective tissue."
                   % (_n(house.get("dynamic_range_db"), 2),
                      _n(cadence.get("dynamic_range_db"), 2)))
    elif c == -1:
        out.append("- Flatter loudness on Cadence (dynamic range %s dB "
                   "house vs %s dB) — a more even, less emphatic delivery."
                   % (_n(house.get("dynamic_range_db"), 2),
                      _n(cadence.get("dynamic_range_db"), 2)))

    c = _cmp(house.get("f0_sd_semitones"), cadence.get("f0_sd_semitones"))
    if c == 1:
        out.append("- More pitch movement on Cadence (F0 SD %s st house vs "
                   "%s st) — contour does more of the talking."
                   % (_n(house.get("f0_sd_semitones"), 2),
                      _n(cadence.get("f0_sd_semitones"), 2)))
    elif c == -1:
        out.append("- Steadier pitch on Cadence (F0 SD %s st house vs "
                   "%s st) — a flatter, more level read."
                   % (_n(house.get("f0_sd_semitones"), 2),
                      _n(cadence.get("f0_sd_semitones"), 2)))

    c = _cmp(house.get("rate_cv"), cadence.get("rate_cv"))
    if c == 1:
        out.append("- Less even pacing on Cadence (rate CV %s house vs "
                   "%s) — local speed varies more from passage to passage."
                   % (_n(house.get("rate_cv"), 3),
                      _n(cadence.get("rate_cv"), 3)))
    elif c == -1:
        out.append("- More even pacing on Cadence (rate CV %s house vs "
                   "%s) — local speed holds steadier across passages."
                   % (_n(house.get("rate_cv"), 3),
                      _n(cadence.get("rate_cv"), 3)))

    c = _cmp(house.get("voiced_fraction"), cadence.get("voiced_fraction"))
    if c is not None and c != 0:
        out.append("- Voiced fraction %s house vs %s Cadence — the Cadence "
                   "take carries proportionally %s sound against silence."
                   % (_n(house.get("voiced_fraction"), 3),
                      _n(cadence.get("voiced_fraction"), 3),
                      "more" if c == 1 else "less"))
    if not out:
        out.append("- No measured dimension differs between the takes; "
                   "any difference you hear is below these metrics.")
    return out


# -------------------------------------------------------------------- CLI

def _load(path):
    if not path:
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--house-metrics")
    ap.add_argument("--cadence-metrics")
    ap.add_argument("--acceptance")
    ap.add_argument("--house-dur", type=float)
    ap.add_argument("--cadence-dur", type=float)
    ap.add_argument("--house-path")
    ap.add_argument("--cadence-path")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    acceptance = _load(args.acceptance)
    house = _merge(_from_acceptance_side(
        (acceptance or {}).get("shipped") or (acceptance or {}).get("house")),
        _from_metrics(_load(args.house_metrics)))
    cadence = _merge(_from_acceptance_side((acceptance or {}).get("cadence")),
                     _from_metrics(_load(args.cadence_metrics)))
    if house is None and cadence is None:
        print("no take data: pass --acceptance and/or per-take metrics",
              file=sys.stderr)
        return 2
    if args.house_dur is not None and house is not None:
        house["duration_s"] = args.house_dur
    if args.cadence_dur is not None and cadence is not None:
        cadence["duration_s"] = args.cadence_dur

    note = build_note(house, cadence, _episode_label(acceptance),
                      args.house_path, args.cadence_path)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(note)
    print("wrote %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
