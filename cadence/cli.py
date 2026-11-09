"""CLI: python3 -m cadence compile <script.md> --out <score.json>"""
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
    args = parser.parse_args(argv)
    if args.command == "compile":
        score = compile_file(args.script)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(score, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        print("cadence: %s -> %s (%d spans, rule_set=%s)"
              % (args.script, args.out, len(score["spans"]), score["rule_set"]))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
