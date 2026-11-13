# Cyan Eclipse Cadence MCP

**Free and open source under the MIT License.** Cyan Eclipse Cadence MCP is a
local stdio Model Context Protocol server for inspecting Cadence performance
scripts, the voice catalogue stored in a Cadence checkout, stored render and
acceptance records, and the server's hash-chained call receipts. It also records
render proposals. It works with any MCP-compatible host; no account, API key,
credential, or paid service is required.

The MCP surface is deliberately non-executing: it has **no render-start or
render-apply tool**, invokes no speech-synthesis engine, makes no network calls,
and cannot spend money or third-party synthesis credits. A proposal is only a
local JSON record for later review.

## Run the MCP server

Requirements: Python 3.10 or newer and this repository. The server uses only
the Python standard library.

```sh
git clone https://github.com/CYAN-ECLIPSE-TECHNOLOGIES-LLC/cyan-eclipse-cadence.git
cd cyan-eclipse-cadence
python3 server.py
```

Generic MCP host configuration:

```json
{
  "mcpServers": {
    "cyan-eclipse-cadence": {
      "command": "python3",
      "args": ["/absolute/path/to/cyan-eclipse-cadence/server.py"],
      "env": {
        "CADENCE_MCP_STATE_DIR": "/absolute/path/to/writable/cadence-state"
      }
    }
  }
}
```

`CADENCE_MCP_STATE_DIR` is recommended because successful calls append
content-minimal hash receipts and proposals are written as JSON files. Set
`CADENCE_MCP_HOME` to another Cadence checkout when the server should inspect
that checkout's stored scripts and acceptance records instead of its own.

## MCP tools

| Tool | Purpose |
| --- | --- |
| `cadence_scripts_list` | Discover stored scripts and their hashes and segment counts |
| `cadence_scripts_read` | Read one script and compile its performance score |
| `cadence_voices_list` | Read the voice catalogue stored in the checkout |
| `cadence_renders_list` | List stored render and acceptance records |
| `cadence_renders_read` | Read one stored render or acceptance record in full |
| `cadence_receipts_verify` | Recompute and verify the facade receipt hash chain |
| `cadence_render_propose` | Write a local, non-executing render proposal |

The repository includes a sample script and acceptance record so the tools can
be tried immediately. The server's reads are computed from the checkout's own
Cadence compiler, synthesis-adapter constants, and acceptance files rather than
from a second copied dataset.

## What Cadence is

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
climax (quieter, not louder).

## MCP server scope

- Rule set v1 as cited YAML (`rules/rules-v1.yaml`, 14 rules, R1–R14).
- Performance compiler for the house segment script format
  (`## SCRIPT` section, `[Sn]` segments, optional persona header).
- Stored render and acceptance-record inspection.
- Hash-chained, content-minimal receipts for successful facade calls.
- Local render proposals that start and apply nothing.
- Stdlib-only Python; stdlib `unittest` test suites.

## Not exposed by the MCP server

- Speech synthesis or any render execution.
- Voice selection or voice changes.
- Network calls, accounts, credentials, or paid services.
- Automatic script rewriting (the compiler flags; a human rewrites).
- Any UI beyond the MCP server, CLI, and score JSON.

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
