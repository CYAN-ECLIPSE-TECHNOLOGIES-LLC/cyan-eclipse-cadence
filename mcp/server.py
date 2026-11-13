#!/usr/bin/env python3
"""Cyan Eclipse Cadence MCP server (facade) - agent-neutral MCP, stdio.

Local stdio only: line-delimited JSON-RPC, no listener, no network code.
Python 3 standard library only.

Engine-true by construction: script reads go through the app's own
cadence package (cadence.compile / cadence.parse), the voice catalogue
is read from cadence.helio (the app's own synthesis adapter constants),
and render records are read from the acceptance records the app itself
keeps under tests/fixtures/acceptance-*.json.

Scope (SCOPE-MATRIX row 14, CAD-AI-1): T0 reads (scripts, voice
catalogue, render records, receipt-chain verify) + T1 render proposal.
A proposal mints a record in mcp/proposals/ only - NO render is started
through this server; the White Report production pipeline's own gates
dispose of proposals. No T2 exists. The Cadence-only voice rule for
reels is untouched: this facade renders nothing.

Every tools/call appends a content-minimal receipt (hashes + metadata
only) to the hash-chained store at <state>/receipts.jsonl.
Delete-shaped and unknown methods are refused.
"""
import hashlib
import json
import os
import sys
from pathlib import Path

PROTOCOL = "2025-03-26"
SERVER_NAME = "cyan-eclipse-cadence-mcp"
SERVER_VERSION = "0.1.0"

HOME = Path(os.environ.get("CADENCE_MCP_HOME") or Path(__file__).resolve().parent.parent)
STATE = Path(os.environ.get("CADENCE_MCP_STATE_DIR") or (Path(__file__).resolve().parent))
RECEIPTS = STATE / "receipts.jsonl"
PROPOSALS = STATE / "proposals"

sys.path.insert(0, str(HOME))

DENY_SUBSTRINGS = ("delete", "remove", "drop", "destroy", "purge")


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# --- receipt chain -----------------------------------------------------------

def _read_receipts():
    if not RECEIPTS.exists():
        return []
    out = []
    for line in RECEIPTS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


def verify_chain():
    prev = None
    rows = _read_receipts()
    for i, row in enumerate(rows):
        body = {k: v for k, v in row.items() if k != "hash"}
        if row.get("prev") != prev or sha256_text(canonical(body)) != row.get("hash"):
            return {"ok": False, "diverging_seq": row.get("seq", i + 1), "count": len(rows),
                    "head": rows[-1]["hash"] if rows else None}
        prev = row["hash"]
    return {"ok": True, "diverging_seq": None, "count": len(rows), "head": prev}


def append_receipt(tool, args, result_summary):
    STATE.mkdir(parents=True, exist_ok=True)
    verdict = verify_chain()
    if not verdict["ok"]:
        raise RuntimeError("receipt chain broken at seq %s; refusing to append" % verdict["diverging_seq"])
    rows = _read_receipts()
    seq = len(rows) + 1
    body = {
        "seq": seq,
        "id": "R-%06d" % seq,
        "tool": tool,
        "args_sha256": sha256_text(canonical(args or {})),
        "result_sha256": sha256_text(canonical(result_summary)),
        "prev": rows[-1]["hash"] if rows else None,
    }
    body["hash"] = sha256_text(canonical(body))
    with open(RECEIPTS, "a", encoding="utf-8") as fh:
        fh.write(canonical(body) + "\n")
    return body["id"], body["hash"]


# --- app data layer (the app's own modules and stored records) ---------------

def _script_files():
    out = []
    fixtures = HOME / "tests" / "fixtures"
    if fixtures.is_dir():
        for p in sorted(fixtures.glob("*.md")):
            try:
                text = p.read_text(encoding="utf-8")
            except OSError:
                continue
            if "## SCRIPT" in text:
                out.append(p)
    return out


def _script_summary(path):
    from cadence.parse import parse_script
    text = path.read_text(encoding="utf-8")
    parsed = parse_script(text)
    return {
        "script_id": path.stem,
        "path": str(path.relative_to(HOME)),
        "sha256": sha256_file(path),
        "persona": parsed.persona,
        "segments": [{"segment": s.seg_id, "text_raw": s.text_raw} for s in parsed.segments],
        "segment_count": len(parsed.segments),
    }


def _voice_catalogue():
    from cadence import helio
    return [{
        "voice_id": helio.VOICE,
        "engine": "helio",
        "adapter": "cadence/helio.py (the app's own synthesis adapter)",
        "timeout_secs": helio.TIMEOUT_SECS,
        "note": "Catalogue as stored in the app. This facade renders nothing; "
                "reel voice rules live in the production pipeline, not here.",
    }]


def _render_records():
    records = []
    fixtures = HOME / "tests" / "fixtures"
    if fixtures.is_dir():
        for p in sorted(fixtures.glob("acceptance-*.json")):
            data = json.loads(p.read_text(encoding="utf-8"))
            episode = data.get("episode", p.stem)
            for take in ("shipped", "cadence"):
                block = data.get(take)
                if not isinstance(block, dict):
                    continue
                gate = block.get("gate") or {}
                records.append({
                    "record_id": "%s-%s" % (episode, take),
                    "episode": episode,
                    "take": take,
                    "audio": block.get("audio"),
                    "voice": _voice_catalogue()[0]["voice_id"] if take == "cadence" else None,
                    "duration_s": block.get("duration_s"),
                    "basis": block.get("basis"),
                    "in_band_pct": gate.get("in_band_pct"),
                    "in_band_count": gate.get("in_band_count"),
                    "judged_count": gate.get("judged_count"),
                    "state": "measured",
                    "source": str(p.relative_to(HOME)),
                })
            receipt = data.get("receipt") or {}
            if receipt.get("receipt_sha256"):
                records.append({
                    "record_id": "%s-receipt" % episode,
                    "episode": episode,
                    "take": "receipt",
                    "audio": None,
                    "voice": None,
                    "duration_s": None,
                    "basis": None,
                    "in_band_pct": receipt.get("span_in_band_pct"),
                    "in_band_count": None,
                    "judged_count": None,
                    "state": "sealed",
                    "receipt_sha256": receipt.get("receipt_sha256"),
                    "source": str(p.relative_to(HOME)),
                })
    return records


# --- tools -------------------------------------------------------------------

def t_scripts_list(args):
    return {"scripts": [{k: s[k] for k in ("script_id", "path", "sha256", "segment_count")}
                         for s in (_script_summary(p) for p in _script_files())]}


def t_scripts_read(args):
    sid = args.get("script_id")
    for p in _script_files():
        if p.stem == sid:
            from cadence.compile import compile_file
            summary = _script_summary(p)
            score = compile_file(str(p))
            summary["rule_set"] = score.get("rule_set")
            summary["span_count"] = len(score.get("spans", []))
            summary["score_sha256"] = sha256_text(canonical(score))
            return summary
    raise ValueError("unknown script_id %r" % (sid,))


def t_voices_list(args):
    return {"voices": _voice_catalogue()}


def t_renders_list(args):
    keys = ("record_id", "episode", "take", "voice", "duration_s", "state", "in_band_pct")
    return {"renders": [{k: r.get(k) for k in keys} for r in _render_records()]}


def t_renders_read(args):
    rid = args.get("record_id")
    for r in _render_records():
        if r["record_id"] == rid:
            return r
    raise ValueError("unknown record_id %r" % (rid,))


def t_receipts_verify(args):
    return verify_chain()


def t_render_propose(args):
    sid = args.get("script_id")
    known = {p.stem for p in _script_files()}
    if sid not in known:
        raise ValueError("unknown script_id %r" % (sid,))
    PROPOSALS.mkdir(parents=True, exist_ok=True)
    existing = sorted(PROPOSALS.glob("P-*.json"))
    seq = len(existing) + 1
    pid = "P-%06d" % seq
    proposal = {
        "proposal_id": pid,
        "kind": "cadence_render_proposal",
        "script_id": sid,
        "voice": _voice_catalogue()[0]["voice_id"],
        "note": args.get("note"),
        "applied": False,
        "started": False,
        "state": "proposed",
        "disposition": "The White Report production pipeline's own gates dispose "
                       "of this proposal. No render was started by this call.",
    }
    (PROPOSALS / (pid + ".json")).write_text(
        json.dumps(proposal, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return proposal


_RECEIPT_PROPS = {
    "receipt_id": {"type": "string",
                   "description": "Identifier of the content-minimal receipt appended for this successful call."},
    "receipt_head": {"type": "string",
                     "description": "Hash head of the facade receipt chain after this call's receipt was appended."},
}
_READ_ANN = {"readOnlyHint": False, "destructiveHint": False,
             "idempotentHint": False, "openWorldHint": False}

TOOLS = [
    {"name": "cadence_scripts_list", "class": "T0", "fn": t_scripts_list,
     "title": "List Cadence performance scripts",
     "description": "List the performance scripts stored in this Cadence checkout, with each script's identifier, repository-relative path, SHA-256 of its file bytes, and segment count. Use this first to discover a valid script_id for cadence_scripts_read or cadence_render_propose. This tool does not compile a score or start a render; use cadence_scripts_read when persona, segment text, and compiled-score evidence are needed. Behaviour: local stdio only, with no network, authentication, credentials, or external service; a successful call appends a content-minimal hash receipt (argument and result hashes only) to the facade receipt chain. Returns an object with scripts, receipt_id, and receipt_head.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
     "outputSchema": {"type": "object", "properties": {
         "scripts": {"type": "array", "items": {"type": "object", "properties": {
             "script_id": {"type": "string"}, "path": {"type": "string"},
             "sha256": {"type": "string"}, "segment_count": {"type": "integer"}},
             "required": ["script_id", "path", "sha256", "segment_count"],
             "additionalProperties": False}}, **_RECEIPT_PROPS},
         "required": ["scripts", "receipt_id", "receipt_head"],
         "additionalProperties": False},
     "annotations": dict(_READ_ANN)},
    {"name": "cadence_scripts_read", "class": "T0", "fn": t_scripts_read,
     "title": "Read and compile one Cadence script",
     "description": "Read one stored performance script and compile its performance score with Cadence's own compiler, returning the script identifier, path, file SHA-256, persona when present, segment texts, segment count, rule set, compiled span count, and canonical score SHA-256. Use when the actual script and compiler evidence are needed; use cadence_scripts_list for discovery only and cadence_render_propose only to record a non-executing render proposal. An unknown identifier returns an error and creates no proposal. Behaviour: local and deterministic for unchanged files; no synthesis, network, authentication, or credential use; a successful call appends a content-minimal hash receipt. Returns the script object plus receipt_id and receipt_head.",
     "inputSchema": {"type": "object", "properties": {
         "script_id": {"type": "string",
                       "description": "Exact stored script identifier without the .md extension, for example ep18-script. Obtain valid identifiers from cadence_scripts_list."}},
                     "required": ["script_id"], "additionalProperties": False},
     "outputSchema": {"type": "object", "properties": {
         "script_id": {"type": "string"}, "path": {"type": "string"},
         "sha256": {"type": "string"},
         "persona": {"type": ["object", "null"], "properties": {
             "speaker": {"type": "string"}, "to": {"type": "string"},
             "want": {"type": "string"}}, "additionalProperties": False},
         "segments": {"type": "array", "items": {"type": "object", "properties": {
             "segment": {"type": "string"}, "text_raw": {"type": "string"}},
             "required": ["segment", "text_raw"], "additionalProperties": False}},
         "segment_count": {"type": "integer"}, "rule_set": {"type": "string"},
         "span_count": {"type": "integer"}, "score_sha256": {"type": "string"},
         **_RECEIPT_PROPS},
         "required": ["script_id", "path", "sha256", "persona", "segments",
                      "segment_count", "rule_set", "span_count", "score_sha256",
                      "receipt_id", "receipt_head"],
         "additionalProperties": False},
     "annotations": dict(_READ_ANN)},
    {"name": "cadence_voices_list", "class": "T0", "fn": t_voices_list,
     "title": "List the stored Cadence voice catalogue",
     "description": "List the voice catalogue exactly as stored in this Cadence checkout's synthesis-adapter constants, returning voice_id, engine, adapter, timeout_secs, and an explanatory note for each entry. Use to identify the stored voice before reading scripts or proposing a render; use cadence_renders_list to find which voice a stored render record used. This tool cannot add, select, change, or certify a voice and it does not synthesize audio. Behaviour: local stdio only, with no network, authentication, credentials, or external service; a successful call appends a content-minimal hash receipt. Returns an object with voices, receipt_id, and receipt_head.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
     "outputSchema": {"type": "object", "properties": {
         "voices": {"type": "array", "items": {"type": "object", "properties": {
             "voice_id": {"type": "string"}, "engine": {"type": "string"},
             "adapter": {"type": "string"}, "timeout_secs": {"type": "integer"},
             "note": {"type": "string"}},
             "required": ["voice_id", "engine", "adapter", "timeout_secs", "note"],
             "additionalProperties": False}}, **_RECEIPT_PROPS},
         "required": ["voices", "receipt_id", "receipt_head"],
         "additionalProperties": False},
     "annotations": dict(_READ_ANN)},
    {"name": "cadence_renders_list", "class": "T0", "fn": t_renders_list,
     "title": "List stored Cadence render records",
     "description": "List Cadence's stored render and acceptance records with record_id, episode, take, voice, duration_s, state, and in_band_pct. Use this first to discover a valid record_id for cadence_renders_read. These are historical stored records, not live render status; this tool cannot start, retry, or inspect an in-progress synthesis. Behaviour: local file reads only, with no network, authentication, credentials, or audio playback; a successful call appends a content-minimal hash receipt. Returns an object with renders, receipt_id, and receipt_head, with null fields where a record does not carry a value.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
     "outputSchema": {"type": "object", "properties": {
         "renders": {"type": "array", "items": {"type": "object", "properties": {
             "record_id": {"type": "string"}, "episode": {"type": "string"},
             "take": {"type": "string"}, "voice": {"type": ["string", "null"]},
             "duration_s": {"type": ["number", "null"]},
             "state": {"type": "string"},
             "in_band_pct": {"type": ["number", "null"]}},
             "required": ["record_id", "episode", "take", "voice", "duration_s",
                          "state", "in_band_pct"],
             "additionalProperties": False}}, **_RECEIPT_PROPS},
         "required": ["renders", "receipt_id", "receipt_head"],
         "additionalProperties": False},
     "annotations": dict(_READ_ANN)},
    {"name": "cadence_renders_read", "class": "T0", "fn": t_renders_read,
     "title": "Read one stored Cadence render record",
     "description": "Read one stored render or acceptance record in full by exact record_id, returning its episode, take, audio reference, voice, duration, basis, gate counts and percentage, state, source file, and stored receipt hash when the record is a sealed receipt. Use after cadence_renders_list when full evidence is needed; use cadence_receipts_verify for the facade's own call-receipt chain rather than a render record's stored receipt. An unknown record_id returns an error. Behaviour: local file read only; no synthesis, network, authentication, credentials, or external service; a successful call appends a content-minimal hash receipt. Returns the record plus receipt_id and receipt_head.",
     "inputSchema": {"type": "object", "properties": {
         "record_id": {"type": "string",
                       "description": "Exact stored render-record identifier, for example ep18-cadence, ep18-shipped, or ep18-receipt. Obtain valid identifiers from cadence_renders_list."}},
                     "required": ["record_id"], "additionalProperties": False},
     "outputSchema": {"type": "object", "properties": {
         "record_id": {"type": "string"}, "episode": {"type": "string"},
         "take": {"type": "string"}, "audio": {"type": ["string", "null"]},
         "voice": {"type": ["string", "null"]},
         "duration_s": {"type": ["number", "null"]},
         "basis": {"type": ["string", "null"]},
         "in_band_pct": {"type": ["number", "null"]},
         "in_band_count": {"type": ["integer", "null"]},
         "judged_count": {"type": ["integer", "null"]},
         "state": {"type": "string"}, "source": {"type": "string"},
         "receipt_sha256": {"type": "string"}, **_RECEIPT_PROPS},
         "required": ["record_id", "episode", "take", "audio", "voice",
                      "duration_s", "basis", "in_band_pct", "in_band_count",
                      "judged_count", "state", "source", "receipt_id",
                      "receipt_head"],
         "additionalProperties": False},
     "annotations": dict(_READ_ANN)},
    {"name": "cadence_receipts_verify", "class": "T0", "fn": t_receipts_verify,
     "title": "Verify the Cadence facade receipt chain",
     "description": "Recompute this MCP facade's hash-chained call receipts and report whether the chain verifies, the first diverging sequence when it does not, the receipt count checked, and the chain head. Use to audit whether recorded facade calls remain internally consistent; use cadence_renders_read when the target is a stored render record rather than the facade receipt chain. Behaviour: the verdict describes the chain before this call's own successful-call receipt is appended; the server has no network, authentication, or credential surface, and verification changes no record other than that normal receipt append. Returns an object with ok, diverging_seq, count, head, receipt_id, and receipt_head.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
     "outputSchema": {"type": "object", "properties": {
         "ok": {"type": "boolean"},
         "diverging_seq": {"type": ["integer", "null"]},
         "count": {"type": "integer"},
         "head": {"type": ["string", "null"]}, **_RECEIPT_PROPS},
         "required": ["ok", "diverging_seq", "count", "head", "receipt_id",
                      "receipt_head"],
         "additionalProperties": False},
     "annotations": dict(_READ_ANN)},
    {"name": "cadence_render_propose", "class": "T1", "fn": t_render_propose,
     "title": "Record a non-executing Cadence render proposal",
     "description": "Create a local render proposal for one stored script, returning proposal_id, script_id, the stored voice, the optional note, applied: false, started: false, state: proposed, and the disposition text. Use when a render should be recorded for later human or production-pipeline review; do not use it to produce audio, because no render-start or render-apply tool exists on this server. An unknown script_id returns an error. Behaviour: each successful call writes one new proposal JSON file in the facade state area and appends a content-minimal hash receipt; it makes no network call, uses no credentials, invokes no synthesis engine, and is not idempotent because repeated calls create separate proposals. Returns the proposal plus receipt_id and receipt_head.",
     "inputSchema": {"type": "object", "properties": {
         "script_id": {"type": "string",
                       "description": "Exact stored script identifier without the .md extension, for example ep18-script. Obtain valid identifiers from cadence_scripts_list."},
         "note": {"type": "string",
                  "description": "Optional free-text note stored verbatim in the proposal record. It has no execution effect and does not select a voice, change a script, or start a render."}},
                     "required": ["script_id"], "additionalProperties": False},
     "outputSchema": {"type": "object", "properties": {
         "proposal_id": {"type": "string"},
         "kind": {"type": "string"}, "script_id": {"type": "string"},
         "voice": {"type": "string"}, "note": {"type": ["string", "null"]},
         "applied": {"type": "boolean"}, "started": {"type": "boolean"},
         "state": {"type": "string"}, "disposition": {"type": "string"},
         **_RECEIPT_PROPS},
         "required": ["proposal_id", "kind", "script_id", "voice", "note",
                      "applied", "started", "state", "disposition",
                      "receipt_id", "receipt_head"],
         "additionalProperties": False},
     "annotations": {"readOnlyHint": False, "destructiveHint": False,
                     "idempotentHint": False, "openWorldHint": False}},
]
TOOL_MAP = {t["name"]: t for t in TOOLS}


def tool_list_payload():
    return [{"name": t["name"], "title": t["title"],
             "description": t["description"],
             "inputSchema": t["inputSchema"],
             "outputSchema": t["outputSchema"],
             "annotations": t["annotations"]} for t in TOOLS]


# --- JSON-RPC loop -------------------------------------------------------------

def handle(msg):
    method = msg.get("method", "")
    mid = msg.get("id")
    low = method.lower()
    if any(d in low for d in DENY_SUBSTRINGS):
        return {"jsonrpc": "2.0", "id": mid,
                "error": {"code": -32601, "message": "method refused: delete-shaped methods are denylisted"}}
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}}}
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": tool_list_payload()}}
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        tool = TOOL_MAP.get(name)
        if tool is None:
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "isError": True, "content": [{"type": "text", "text":
                    "unknown tool %r; no render/apply tool exists on this facade" % (name,)}]}}
        try:
            result = tool["fn"](args)
            rid, head = append_receipt(name, args, result)
            result = dict(result)
            result["receipt_id"] = rid
            result["receipt_head"] = head
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}}
        except Exception as exc:
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "isError": True, "content": [{"type": "text", "text": "%s: %s" % (type(exc).__name__, exc)}]}}
    return {"jsonrpc": "2.0", "id": mid,
            "error": {"code": -32601, "message": "method not found / refused: %s" % method}}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
