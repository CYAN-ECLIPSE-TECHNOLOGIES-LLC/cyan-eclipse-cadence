"""Performance compiler: parsed script -> performance score (score contract).

Deterministic act assignment, breath-group chunking, peak selection,
contour assignment, pause classes, per-act tempo/register and intensity,
per rules/rules-v1.yaml. See docs/BUILD-RECORD.md and README.md.
"""
import hashlib
import math
import re

from .parse import (
    CLAUSE_SPLIT_RE, FUNCTION_WORDS, clean_text, markup_peaks, parse_script,
    sentence_peak, split_sentences, words_of,
)
from .rules import RuleSet

TURN_RE = re.compile(
    r"\b(?:turn|but|risks|however|underneath)\b|then the tape turned", re.IGNORECASE)
WH_WORDS = {"who", "what", "when", "where", "why", "how", "which", "whom", "whose"}
ACT_REGISTER = {"hook": "medium", "evidence": "medium", "turn": "raised", "payoff": "lowered"}


def assign_act(index, count, segment_text):
    """First segment = hook, last = payoff, a segment whose text matches the
    turn markers = turn, else evidence. Deterministic, in that priority."""
    if index == 0:
        return "hook"
    if index == count - 1:
        return "payoff"
    if TURN_RE.search(clean_text(segment_text)):
        return "turn"
    return "evidence"


def _balanced_chunks(words, max_words):
    n = len(words)
    if n <= max_words:
        return [words]
    k = math.ceil(n / max_words)
    size = math.ceil(n / k)
    return [words[i:i + size] for i in range(0, n, size)]


def chunk_sentence(sentence, max_words=14):
    """Breath-groups (R1): split on clause marks (em dash, ; ,) and accumulate
    clauses into groups of at most `max_words` words. A semicolon is a hard
    boundary (antithesis limbs stay separate, R7). A single clause longer
    than the cap is split balanced, so no split creates a group under
    4 words where the clause length allows it."""
    clauses = [c.strip() for c in CLAUSE_SPLIT_RE.split(sentence) if c.strip()]
    groups, current = [], []
    cur_words = 0

    def flush():
        nonlocal current, cur_words
        if current:
            groups.append(" ".join(current))
        current, cur_words = [], 0

    for clause in clauses:
        cw = words_of(clause)
        if len(cw) > max_words:
            flush()
            for part in _balanced_chunks(cw, max_words):
                groups.append(" ".join(part))
            continue
        if cur_words and cur_words + len(cw) > max_words:
            flush()
        current.append(clause)
        cur_words += len(cw)
        if clause.rstrip().endswith(";"):
            flush()
    flush()
    return groups


def _is_antithesis(sentence):
    clean = clean_text(sentence)
    if ";" not in clean:
        return False
    second = clean.split(";", 1)[1].lower()
    return bool(re.search(r"\bnot\b|\w+n['\u2019]t\b", second))


def _contours(sentence, groups):
    n = len(groups)
    clean = clean_text(sentence)
    final = n - 1
    if clean.rstrip().endswith("?"):
        first = (words_of(clean) or [""])[0].lower()
        out = ["hold-rise"] * n
        out[final] = "fall" if first in WH_WORDS else "rise"
        return out
    if _is_antithesis(sentence):
        semi = next(i for i, g in enumerate(groups) if ";" in g)
        out = []
        for i in range(n):
            if i <= semi:
                out.append("rise")
            elif i == final:
                out.append("fall")
            else:
                out.append("fall-rise")
        return out
    if clean.count(",") >= 2:  # series (R8)
        out = ["hold-rise"] * n
        if n >= 2:
            out[n - 2] = "rise"
        out[final] = "fall"
        return out
    out = ["hold-rise"] * n
    out[final] = "fall"
    return out


def _peak_group(groups, sentence):
    peak = sentence_peak(sentence)
    if peak is None:
        return None, None
    marked = markup_peaks(sentence)
    is_marked = any(w.lower() == peak["word"].lower() for w, _ in marked)
    hits = [i for i, g in enumerate(groups)
            if peak["word"].lower() in [w.lower() for w in words_of(clean_text(g))]]
    if not hits:
        return None, None
    # Default peak is the sentence's last content word: take the last hit.
    # Author markup: take the first hit at the marked position.
    return (hits[0] if is_marked else hits[-1]), peak


def _encode(group_raw, peak, owns_peak):
    text = group_raw.replace("**", "").replace("*", "")
    if owns_peak and peak:
        word = peak["word"]

        def repl(m):
            if peak["tier"] >= 3:
                return m.group(0).upper()
            if peak["tier"] == 2:
                return "*" + m.group(0) + "*"
            return m.group(0)

        text = re.sub(r"\b" + re.escape(word) + r"\b", repl, text,
                      count=1, flags=re.IGNORECASE)
    return " ".join(text.split())


def _intensity(group_raw, climax):
    if climax:
        return "climax-quiet"
    ws = [w.lower() for w in words_of(clean_text(group_raw))]
    if 0 < len(ws) <= 6:
        func = sum(1 for w in ws if w in FUNCTION_WORDS or w.endswith("n't")
                   or w.endswith("n\u2019t"))
        if func / len(ws) >= 0.5:
            return "reduced"  # R4 subordination dip
    return "normal"


def compile_parsed(parsed, script_id, source_sha256, rule_set=None):
    rs = rule_set or RuleSet()
    spans = []
    count = len(parsed.segments)
    for s_idx, segment in enumerate(parsed.segments):
        act = assign_act(s_idx, count, segment.text_raw)
        tempo = min(max(rs.act_tempo[act], rs.tempo_bounds[0]), rs.tempo_bounds[1])
        sentences = split_sentences(segment.text_raw)
        for sent_i, sentence in enumerate(sentences):
            groups = chunk_sentence(sentence, rs.max_group_words)
            contours = _contours(sentence, groups)
            peak_idx, peak = _peak_group(groups, sentence)
            climax = act == "payoff" and sent_i == len(sentences) - 1
            for g_idx, group in enumerate(groups):
                is_last_group = (s_idx == count - 1 and sent_i == len(sentences) - 1
                                 and g_idx == len(groups) - 1)
                is_seg_last = sent_i == len(sentences) - 1 and g_idx == len(groups) - 1
                if is_last_group:
                    pause = "none"
                elif is_seg_last:
                    pause = "act-boundary"
                elif "||" in group:
                    pause = "emphasis"  # R2: author rhetorical pause token
                else:
                    pause = "breath"
                spans.append({
                    "span_id": "sp%03d" % (len(spans) + 1),
                    "segment": segment.seg_id,
                    "act": act,
                    "text_raw": " ".join(group.split()),
                    "text_encoded": _encode(group, peak, g_idx == peak_idx),
                    "peak": peak if g_idx == peak_idx else None,
                    "contour": contours[g_idx],
                    "pause_after": {"class": pause, "seconds": rs.pause_map[pause]},
                    "tempo": tempo,
                    "register": ACT_REGISTER[act],
                    "intensity": _intensity(group, climax),
                })
    return {
        "script_id": script_id,
        "source_sha256": source_sha256,
        "persona": parsed.persona,
        "rule_set": rs.data["rule_set"],
        "spans": spans,
    }


def compile_file(path, rule_set=None):
    with open(path, "rb") as fh:
        raw = fh.read()
    parsed = parse_script(raw.decode("utf-8"))
    base = path.replace("\\", "/").rsplit("/", 1)[-1]
    script_id = base[:-3] if base.endswith(".md") else base
    return compile_parsed(parsed, script_id, hashlib.sha256(raw).hexdigest(), rule_set)
