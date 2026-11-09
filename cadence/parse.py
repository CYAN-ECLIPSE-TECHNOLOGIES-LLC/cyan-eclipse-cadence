"""Parse the house reel-script format.

Format: a `## SCRIPT` section containing `[Sn]` segments (the ep.18/ep.19
house format). Optional persona header line anywhere in the document:
`> Persona: speaker | to | want`.

Author markup honoured:
  *word*    -> tier-2 peak
  **word**  -> tier-3 peak
  CAPS word -> tier-3 peak
  ||        -> rhetorical emphasis pause token (Sanders, 1863, section V)
"""
import re
from dataclasses import dataclass, field

PERSONA_RE = re.compile(r"^>\s*Persona:\s*(.+?)\s*$", re.MULTILINE)
SEGMENT_RE = re.compile(r"\[(S\d+)\]\s*(.*?)(?=\[S\d+\]|\Z)", re.DOTALL)
SECTION_RE = re.compile(r"^##\s+SCRIPT\b", re.MULTILINE)
NEXT_SECTION_RE = re.compile(r"^##\s+", re.MULTILINE)
SENTENCE_RE = re.compile(r"[^.!?]+[.!?]*")
CLAUSE_SPLIT_RE = re.compile(r"(?<=[,;\u2014])\s*")
WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'\u2019-]*")

FUNCTION_WORDS = set(
    "the a an and or but of to in on for with by at from as is are was were be been "
    "it its this that these those i you he she we they his her their our your my "
    "not no do does did didn't don't isn't aren't won't can't than then so if when "
    "who whom which what all any each".split()
)
# Stopwords for last-content-word peak selection: function words plus light verbs.
STOPWORDS = FUNCTION_WORDS | set("had has have having".split())


@dataclass
class Segment:
    seg_id: str
    text_raw: str


@dataclass
class ParsedScript:
    persona: dict = None
    segments: list = field(default_factory=list)


def parse_persona(text):
    m = PERSONA_RE.search(text)
    if not m:
        return None
    parts = [p.strip() for p in m.group(1).split("|")]
    if len(parts) != 3 or not all(parts):
        return None
    return {"speaker": parts[0], "to": parts[1], "want": parts[2]}


def script_section(text):
    m = SECTION_RE.search(text)
    if not m:
        raise ValueError("no '## SCRIPT' section found")
    rest = text[m.end():]
    nxt = NEXT_SECTION_RE.search(rest)
    return rest[:nxt.start()] if nxt else rest


def parse_script(text):
    section = script_section(text)
    segments = []
    for seg_id, body in SEGMENT_RE.findall(section):
        body = " ".join(body.split())
        if body:
            segments.append(Segment(seg_id=seg_id, text_raw=body))
    if not segments:
        raise ValueError("SCRIPT section contains no [Sn] segments")
    return ParsedScript(persona=parse_persona(text), segments=segments)


def split_sentences(text):
    return [s.strip() for s in SENTENCE_RE.findall(text) if s.strip()]


def words_of(text):
    return WORD_RE.findall(text.replace("*", " ").replace("||", " "))


def clean_text(text):
    """Plain text: emphasis-pause tokens and stress markup removed."""
    text = text.replace("||", " ")
    text = text.replace("**", " ").replace("*", " ")
    return " ".join(text.split())


def markup_peaks(sentence):
    """Author-marked peaks in one sentence: list of (word, tier), in order."""
    peaks = []
    for m in re.finditer(r"\*\*([^*]+)\*\*", sentence):
        for w in words_of(m.group(1)):
            peaks.append((w, 3))
    rest = re.sub(r"\*\*[^*]+\*\*", " ", sentence)
    for m in re.finditer(r"\*([^*]+)\*", rest):
        for w in words_of(m.group(1)):
            peaks.append((w, 2))
    plain = clean_text(sentence)
    for w in words_of(plain):
        if len(w) >= 2 and w.isupper() and w.isalpha():
            peaks.append((w, 3))
    return peaks


def sentence_peak(sentence):
    """One peak per sentence (R3): author markup wins (highest tier, first at
    that tier); else the last content word of the sentence, tier 1."""
    marked = markup_peaks(sentence)
    if marked:
        top = max(t for _, t in marked)
        for w, t in marked:
            if t == top:
                return {"word": w, "tier": t}
    for w in reversed(words_of(clean_text(sentence))):
        if w.lower() not in STOPWORDS:
            return {"word": w, "tier": 1}
    return None
