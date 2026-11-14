PROPRIETARY — CYAN ECLIPSE TECHNOLOGIES LLC · COMPANY INTELLECTUAL PROPERTY

# Cyan Eclipse Cadence — CAD-16 error-discipline delta bench request

(INT — INTERNAL)

## What changed (shipped behaviour)

Commits 204b0ee (tool paths) and c9c2795 (transport leg) on main,
2026-11-14, curing sweep finding CAD-16 (HIGH, K4): the MCP tools/call
catch-all returned "%s: %s" % (type(exc).__name__, exc) — the exception
class name plus the raw exception message — to any caller, on a
Glama-listed public server.

Before (live-reproduced 2026-11-14 against the deployed tree):
cadence_scripts_read with an unknown id returned
"ValueError: unknown script_id 'no-such-script'".

After (live-verified against the deployed tree at c9c2795, GitHub main
== Shard E HEAD): every failure returns a stable error name plus one
plain-language sentence, e.g.
"SCRIPT_NOT_FOUND: No script with that identifier is stored in this
Cadence checkout. Use cadence_scripts_list to see the scripts that
are available."

## Claims for this delta

- D1 — No caller surface leaks internals: no tool path, transport
  path, or method refusal returns an exception class name, a raw
  exception message, or a stack fragment. Unexpected failures in any
  tool collapse to the single INTERNAL_ERROR refusal; the exception
  type is logged to stderr for operators only.
- D2 — Named shapes per member: cadence_scripts_list (INTERNAL_ERROR
  on data-layer failure) · cadence_scripts_read (SCRIPT_NOT_FOUND /
  INVALID_ARGUMENTS / INTERNAL_ERROR) · cadence_voices_list
  (INTERNAL_ERROR) · cadence_renders_list (INTERNAL_ERROR) ·
  cadence_renders_read (RENDER_RECORD_NOT_FOUND / INVALID_ARGUMENTS /
  INTERNAL_ERROR) · cadence_receipts_verify (INTERNAL_ERROR /
  RECEIPT_CHAIN_BROKEN via the receipt append) ·
  cadence_render_propose (SCRIPT_NOT_FOUND / INVALID_ARGUMENTS /
  INTERNAL_ERROR) · unserved tool names (UNKNOWN_TOOL) · non-object
  or missing arguments (INVALID_ARGUMENTS).
- D3 — Transport leg: malformed JSON returns a named -32700
  PARSE_ERROR (previously silently dropped); a non-object message
  returns -32600 INVALID_REQUEST; a fault in dispatch returns -32603
  INTERNAL_ERROR and the stdio loop keeps serving (previously a
  non-object message or any dispatch fault killed the process —
  refusal-by-crash); delete-shaped and unknown methods return
  METHOD_REFUSED / METHOD_NOT_FOUND with no caller-supplied echo.
- D4 — Nothing else moved: success results, tool list, schemas,
  receipt appends on success, and the non-executing posture are
  unchanged (no render/apply tool exists; cadence_render_propose
  still starts nothing). The web face was not touched.

## Evidence

- Regression suite mcp/tests/test_error_discipline.py (21 tests): one
  forced-failure test per tool member injecting exceptions carrying a
  secret marker (marker, class name, and internal path asserted
  absent from caller text), the named not-found/argument shapes, the
  broken-chain refusal, and five transport tests.
- Suites at c9c2795: MCP 29 pass / 0 fail; app 36 pass / 0 fail.
- Live-face transcript: sweep-cure-program-20261114/ws2-cadence/
  RECORD.md (failing calls exercised against the deployed Shard E
  tree after push; GitHub main == Shard E HEAD c9c2795).

## Verdict requested

PASS / PASS-WITH-CONDITIONS / CONCERN / FAIL on D1–D4. The cure is
BUILT and LIVE on the MCP face, bench-pending; no certified claim is
made until this delta verdict lands. This delta amends claim C6 of
BENCH-REQUEST-CONNECTOR-DELTA.md (refusals are now named-shape, not
only isError).

```json
{
 "app": "cadence",
 "kind": "cad16-error-discipline-delta",
 "claims": [
  {"id": "D1", "claim": "No caller surface (tool, transport, method refusal) returns an exception class name, raw exception message, or stack fragment; unexpected failures collapse to INTERNAL_ERROR, exception type logged to stderr only."},
  {"id": "D2", "claim": "Every tool member returns its named refusal shapes (SCRIPT_NOT_FOUND, RENDER_RECORD_NOT_FOUND, INVALID_ARGUMENTS, UNKNOWN_TOOL, RECEIPT_CHAIN_BROKEN, INTERNAL_ERROR) as a stable name plus a plain-language sentence."},
  {"id": "D3", "claim": "Transport: malformed JSON gets -32700 PARSE_ERROR, non-object messages -32600 INVALID_REQUEST, dispatch faults -32603 INTERNAL_ERROR with the loop surviving; methods return METHOD_REFUSED / METHOD_NOT_FOUND."},
  {"id": "D4", "claim": "Success paths, tool list, schemas, receipts, and the non-executing posture are unchanged; the web face was not touched."}
 ]
}
```

© Cyan Eclipse Technologies LLC — Proprietary · Company Intellectual Property
