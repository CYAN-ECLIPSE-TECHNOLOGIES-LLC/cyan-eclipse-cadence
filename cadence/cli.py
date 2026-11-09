"""CLI: python3 -m cadence compile|render|measure|receipt ..."""
import argparse
import json
import sys

from .compile import compile_file


def main(argv=None):
    parser = argparse.ArgumentParser(prog="cadence")
    sub = parser.add_subparsers(dest="command", required=True)
    comp = sub.add_parser("compile", help="compile a house-format script to a score JSON")
    comp.add_argument("script", help="path to script.md")
    comp.add_argument("--out", required=True, help="path for score.json")
    rend = sub.add_parser("render", help="render a score JSON to a joined wav via Helio")
    rend.add_argument("score", help="path to score.json")
    rend.add_argument("--out", required=True, help="path for joined.wav")
    rend.add_argument("--workdir", required=True,
                      help="dir for per-span intermediates")
    meas = sub.add_parser("measure", help="measure prosody of a render")
    meas.add_argument("audio", help="path to audio (wav/mp3/...)")
    meas.add_argument("--timings", help="path to timings JSON")
    meas.add_argument("--score", help="path to score JSON")
    meas.add_argument("--out", required=True, help="path for metrics.json")
    rec = sub.add_parser("receipt", help="emit a sealed performance receipt")
    rec.add_argument("--render", required=True, help="path to rendered audio")
    rec.add_argument("--score", required=True, help="path to score JSON")
    rec.add_argument("--metrics", required=True, help="path to metrics JSON")
    rec.add_argument("--bands", required=True, help="path to reference bands JSON")
    rec.add_argument("--out", required=True, help="path for receipt.json")
    args = parser.parse_args(argv)
    if args.command == "compile":
        score = compile_file(args.script)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(score, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        print("cadence: %s -> %s (%d spans, rule_set=%s)"
              % (args.script, args.out, len(score["spans"]), score["rule_set"]))
        return 0
    if args.command == "render":
        from .render import render_file
        result = render_file(args.score, args.out, args.workdir)
        print("cadence: %s -> %s (%d spans, %.3fs, expected %.3fs)"
              % (args.score, args.out, result["spans"],
                 result["duration_s"], result["expected_duration_s"]))
        print("cadence: timings -> %s" % result["timings"])
        print("cadence: renderlog -> %s" % result["renderlog"])
        return 0
    if args.command == "measure":
        from .measure import measure
        metrics = measure(args.audio, timings_path=args.timings,
                          score_path=args.score)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(metrics, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        print("cadence: measured %s -> %s (dur %.2fs, pauses %d, basis %s)"
              % (args.audio, args.out, metrics["duration_s"],
                 metrics["pauses"]["count"], metrics["rate"]["rate_cv_basis"]))
        return 0
    if args.command == "receipt":
        from .receipt import build_receipt
        receipt = build_receipt(args.render, args.score, args.metrics,
                                args.bands)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(receipt, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        print("cadence: receipt -> %s (in-band %.1f%%, sha %s)"
              % (args.out, receipt["totals"]["in_band_pct"],
                 receipt["receipt_sha256"][:12]))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
