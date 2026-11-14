"""CAD-16 regression tests: MCP error discipline for Cyan Eclipse Cadence.

Every tool path must return a named, plain-language refusal on failure:
a stable error name plus a human sentence, and NEVER an exception class
name, a raw exception message, or a stack fragment.

One forced-failure test per tool member, plus the named not-found /
bad-argument shapes, the unknown-tool shape, and the receipt-chain
refusal. Forced failures inject exceptions carrying a secret marker;
the marker, the class name, and any internal path must not appear in
the text returned to the caller.
"""
import io
import json
import os
import re
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HOME)

import mcp.server as mod  # noqa: E402

SECRET = "INTERNAL-SECRET-MARKER-9f3"
BANNED = [
    SECRET, "ValueError", "RuntimeError", "KeyError", "OSError",
    "AttributeError", "TypeError", "JSONDecodeError", "Traceback",
    "/home/", "unknown script_id", "unknown record_id",
]
SHAPE = re.compile(r"^[A-Z][A-Z_]+: [A-Z].*\.$")


def call_tool(name, args=None):
    resp = mod.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": name, "arguments": args or {}}})
    return resp["result"]


def error_text(name, args=None):
    result = call_tool(name, args)
    assert result.get("isError"), result
    return result["content"][0]["text"]


class ErrorDisciplineTests(unittest.TestCase):
    def setUp(self):
        self._saved = (mod.STATE, mod.RECEIPTS, mod.PROPOSALS)
        self.state = tempfile.mkdtemp(prefix="cadence-k4-test-")
        mod.STATE = mod.Path(self.state)
        mod.RECEIPTS = mod.STATE / "receipts.jsonl"
        mod.PROPOSALS = mod.STATE / "proposals"

    def tearDown(self):
        mod.STATE, mod.RECEIPTS, mod.PROPOSALS = self._saved

    def assert_refusal(self, text, name):
        self.assertTrue(text.startswith(name + ": "), text)
        self.assertRegex(text, SHAPE)
        for banned in BANNED:
            self.assertNotIn(banned, text)

    # -- per-member forced failures (class cure, not spot fix) ----------

    def test_scripts_list_forced_failure(self):
        with mock.patch.object(mod, "_script_summary",
                               side_effect=RuntimeError(SECRET)):
            self.assert_refusal(error_text("cadence_scripts_list"),
                                "INTERNAL_ERROR")

    def test_scripts_read_forced_failure(self):
        import cadence.compile as compile_mod
        with mock.patch.object(compile_mod, "compile_file",
                               side_effect=ValueError(SECRET)):
            self.assert_refusal(
                error_text("cadence_scripts_read",
                           {"script_id": "ep18-script"}),
                "INTERNAL_ERROR")

    def test_voices_list_forced_failure(self):
        with mock.patch.object(mod, "_voice_catalogue",
                               side_effect=KeyError(SECRET)):
            self.assert_refusal(error_text("cadence_voices_list"),
                                "INTERNAL_ERROR")

    def test_renders_list_forced_failure(self):
        with mock.patch.object(mod, "_render_records",
                               side_effect=OSError(SECRET + " /home/x")):
            self.assert_refusal(error_text("cadence_renders_list"),
                                "INTERNAL_ERROR")

    def test_renders_read_forced_failure(self):
        with mock.patch.object(mod, "_render_records",
                               side_effect=RuntimeError(SECRET)):
            self.assert_refusal(
                error_text("cadence_renders_read",
                           {"record_id": "ep18-cadence"}),
                "INTERNAL_ERROR")

    def test_receipts_verify_forced_failure(self):
        with mock.patch.object(mod, "verify_chain",
                               side_effect=RuntimeError(SECRET)):
            self.assert_refusal(error_text("cadence_receipts_verify"),
                                "INTERNAL_ERROR")

    def test_receipts_verify_corrupt_store(self):
        mod.RECEIPTS.write_text("this is not json\n", encoding="utf-8")
        self.assert_refusal(error_text("cadence_receipts_verify"),
                            "INTERNAL_ERROR")

    def test_render_propose_forced_failure(self):
        with mock.patch.object(mod, "_voice_catalogue",
                               side_effect=RuntimeError(SECRET)):
            self.assert_refusal(
                error_text("cadence_render_propose",
                           {"script_id": "ep18-script"}),
                "INTERNAL_ERROR")

    # -- named shapes for the ordinary failures -------------------------

    def test_scripts_read_not_found_is_named(self):
        self.assert_refusal(
            error_text("cadence_scripts_read", {"script_id": "nope"}),
            "SCRIPT_NOT_FOUND")

    def test_render_propose_not_found_is_named(self):
        self.assert_refusal(
            error_text("cadence_render_propose", {"script_id": "nope"}),
            "SCRIPT_NOT_FOUND")

    def test_renders_read_not_found_is_named(self):
        self.assert_refusal(
            error_text("cadence_renders_read", {"record_id": "nope"}),
            "RENDER_RECORD_NOT_FOUND")

    def test_missing_arguments_are_named(self):
        self.assert_refusal(error_text("cadence_scripts_read"),
                            "INVALID_ARGUMENTS")
        self.assert_refusal(error_text("cadence_renders_read"),
                            "INVALID_ARGUMENTS")
        self.assert_refusal(error_text("cadence_render_propose"),
                            "INVALID_ARGUMENTS")

    def test_non_object_arguments_are_named(self):
        resp = mod.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                           "params": {"name": "cadence_voices_list",
                                      "arguments": ["not", "an", "object"]}})
        text = resp["result"]["content"][0]["text"]
        self.assertTrue(resp["result"].get("isError"))
        self.assert_refusal(text, "INVALID_ARGUMENTS")

    def test_unknown_tool_is_named(self):
        self.assert_refusal(error_text("cadence_render_start"),
                            "UNKNOWN_TOOL")

    def test_broken_receipt_chain_is_named(self):
        # A receipt row whose hash does not verify: the tool itself
        # succeeds, but the receipt append must refuse by name.
        row = {"seq": 1, "id": "R-000001", "tool": "x",
               "args_sha256": "0" * 64, "result_sha256": "0" * 64,
               "prev": None, "hash": "0" * 64}
        mod.RECEIPTS.write_text(json.dumps(row) + "\n", encoding="utf-8")
        self.assert_refusal(error_text("cadence_voices_list"),
                            "RECEIPT_CHAIN_BROKEN")

    def test_success_path_unchanged(self):
        result = call_tool("cadence_voices_list")
        self.assertFalse(result.get("isError"))
        out = json.loads(result["content"][0]["text"])
        self.assertIn("voices", out)
        self.assertIn("receipt_id", out)


if __name__ == "__main__":
    unittest.main()
