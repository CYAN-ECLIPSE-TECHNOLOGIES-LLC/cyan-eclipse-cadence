# Cyan Eclipse Cadence — Build Record

## M1 — Scaffold + rule set + compiler (2026-11-09)

Commissioned by the owner 2026-11-09. Design of record: White Report
voice-humanization report (2026-11-09), §4 (esp. §4.2 novel mechanism,
§4.3 architecture, §4.4 rule set v1). Built remote-first on Shard E
(`/home/witness/app-family/cyan-eclipse-cadence`), Python 3.12 stdlib only,
text source only (disk-constrained host: no audio, no venvs, no installs).

### What landed

- `LICENSE` — full MIT text, copyright Cyan Eclipse Technologies LLC.
- `README.md` — working name "Cyan Eclipse Cadence" marked WORKING NAME
  (owner pick + TM screen owed), novel-mechanism summary, v0.1 scope and
  out-of-scope, CLI usage, score contract.
- `rules/rules-v1.yaml` — 14 rules R1–R14, each with name, corpus citation
  (author/year/chapter, verbatim from the design report §4.4/§1),
  realisation note and parameters. Global parameters carry the pause map
  (breath 0.35 s · emphasis 0.70 s · act-boundary 0.90 s · none 0.0 s — the
  legacy flat 0.120 s join appears nowhere), tempo bounds [0.88, 1.12],
  per-act tempo defaults (hook 1.00 · evidence 1.05 · turn 0.95 ·
  payoff 0.92) and the emphasis-pause budget (≤6/min).
- `cadence/` package (stdlib only):
  - `rules.py` — minimal YAML-subset parser (~80 lines: nested maps, a list
    of flat maps, scalars) + validation (all 14 rules in order, every rule
    cited with a year, pause map, tempo bounds/defaults, budget).
  - `parse.py` — house script format (`## SCRIPT`, `[Sn]` segments),
    optional persona header (`> Persona: speaker | to | want`), author
    markup (`*word*` tier-2, `**word**`/CAPS tier-3, `||` emphasis pause).
  - `compile.py` — deterministic act assignment (first hook, last payoff,
    turn-marker match = turn, else evidence); breath-group chunking
    (sentence ends, then clause marks — ; , — groups ≤14 words, semicolon
    a hard boundary, oversize clauses split balanced); one peak per
    sentence (author markup wins, else last content word); contours by
    clause function (question rise / wh- fall, series hold-rise +
    penultimate rise + final fall, antithesis mirrored); pause classes
    from the rule-set map; per-act tempo/register; subordination dip
    (intensity reduced) and payoff-final-sentence climax-quiet.
  - `cli.py` + `__main__.py` — `python3 -m cadence compile <script.md>
    --out <score.json>` emitting the fixed score contract.
- `tests/` — stdlib unittest; fixture `tests/fixtures/ep18-script.md`
  carries ep.18's five `[Sn]` segment texts verbatim in house format.
- `.gitignore` — audio artefacts, `__pycache__`, scratch, local scores.

### Test output summary (on the droplet, 2026-11-09)

```
python3 -m unittest discover -s tests
Ran 18 tests in 0.007s — OK
python3 -m cadence compile tests/fixtures/ep18-script.md --out score.json
-> 16 spans across S1–S5, acts hook/turn/turn/evidence/payoff,
   final payoff span intensity=climax-quiet, no 0.12 s pause anywhere.
```

Coverage: 14 rules + citations present · pause map exact with no 0.12 ·
tempo bounds + per-act defaults · all compiled tempos within bounds ·
breath-groups ≤14 words · ep.18 compiles to 5 segments, acts hook…payoff ·
one peak per sentence · author-markup peaks honoured (tier-2/tier-3,
encoded typography) · `||` emphasis pause (0.70 s) · persona header ·
series penultimate-rise (ep.18 S5 + synthetic) · antithesis mirroring
(ep.18 S1) · climax-quiet + closing pause at payoff end.

### Contract deviations

None. The score contract is emitted exactly as specified (no extra span
fields). R9 (circumflex rationing) and R14 (listen-back gate) are carried
in the rule set with parameters; their enforcement lands with the measurer
in a later milestone, as the rules' realisation notes state.
