"""MCP facade tests for Cyan Eclipse Cadence (stdlib unittest).

Spawns the real server over stdio against the app's real stored data,
with receipt/proposal state redirected to a temp dir.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "..", "server.py")
HOME = os.path.abspath(os.path.join(HERE, "..", ".."))


class Server:
    def __init__(self, state):
        env = dict(os.environ)
        env["CADENCE_MCP_STATE_DIR"] = state
        self.proc = subprocess.Popen(
            [sys.executable, SERVER], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, text=True, env=env)
        self.seq = 0
        self.call("initialize", {"protocolVersion": "2025-03-26",
                                 "capabilities": {},
                                 "clientInfo": {"name": "test", "version": "0"}})

    def call(self, method, params=None, raw_method=None):
        self.seq += 1
        body = {"jsonrpc": "2.0", "id": self.seq,
                "method": raw_method or method, "params": params or {}}
        self.proc.stdin.write(json.dumps(body) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        return json.loads(line)

    def tool(self, name, args=None):
        resp = self.call("tools/call", {"name": name, "arguments": args or {}})
        result = resp["result"]
        if result.get("isError"):
            raise AssertionError(result["content"][0]["text"])
        return json.loads(result["content"][0]["text"])

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=10)


class FacadeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state = tempfile.mkdtemp(prefix="cadence-mcp-test-")
        cls.srv = Server(cls.state)

    @classmethod
    def tearDownClass(cls):
        cls.srv.close()

    def test_01_surface(self):
        resp = self.srv.call("tools/list")
        names = [t["name"] for t in resp["result"]["tools"]]
        self.assertEqual(names, ["cadence_scripts_list", "cadence_scripts_read",
                                 "cadence_voices_list", "cadence_renders_list",
                                 "cadence_renders_read", "cadence_receipts_verify",
                                 "cadence_render_propose"])

    def test_02_scripts_against_real_data(self):
        out = self.srv.tool("cadence_scripts_list")
        ids = [s["script_id"] for s in out["scripts"]]
        self.assertIn("ep18-script", ids)
        entry = next(s for s in out["scripts"] if s["script_id"] == "ep18-script")
        path = os.path.join(HOME, entry["path"])
        digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
        self.assertEqual(entry["sha256"], digest)
        self.assertEqual(entry["segment_count"], 5)

    def test_03_script_read_compiles_via_app(self):
        out = self.srv.tool("cadence_scripts_read", {"script_id": "ep18-script"})
        self.assertEqual(out["rule_set"], "rules-v1")
        self.assertGreater(out["span_count"], 5)
        self.assertEqual(len(out["segments"]), 5)
        self.assertIn("Zcash", out["segments"][0]["text_raw"])

    def test_04_voice_catalogue_matches_app_constant(self):
        sys.path.insert(0, HOME)
        from cadence import helio
        out = self.srv.tool("cadence_voices_list")
        self.assertEqual([v["voice_id"] for v in out["voices"]], [helio.VOICE])

    def test_05_render_records(self):
        out = self.srv.tool("cadence_renders_list")
        ids = [r["record_id"] for r in out["renders"]]
        self.assertIn("ep18-cadence", ids)
        self.assertIn("ep18-shipped", ids)
        rec = self.srv.tool("cadence_renders_read", {"record_id": "ep18-cadence"})
        self.assertAlmostEqual(rec["duration_s"], 67.144, places=3)
        self.assertEqual(rec["state"], "measured")

    def test_06_propose_applies_and_starts_nothing(self):
        before = set(os.listdir(HOME))
        out = self.srv.tool("cadence_render_propose",
                            {"script_id": "ep18-script", "note": "test"})
        self.assertFalse(out["applied"])
        self.assertFalse(out["started"])
        self.assertEqual(out["state"], "proposed")
        pfile = os.path.join(self.state, "proposals", out["proposal_id"] + ".json")
        self.assertTrue(os.path.isfile(pfile))
        self.assertEqual(set(os.listdir(HOME)), before)

    def test_07_refusals(self):
        resp = self.srv.call("tools/call", {"name": "cadence_render_start",
                                            "arguments": {}})
        self.assertTrue(resp["result"]["isError"])
        resp = self.srv.call("x", raw_method="tools/delete")
        self.assertIn("error", resp)
        resp = self.srv.call("x", raw_method="bogus/method")
        self.assertIn("error", resp)
        with self.assertRaises(AssertionError):
            self.srv.tool("cadence_scripts_read", {"script_id": "nope"})

    def test_08_receipt_chain(self):
        out = self.srv.tool("cadence_receipts_verify")
        self.assertTrue(out["ok"])
        self.assertGreaterEqual(out["count"], 6)
        rows = [json.loads(l) for l in
                open(os.path.join(self.state, "receipts.jsonl"), encoding="utf-8")
                if l.strip()]
        for row in rows:
            self.assertNotIn("text_raw", json.dumps(row))
            self.assertIn("args_sha256", row)


if __name__ == "__main__":
    unittest.main()
