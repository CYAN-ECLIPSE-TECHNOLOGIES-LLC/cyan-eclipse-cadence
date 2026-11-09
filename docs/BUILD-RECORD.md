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

## M2 — Helio adapter + renderer (2026-11-09)

- `cadence/helio.py` — Helio engine adapter (stdlib only). Call text is
  the span's `text_encoded` verbatim (compiler owns typography; the
  adapter never re-encodes). Route detected once per process: primary
  `--format wav` via the VM-local `/opt/hatch/bin/tts speak --voice
  avocado_v2:MAI_03`; this bridge prepends a small ID3 tag to the RIFF
  stream, so the adapter strips it in place and validates with the
  stdlib wave reader. Fallback (wav error/unreadable): `--format mp3`
  + ffmpeg decode. The route used is returned per span and recorded in
  the render log. `--speed` is never passed (hard-blocked on the VM by
  design; pacing is the renderer's per-unit tempo, never the engine's).
- `cadence/render.py` + `python3 -m cadence render <score.json> --out
  <joined.wav> --workdir <dir>` — per span: synth → decode/normalise
  to 48 kHz mono wav → per-span tempo via ffmpeg `atempo=<span.tempo>`
  (per-unit only; a global atempo pass over the joined file is
  forbidden — legacy defect F1) → per-span level from the intensity
  map (reduced 0.79 / −2 dB · climax-quiet 0.71 / −3 dB · normal 1.0)
  → silence of `pause_after.seconds` appended (skipped after the final
  span) → concat of same-format wavs (ffmpeg concat demuxer) → out
  wav 48 kHz mono. Emits `<out>.timings.json` ([{span_id, segment,
  act, start_s, end_s, pause_after_s}], measured from the actual
  assembled parts) and `<out>.renderlog.json` (per-span synth route,
  raw dur, tempo, gain, out dur; joined vs expected duration). ffmpeg
  is the primary audio path; a stdlib fallback (duration-exact linear
  resampling for tempo) covers ffmpeg-less hosts such as this droplet.
- `tests/test_render.py` — renderer unit tests with `helio.synth_span`
  stubbed (stdlib wave-module sine, 0.30 s/word; no tts on this host):
  joined duration = Σ span durs × 1/tempo + Σ pauses (±5%) · pause set
  exactly {0.35, 0.70, 0.90, 0.0} with no 0.12 flat-join constant ·
  timings monotonic and measured · render log records route/tempo/
  durations · intensity gain map exact.

### Test output summary (on the droplet, 2026-11-09)

```
python3 -m unittest discover -s tests
Ran 23 tests in 1.051s — OK   (18 M1 + 5 M2)
```

### Regression fixture (rendered on the VM, same day)

ep.18 script compiled + rendered through Cadence on the VM (tts is
VM-local; droplet code arrived as a throwaway git-bundle checkout —
no maintained VM tree). Synth route: wav (ID3 stripped). Artefacts in
the White Report voice-humanization folder, `fixture-ep18/`; the house
pipeline under `reels/ep18/` is untouched.

### M2 addendum — CLI merge fix (2026-11-09)

The M2 commit's `cadence/cli.py` was overwritten in the working tree by
the parallel M3 lane's CLI (compile/measure/receipt) between the M2
file drop and the M2 commit, so the committed CLI lacked the `render`
subcommand. Fixed by merging all four subcommands
(compile/render/measure/receipt) into one CLI in a follow-up commit;
the M3 lane's untracked `measure.py`/`receipt.py`/`test_measure.py`
were left untouched for that lane to commit.

## M3 — Prosody measurer + human reference bands + acceptance gate + performance receipt (2026-11-09)

- `cadence/measure.py` + CLI `python3 -m cadence measure <audio.wav> [--timings t.json] [--score s.json] --out metrics.json`:
  decode to 48 kHz mono via ffmpeg when present (stdlib WAV read otherwise);
  pauses via ffmpeg silencedetect (noise=-40 dB, d=0.08) with an energy-based
  fallback at identical parameters, classified micro <0.25 s · breath
  0.25–0.55 s · emphasis >0.55 s, with share+count distribution, mean/max;
  F0 by autocorrelation (frame 40 ms, hop 20 ms, 50–400 Hz, periodicity
  ≥0.35) → mean/median Hz, SD in semitones vs median, voiced fraction, and
  per-act F0 SD when timings+score are given; rate = overall wpm from the
  score when given, local rate CV per-span (basis `spans`) or, for score-less
  audio, silence-delimited chunk-duration CV (basis `chunks`; chunk CV is
  always also reported so band comparisons stay like-for-like); loudness
  dynamics = per-span/per-chunk RMS dB, dynamic range p90−p10. numpy/scipy
  are lazy imports: the module is importable and testable stdlib-only
  (droplet has neither numpy nor ffmpeg; real measurement runs in the VM
  venv + ffmpeg). `evaluate_bands()` in the same module is the acceptance
  gate: per-metric in/out + in-band %.
- `cadence/receipt.py` + CLI `python3 -m cadence receipt --render <wav>
  --score <score.json> --metrics <metrics.json> --bands <bands.json> --out
  receipt.json`: render/score SHA-256, rule set, per-span directives +
  measured {dur_s, pause_after_s} + verdict (pause-adherence, span-rate and
  span-F0-SD checks) with reasons, totals in-band %, prev_receipt_sha256
  null, receipt_sha256 over the canonical JSON of all other fields.
- `data/reference-bands-v1.json` — human reference bands measured
  identically (cadence-metrics-v1) from 3 LibriVox non-fiction samples
  (Emerson, *Essays, First Series* ch.03 · Nicolay & Hay, *Abraham Lincoln:
  A History* vol.6 ch.03 · Russell, *Icarus; or, The Future of Science*
  sec.03; 180 s each from a 30 s offset, past the LibriVox intro; IA ids,
  URLs and fetch date recorded per sample in the file). Band rule: per
  metric [min×0.9, max×1.1] across the 3; SD/CV metrics widened ×0.8/×1.2;
  shares/fractions clamped to [0,1]. No rate-wpm band: reference audio has
  no transcript, so rate is banded on chunk-basis CV only.
- Tests: `tests/test_measure.py` (stdlib-only, synthetic sine+silence WAV):
  pause-classifier bins · distribution shares · F0 of a 120 Hz sine within
  ±5 % · metrics schema keys · breath-class pause detection. Full suite on
  the droplet: 28 tests, OK.
- Acceptance (ep.18 shipped VO vs Cadence fixture): appended below when the
  M2 fixture lands.

### M3 acceptance — ep.18 (2026-11-09)

Measured identically against reference-bands-v1: shipped VO
(`reels/ep18/work/vo-joined.wav`, 52.94 s, chunks basis, no score) vs the
M2 Cadence fixture (`fixture-ep18/cadence-ep18.wav`, 67.14 s, spans basis,
17 spans). Full table: `fixture-ep18/acceptance-ep18.md` + `.json`;
sealed receipt `fixture-ep18/receipt-ep18.json`.

- Metric gate in-band: shipped 70 % (7/10) · Cadence 50 % (5/10).
- Cadence in-band: F0 mean 122.15 Hz · F0 SD 5.50 st · pause breath share
  0.28 · pause max 1.381 s · rate CV (chunk) 0.587. Out-of-band: voiced
  fraction 0.391 (shipped also out, 0.416 — both TTS takes sit below the
  human band 0.538–0.723), pause mean 0.479 s (band ≤0.475, marginal),
  pause micro share 0.380 (band ≥0.386, marginal), pause emphasis share
  0.34 (designed emphasis/act pauses exceed the human share band),
  chunk-basis dynamic range 5.3 dB (span-basis is 2.46 dB, in-band — the
  chunk figure is inflated by pause-split quiet chunks).
- Span-level receipt: 17/17 in-band (100 %) — every span's measured pause
  matches its directive within tolerance; no re-compile candidates at
  span level. The metric-gate misses are global-shape findings (pause
  budget + voiced fraction), i.e. rule-set/band calibration input for
  M4, not span defects.
- Cadence-only figures: overall 134.0 wpm · span rate CV 0.227 ·
  per-act F0 SD hook 6.61 / turn 4.99 / evidence 6.28 / payoff 5.20 st.
