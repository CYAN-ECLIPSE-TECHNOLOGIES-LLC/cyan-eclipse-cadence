# Cyan Eclipse Cadence — WORKING NAME

**WORKING NAME:** "Cyan Eclipse Cadence" is a working name only. Final name is
an owner pick and a trademark (TM) screen is still owed on the pick.

## What it is

Cadence performs **rhetorical performance compilation with an acoustic
acceptance gate**: it compiles a reel script the way a compiler compiles code —
parsing the script into rhetorical acts (hook · evidence · turn · payoff) and
breath-groups, then compiling each span into a performance score using a
versioned, inspectable rule set derived from Cyan Eclipse Technologies LLC's
library corpus (each of the 14 rules in `rules/rules-v1.yaml` names its
treatise, author, year and chapter). The score carries, per span: one peak word
with a graded stress tier, a contour class assigned by clause function
(antithesis mirroring, series penultimate rise, question contours), a pause
class with a situation-set duration from a three-class taxonomy (the flat
0.120 s join of the legacy pipeline appears nowhere), a register per act, a
per-act tempo bounded to ±12%, and the Delsarte inverse-intensity rule at
climax (quieter, not louder). Because the current synthesis engine surface is
plain text, the score is realised through the levers that exist — graded
emphasis typography and oral punctuation in the encoded text, per-breath-group
synthesis granularity, per-class join silences, and bounded per-unit tempo —
and, in later milestones, the rendered audio is measured against corpus-derived
acceptance bands and sealed into a performance receipt. A bare SSML/emotion
wrapper annotates text and trusts the engine; Cadence treats delivery as a
compiled artifact that must pass inspection, under a cited, versioned rule set.

Design of record: White Report voice-humanization report (2026-11-09), §4.

## v0.1 scope

- Rule set v1 as cited YAML (`rules/rules-v1.yaml`, 14 rules, R1–R14).
- Performance compiler for the existing house segment script format
  (`## SCRIPT` section, `[Sn]` segments, optional persona header).
- Score JSON output per the fixed score contract (see CLI usage).
- Stdlib-only Python 3.12; stdlib `unittest` test suite.

## Out of scope (v0.1)

- Any audio work: no synthesis, no engine adapter, no prosody measurement,
  no acceptance gate execution, no performance receipt (later milestones).
- New TTS engine procurement, voice cloning, SSML-engine adapter.
- Any UI beyond the CLI + score JSON.
- Multilingual support.
- Automatic script rewriting (the compiler flags; a human rewrites).
- Changing the voice identity.

## CLI usage

```sh
python3 -m cadence compile <script.md> --out <score.json>
python3 -m unittest discover -s tests
```

Score JSON contract:

```json
{
  "script_id": "ep18-script",
  "source_sha256": "<sha256 of the source script file>",
  "persona": {"speaker": "...", "to": "...", "want": "..."},
  "rule_set": "rules-v1",
  "spans": [
    {
      "span_id": "sp001",
      "segment": "S1",
      "act": "hook",
      "text_raw": "...",
      "text_encoded": "...",
      "peak": {"word": "...", "tier": 1},
      "contour": "fall",
      "pause_after": {"class": "act-boundary", "seconds": 0.9},
      "tempo": 1.0,
      "register": "medium",
      "intensity": "normal"
    }
  ]
}
```

`persona` is `null` when the script carries no persona header line
(`> Persona: speaker | to | want`). Span = one breath-group.
`act` ∈ hook|evidence|turn|payoff · `contour` ∈
fall|rise|hold-rise|fall-rise|level · pause `class` ∈
breath|emphasis|act-boundary|none · `tempo` ∈ [0.88, 1.12] ·
`intensity` ∈ normal|reduced|climax-quiet.
