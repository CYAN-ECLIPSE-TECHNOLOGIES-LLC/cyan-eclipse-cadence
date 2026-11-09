"""Performance receipt (M3): sealed per-span verdicts for one render.

receipt_sha256 = sha256 of the canonical JSON (sorted keys, compact
separators) of every field except receipt_sha256 itself.
"""
import hashlib
import json
import os


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def build_receipt(render_path, score_path, metrics_path, bands_path):
    with open(score_path, encoding="utf-8") as fh:
        score = json.load(fh)
    with open(metrics_path, encoding="utf-8") as fh:
        metrics = json.load(fh)
    with open(bands_path, encoding="utf-8") as fh:
        bands = json.load(fh)
    band_metrics = bands.get("metrics", {})
    measured_by_id = {s["span_id"]: s for s in (metrics.get("spans") or [])}

    spans_out = []
    for span in score.get("spans", []):
        sid = span["span_id"]
        m = measured_by_id.get(sid, {})
        reasons = []
        # Pause adherence: measured pause after the span vs the directive.
        directive = (span.get("pause_after") or {}).get("seconds")
        measured_pause = m.get("pause_after_s")
        if directive is not None and measured_pause is not None:
            tol = max(0.15, 0.25 * directive)
            if abs(measured_pause - directive) > tol:
                reasons.append(
                    "pause_after %.2fs vs directive %.2fs (tol %.2fs)"
                    % (measured_pause, directive, tol))
        # Span rate vs the reference band for overall rate, when both exist.
        rng = band_metrics.get("rate_overall_wpm")
        if rng and m.get("wpm"):
            if not (rng[0] * 0.7 <= m["wpm"] <= rng[1] * 1.3):
                reasons.append("span wpm %.1f far outside rate band %s"
                               % (m["wpm"], rng))
        # Span F0 variation vs the reference F0-SD band, when both exist.
        rng = band_metrics.get("f0_sd_semitones")
        if rng and m.get("f0_sd_semitones") is not None:
            if not (rng[0] * 0.5 <= m["f0_sd_semitones"] <= rng[1] * 1.5):
                reasons.append("span F0 SD %.2f st far outside band %s"
                               % (m["f0_sd_semitones"], rng))
        spans_out.append({
            "span_id": sid,
            "directives": span,
            "measured": {"dur_s": m.get("dur_s"),
                         "pause_after_s": measured_pause},
            "verdict": "out-of-band" if reasons else "in-band",
            "reasons": reasons,
        })
    in_n = sum(1 for s in spans_out if s["verdict"] == "in-band")
    receipt = {
        "schema": "cadence-receipt-v1",
        "render": os.path.basename(render_path),
        "render_sha256": _sha256_file(render_path),
        "score_sha256": _sha256_file(score_path),
        "rule_set": score.get("rule_set"),
        "bands_version": bands.get("version"),
        "spans": spans_out,
        "totals": {"spans": len(spans_out), "in_band": in_n,
                   "in_band_pct": (round(100.0 * in_n / len(spans_out), 1)
                                   if spans_out else None)},
        "prev_receipt_sha256": None,
    }
    receipt["receipt_sha256"] = hashlib.sha256(
        _canonical(receipt).encode("utf-8")).hexdigest()
    return receipt
