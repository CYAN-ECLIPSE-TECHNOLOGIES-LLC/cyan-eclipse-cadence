PROPRIETARY — CYAN ECLIPSE TECHNOLOGIES LLC · COMPANY INTELLECTUAL PROPERTY

# Cyan Eclipse Cadence — MCP facade + connector delta bench request (custom.cadence)

(INT — INTERNAL)

## What is being benched

Two layers added 2026-11-12 under the MCP/Tools/Connectors commission: (1) the MCP facade at mcp/server.py in this home (Python 3 stdlib only, local stdio, no listener, no network code) implementing the commission scope for Cadence (SCOPE-MATRIX row 14) — script/performance reads, voice catalogue read, render-record reads (T0) and render proposals (T1); and (2) the connector at ~/workspace/custom-connectors/custom.cadence/ (client.py + tools-manifest.json + connector doc) granting exactly the served set. No render or apply tool exists on the facade at any posture. PRE-STANDARD: built to commission conventions; the agent-neutral standard document had not landed at build time.

## Claims for the delta

- C1 — Grant exactness: the connector's client allowlist, its tools-manifest.json, and the server's live tools/list agree exactly — 7 tools served and granted (cadence_scripts_list, cadence_scripts_read, cadence_voices_list, cadence_renders_list, cadence_renders_read, cadence_receipts_verify = T0; cadence_render_propose = T1); the two withheld names (cadence_render_start, cadence_render_apply) are not served at all and are refused client-side with their fences named in the manifest.
- C2 — Engine-truth: script reads parse and compile through the app's own cadence package (cadence_scripts_read on ep18-script returns rule_set rules-v1, 16 compiled spans, score_sha256 d480bdaeca97… produced by cadence.compile); the voice catalogue equals the cadence.helio constant at call time; render records equal the app's stored acceptance records (ep18-cadence 67.144 s).
- C3 — Propose-starts-nothing: cadence_render_propose returns applied: false / started: false and mints only a proposal record in the facade proposal area; no render starts and no audio is produced through this facade. The Cadence-only voice rule for reels is untouched.
- C4 — Receipt integrity: every tools/call appends a content-minimal receipt (hashes + metadata only, never script text) to the hash-chained mcp/receipts.jsonl; cadence_receipts_verify recomputes the chain and returned ok: true in the live runs; a broken chain fails closed (suite-proven).
- C5 — Determinism: cadence_scripts_read results are byte-identical canonical across the ECLIPSE_CADENCE_HOME route and the ECLIPSE_CADENCE_CMD SSH stdio bridge (sha256 8e889c3da272… both routes, receipt fields dropped).
- C6 — Refusals and confinement: unknown tools return isError; delete-shaped and unknown JSON-RPC methods are refused at the server layer; reads resolve only inside the app home's own stored data; the connector passes no credential and touches no network.

## Evidence on file

- TRANSCRIPT-20261112.md in the connector directory (live runs, both routes, refusal, determinism hash).
- tools-manifest.json in the connector directory and byte-identical at this home's root (sha256 8cc6cb7654a1…).
- Facade suite: python3 -m unittest discover -s mcp/tests — 8 pass / 0 fail. App regression suite: 36 pass / 0 fail.
- Client unit tests: 7/7 OK (resolution, withheld-name refusal, manifest consistency).

## Verdict requested

PASS / PASS-WITH-CONDITIONS / CONCERN / FAIL on the delta claims above. The facade + connector are BUILT bench-pending; no certified or shipped claim is made for them until this delta verdict lands.

```json
{
 "app": "cadence",
 "kind": "connector-delta",
 "claims": [
  {
   "id": "C1",
   "claim": "Grant exactness: client allowlist, tools-manifest.json, and live tools/list agree exactly - 7 served and granted (6 T0 + cadence_render_propose T1); cadence_render_start and cadence_render_apply are not served and are refused client-side with fences named."
  },
  {
   "id": "C2",
   "claim": "Engine-truth: script reads compile through the app's own cadence package (ep18-script: rules-v1, 16 spans, score_sha256 d480bdaeca97...); voice catalogue equals the cadence.helio constant; render records equal the app's stored acceptance records."
  },
  {
   "id": "C3",
   "claim": "Propose-starts-nothing: cadence_render_propose returns applied:false / started:false, mints only a facade proposal record, starts no render, produces no audio; the Cadence-only reel voice rule is untouched."
  },
  {
   "id": "C4",
   "claim": "Receipt integrity: content-minimal hash-chained receipts (hashes + metadata only); cadence_receipts_verify returned ok:true live; a broken chain fails closed."
  },
  {
   "id": "C5",
   "claim": "Determinism: cadence_scripts_read byte-identical canonical across HOME and CMD SSH-bridge routes (sha256 8e889c3da272..., receipt fields dropped)."
  },
  {
   "id": "C6",
   "claim": "Refusals and confinement: unknown tools isError; delete-shaped/unknown JSON-RPC methods refused server-side; reads resolve only inside the app home's stored data; no credential, no network."
  }
 ]
}
```

© Cyan Eclipse Technologies LLC — Proprietary · Company Intellectual Property
