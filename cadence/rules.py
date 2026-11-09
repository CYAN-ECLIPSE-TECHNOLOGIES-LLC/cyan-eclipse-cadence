"""Load and validate rules/rules-v1.yaml (stdlib only).

The YAML file is deliberately written in a small subset (nested maps, a list
of flat maps, scalars) so this minimal indentation parser can read it.
JSON-compatible output is not used: the file on disk is real YAML.
"""
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RULES_PATH = os.path.join(_HERE, "..", "rules", "rules-v1.yaml")

EXPECTED_RULE_IDS = ["R%d" % i for i in range(1, 15)]
_YEAR_RE = re.compile(r"(1[6789]|20)\d\d")


def _scalar(text):
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        return text[1:-1]
    for conv in (int, float):
        try:
            return conv(text)
        except ValueError:
            pass
    if text in ("true", "false"):
        return text == "true"
    if text in ("null", "~"):
        return None
    return text


def _parse_block(lines, pos, indent):
    """Parse one block at `indent`; returns (object, next_pos)."""
    if pos >= len(lines):
        return {}, pos
    if lines[pos][1].startswith("- "):
        items = []
        while pos < len(lines) and lines[pos][0] == indent and lines[pos][1].startswith("- "):
            first = lines[pos][1][2:]
            item = {}
            key, _, val = first.partition(":")
            item[key.strip()] = _scalar(val) if val.strip() else None
            pos += 1
            while pos < len(lines) and lines[pos][0] > indent:
                k2, _, v2 = lines[pos][1].partition(":")
                item[k2.strip()] = _scalar(v2) if v2.strip() else None
                pos += 1
            items.append(item)
        return items, pos
    mapping = {}
    while pos < len(lines) and lines[pos][0] == indent and not lines[pos][1].startswith("- "):
        key, _, val = lines[pos][1].partition(":")
        key, val = key.strip(), val.strip()
        pos += 1
        if val:
            mapping[key] = _scalar(val)
        elif pos < len(lines) and lines[pos][0] > indent:
            mapping[key], pos = _parse_block(lines, pos, lines[pos][0])
        else:
            mapping[key] = None
    return mapping, pos


def parse_yaml_subset(text):
    lines = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        lines.append((indent, stripped))
    obj, pos = _parse_block(lines, 0, 0)
    if pos != len(lines):
        raise ValueError("YAML subset parse stopped early at line %d" % pos)
    return obj


def load_rules(path=None):
    path = path or DEFAULT_RULES_PATH
    with open(path, "r", encoding="utf-8") as fh:
        data = parse_yaml_subset(fh.read())
    validate_rules(data)
    return data


def validate_rules(data):
    if data.get("rule_set") != "rules-v1":
        raise ValueError("rule_set must be rules-v1, got %r" % data.get("rule_set"))
    rules = data.get("rules") or []
    ids = [r.get("id") for r in rules]
    if ids != EXPECTED_RULE_IDS:
        raise ValueError("rules must be R1..R14 in order, got %r" % ids)
    for rule in rules:
        for field in ("name", "citation", "realisation", "parameters"):
            if not rule.get(field):
                raise ValueError("%s missing %s" % (rule["id"], field))
        if not _YEAR_RE.search(str(rule["citation"])):
            raise ValueError("%s citation carries no year: %r" % (rule["id"], rule["citation"]))
    p = data.get("parameters") or {}
    pause_map = pause_map_from(p)
    if pause_map != {"breath": 0.35, "emphasis": 0.70, "act-boundary": 0.90, "none": 0.0}:
        raise ValueError("pause map drifted from contract: %r" % pause_map)
    if 0.12 in pause_map.values():
        raise ValueError("legacy flat join gap must appear nowhere in the pause map")
    if (p.get("tempo_min"), p.get("tempo_max")) != (0.88, 1.12):
        raise ValueError("tempo bounds must be [0.88, 1.12]")
    defaults = act_tempo_defaults(p)
    if defaults != {"hook": 1.00, "evidence": 1.05, "turn": 0.95, "payoff": 0.92}:
        raise ValueError("per-act tempo defaults drifted: %r" % defaults)
    if p.get("emphasis_pause_budget_per_minute") != 6:
        raise ValueError("emphasis-pause budget must be <=6/min (set to 6)")
    return data


def pause_map_from(params):
    return {
        "breath": float(params["pause_breath_seconds"]),
        "emphasis": float(params["pause_emphasis_seconds"]),
        "act-boundary": float(params["pause_act_boundary_seconds"]),
        "none": float(params["pause_none_seconds"]),
    }


def act_tempo_defaults(params):
    return {
        "hook": float(params["tempo_hook"]),
        "evidence": float(params["tempo_evidence"]),
        "turn": float(params["tempo_turn"]),
        "payoff": float(params["tempo_payoff"]),
    }


class RuleSet:
    """Validated, convenient view over the parsed rule-set document."""

    def __init__(self, path=None):
        self.data = load_rules(path)
        p = self.data["parameters"]
        self.pause_map = pause_map_from(p)
        self.tempo_bounds = (float(p["tempo_min"]), float(p["tempo_max"]))
        self.act_tempo = act_tempo_defaults(p)
        self.emphasis_budget_per_minute = int(p["emphasis_pause_budget_per_minute"])
        self.max_group_words = int(p["breath_group_max_words"])
        self.rules = self.data["rules"]
