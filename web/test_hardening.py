#!/usr/bin/env python3
"""Hardening tests for the Cadence free web service (2026-11-14).

Run from web/:  python3 -m unittest test_hardening -v

Covers the hardening lane's cures:
  * bounded processing — a hung ffmpeg/ffprobe ends as a named
    StageTimeout refusal within its bound (child process, stubbed
    slow tools), never as a wedged job;
  * proxy-only header trust — X-Forwarded-For from a non-proxy peer
    is ignored for the daily cap; from the trusted proxy it keys the
    cap exactly as before;
  * response headers — X-Content-Type-Options: nosniff on JSON,
    downloads, receipts and OPTIONS;
  * startup orphan sweep — job dirs left by a previous process are
    removed at startup, counters.json kept;
  * hostile-file battery — truncated / polyglot / non-media /
    absurd-duration / metadata-bomb inputs end as clean refusals or
    bounded successes; the service stays healthy throughout.
"""
import http.client
import http.server
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
BASE = "/cadence-voice"


def _run(cmd):
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def _read(path):
    with open(path, "rb") as f:
        return f.read()


def _make_fixtures(d):
    fx = {}
    tone = os.path.join(d, "tone.wav")
    _run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
          "sine=frequency=440:duration=2", "-ac", "1", "-ar", "44100",
          tone])
    fx["tone_wav"] = _read(tone)
    clip = os.path.join(d, "clip.mp4")
    _run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
          "testsrc=duration=2:size=320x240:rate=10", "-f", "lavfi",
          "-i", "sine=frequency=440:duration=2", "-c:v", "libx264",
          "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", clip])
    fx["clip_mp4"] = _read(clip)
    long_wav = os.path.join(d, "long.wav")
    _run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
          "anullsrc=r=8000:cl=mono", "-t", "700", long_wav])
    fx["long_wav"] = _read(long_wav)
    meta = os.path.join(d, "bomb.ffmeta")
    with open(meta, "w") as f:
        f.write(";FFMETADATA1\ncomment=" + "A" * 200_000 + "\n")
    bomb = os.path.join(d, "bomb.mp4")
    _run(["ffmpeg", "-y", "-v", "error", "-i", clip, "-i", meta,
          "-map_metadata", "1", "-c", "copy", bomb])
    fx["bomb_mp4"] = _read(bomb)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("inner.txt", "not media")
    fx["zip_bytes"] = buf.getvalue()
    # Unlisted-codec carriers: accepted container (MOV), codec outside
    # the allowlist — the CVE-2026-8461 (MagicYUV) and CVE-2026-70632
    # (CineForm) shapes. Each carries a normal AAC track, so without
    # pinning they would sail into processing.
    for key, codec in (("magicyuv_mov", "magicyuv"),
                       ("cfhd_mov", "cfhd")):
        out = os.path.join(d, key + ".mov")
        try:
            _run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                  "testsrc=duration=1:size=320x240:rate=10",
                  "-f", "lavfi", "-i",
                  "sine=frequency=440:duration=1",
                  "-c:v", codec, "-c:a", "aac", "-shortest", out])
            fx[key] = _read(out)
        except subprocess.CalledProcessError:
            fx[key] = None
    return fx


def _load_server(name, work_root, trusted):
    env = {"CADENCE_WEB_WORK": work_root,
           "CADENCE_WEB_TRUSTED_PROXIES": trusted}
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        spec = importlib.util.spec_from_file_location(
            name, os.path.join(HERE, "server.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class _Running:
    def __init__(self, mod):
        self.httpd = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), mod.Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


def _req(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    conn.request(method, path, body=body, headers=headers or {})
    r = conn.getresponse()
    data = r.read()
    hdrs = {k.lower(): v for k, v in r.getheaders()}
    conn.close()
    return r.status, hdrs, data


def _multipart(filename, data):
    # The page sends one file field named "audio" — the parser keys
    # on exactly that name.
    boundary = "----cadencetest7"
    head = (f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="audio"; '
            f'filename="{filename}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n").encode()
    body = head + data + f"\r\n--{boundary}--\r\n".encode()
    return body, {"Content-Type":
                  f"multipart/form-data; boundary={boundary}"}


def _post_job(port, filename, data, xff=None):
    body, headers = _multipart(filename, data)
    if xff:
        headers["X-Forwarded-For"] = xff
    return _req(port, "POST", BASE + "/api/jobs", body, headers)


def _poll_job(port, job_id, deadline_s=120):
    t0 = time.time()
    while time.time() - t0 < deadline_s:
        status, _, data = _req(port, "GET", f"{BASE}/api/jobs/{job_id}")
        assert status == 200, data
        job = json.loads(data)
        if job["status"] in ("done", "error"):
            return job, time.time() - t0
        time.sleep(0.25)
    raise AssertionError(f"job {job_id} did not finish in {deadline_s}s")


class TimeoutTests(unittest.TestCase):
    def test_hung_tools_raise_named_refusal_within_bound(self):
        shim = tempfile.mkdtemp(prefix="cadence-shim-")
        for tool in ("ffmpeg", "ffprobe"):
            p = os.path.join(shim, tool)
            with open(p, "w") as f:
                f.write("#!/bin/sh\nsleep 120\n")
            os.chmod(p, 0o755)
        snippet = r"""
import os, sys, time
sys.path.insert(0, os.environ["CADENCE_MOD_DIR"])
import humanize
t0 = time.time()
try:
    humanize._run(["ffmpeg", "-y", "-i", "x", "y"])
    print("NO-RAISE")
except humanize.StageTimeout as e:
    print("STAGE %.1f refusal=%s" % (time.time() - t0,
          isinstance(e, humanize.Refusal)))
t0 = time.time()
try:
    humanize.probe_duration("x.mp3")
    print("NO-RAISE")
except humanize.StageTimeout:
    print("PROBE %.1f" % (time.time() - t0))
"""
        env = dict(os.environ)
        env["PATH"] = shim + ":" + env["PATH"]
        env["CADENCE_MOD_DIR"] = HERE
        env["CADENCE_STAGE_TIMEOUT_S"] = "3"
        env["CADENCE_PROBE_TIMEOUT_S"] = "2"
        p = subprocess.run([sys.executable, "-c", snippet], env=env,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        stage = [l for l in p.stdout.splitlines()
                 if l.startswith("STAGE")]
        probe = [l for l in p.stdout.splitlines()
                 if l.startswith("PROBE")]
        self.assertEqual(len(stage), 1, p.stdout)
        self.assertEqual(len(probe), 1, p.stdout)
        self.assertIn("refusal=True", stage[0])
        self.assertLess(float(stage[0].split()[1]), 8)
        self.assertLess(float(probe[0].split()[1]), 6)


class ServerHardeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="cadence-hardening-")
        cls.fx = _make_fixtures(cls.tmp)
        cls.root_a = os.path.join(cls.tmp, "work-a")
        cls.mod_a = _load_server("server_untrusted", cls.root_a,
                                 "146.190.150.118")
        cls.root_b = os.path.join(cls.tmp, "work-b")
        cls.mod_b = _load_server("server_trusted", cls.root_b,
                                 "127.0.0.1")

    def test_nosniff_on_json_download_receipt_and_options(self):
        with _Running(self.mod_a) as srv:
            status, hdrs, _ = _req(srv.port, "GET", BASE + "/api/health")
            self.assertEqual(status, 200)
            self.assertEqual(hdrs.get("x-content-type-options"),
                             "nosniff")
            status, hdrs, _ = _req(srv.port, "OPTIONS",
                                   BASE + "/api/jobs")
            self.assertEqual(hdrs.get("x-content-type-options"),
                             "nosniff")
            status, _, data = _post_job(srv.port, "tone.wav",
                                        self.fx["tone_wav"])
            self.assertEqual(status, 202, data)
            job_id = json.loads(data)["job"]
            job, _ = _poll_job(srv.port, job_id)
            self.assertEqual(job["status"], "done", job)
            for kind in ("download", "receipt"):
                status, hdrs, data = _req(
                    srv.port, "GET", f"{BASE}/api/jobs/{job_id}/{kind}")
                self.assertEqual(status, 200, (kind, data))
                self.assertEqual(hdrs.get("x-content-type-options"),
                                 "nosniff", kind)

    def test_xff_spoof_from_non_proxy_peer_is_ignored(self):
        mod = _load_server("server_spoof",
                           os.path.join(self.tmp, "work-spoof"),
                           "146.190.150.118")
        with _Running(mod) as srv:
            codes = []
            for i in range(6):
                status, _, _ = _post_job(
                    srv.port, "tone.wav", self.fx["tone_wav"],
                    xff=f"198.51.100.{10 + i}")
                codes.append(status)
            # All six count against the real peer (127.0.0.1): the
            # rotating spoofed XFF buys nothing — the 6th hits the cap.
            self.assertEqual(codes, [202] * 5 + [429], codes)

    def test_xff_honoured_from_trusted_proxy(self):
        mod = _load_server("server_trusted2",
                           os.path.join(self.tmp, "work-trusted2"),
                           "127.0.0.1")
        with _Running(mod) as srv:
            codes = []
            for _ in range(6):
                status, _, _ = _post_job(
                    srv.port, "tone.wav", self.fx["tone_wav"],
                    xff="203.0.113.7")
                codes.append(status)
            self.assertEqual(codes, [202] * 5 + [429], codes)
            status, _, _ = _post_job(srv.port, "tone.wav",
                                     self.fx["tone_wav"],
                                     xff="203.0.113.8")
            self.assertEqual(status, 202)

    def test_startup_sweep_removes_orphans_keeps_counters(self):
        root = self.root_a
        os.makedirs(root, exist_ok=True)
        orphan = os.path.join(root, "deadbeef" * 4)
        os.makedirs(orphan)
        with open(os.path.join(orphan, "out.mp3"), "wb") as f:
            f.write(b"x")
        counters = os.path.join(root, "counters.json")
        if not os.path.exists(counters):
            with open(counters, "w") as f:
                f.write("{}")
        self.mod_a._sweep_orphans()
        self.assertFalse(os.path.exists(orphan))
        self.assertTrue(os.path.exists(counters))

    def test_hostile_file_battery(self):
        cases = [
            ("truncated mp4", "clip.mp4", self.fx["clip_mp4"][:3000],
             "post-400"),
            ("zero-byte mp4", "empty.mp4", b"", "post-400"),
            ("text named .mp3", "song.mp3",
             b"this is not audio at all" * 8, "post-400"),
            ("zip polyglot named .mp3", "poly.mp3",
             self.fx["zip_bytes"], "post-400"),
            ("absurd duration (700 s wav)", "long.wav",
             self.fx["long_wav"], "post-400"),
            ("metadata bomb mp4 (200 KB comment)", "bomb.mp4",
             self.fx["bomb_mp4"], "bounded"),
            ("MagicYUV in .mov (unlisted codec)", "magic.mov",
             self.fx["magicyuv_mov"], "post-400-pin"),
            ("CineForm in .mov (unlisted codec)", "cine.mov",
             self.fx["cfhd_mov"], "post-400-pin"),
            ("valid tiny mp4 (control)", "clip.mp4",
             self.fx["clip_mp4"], "bounded"),
            ("valid tiny wav (control)", "tone.wav",
             self.fx["tone_wav"], "bounded"),
        ]
        root = os.path.join(self.tmp, "work-battery")
        mod = _load_server("server_battery", root, "146.190.150.118")
        with _Running(mod) as srv:
            for name, fname, data, expect in cases:
                with self.subTest(case=name):
                    if data is None:
                        self.skipTest(f"fixture unavailable: {name}")
                    before = {n for n in os.listdir(root)
                              if os.path.isdir(os.path.join(root, n))}
                    t0 = time.time()
                    status, _, body = _post_job(srv.port, fname, data)
                    if expect in ("post-400", "post-400-pin"):
                        self.assertEqual(status, 400, (name, body))
                        if expect == "post-400-pin":
                            self.assertIn(
                                "isn't one we take",
                                body.decode("utf-8", "replace"),
                                "refusal is not the pinning refusal")
                        self.assertLess(time.time() - t0, 30)
                        # No JOB DIR may be left. (counters.json may
                        # legitimately appear — it is the service's
                        # error/job counter file, not job data.)
                        after = {n for n in os.listdir(root)
                                 if os.path.isdir(os.path.join(root, n))}
                        self.assertEqual(before, after,
                                         "refusal left a job dir")
                    else:
                        self.assertEqual(status, 202, (name, body))
                        job_id = json.loads(body)["job"]
                        job, elapsed = _poll_job(srv.port, job_id)
                        self.assertIn(job["status"], ("done", "error"),
                                      job)
                        if job["status"] == "error":
                            self.assertTrue(job["error"])
                        self.assertLess(elapsed, 120)
                    status, _, _ = _req(srv.port, "GET",
                                        BASE + "/api/health")
                    self.assertEqual(status, 200,
                                     f"service unhealthy after {name}")


def _strip_volatile(obj):
    # created_at / elapsed_s differ between ANY two runs of the same
    # code (wall clock, timing) — they are not behavior.
    if isinstance(obj, dict):
        return {k: _strip_volatile(v) for k, v in obj.items()
                if k not in ("elapsed_s", "created_at")}
    if isinstance(obj, list):
        return [_strip_volatile(v) for v in obj]
    return obj


class ControlIdentityTests(unittest.TestCase):
    """Pinning must not change behavior for accepted files: the same
    control inputs run through the pre-hardening (deployed baseline)
    chain and the hardened chain produce identical receipts and
    identical output bytes. Skips when the lane baseline is absent
    (e.g. when this suite runs from the deployed tree)."""

    def test_accepted_files_convert_identically_to_baseline(self):
        import hashlib
        baseline_dir = os.path.normpath(
            os.path.join(HERE, "..", "baseline-deployed"))
        if not os.path.isdir(baseline_dir):
            self.skipTest("deployed baseline not present")
        sys.path.insert(0, baseline_dir)
        try:
            spec = importlib.util.spec_from_file_location(
                "humanize_baseline",
                os.path.join(baseline_dir, "humanize.py"))
            base = importlib.util.module_from_spec(spec)
            sys.modules["humanize_baseline"] = base
            spec.loader.exec_module(base)
        finally:
            sys.path.remove(baseline_dir)
        import humanize as hardened
        tmp = tempfile.mkdtemp(prefix="cadence-identity-")
        fx = _make_fixtures(tmp)

        def sha(p):
            with open(p, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()

        for kind, fname, data in (
                ("audio", "tone.wav", fx["tone_wav"]),
                ("video", "clip.mp4", fx["clip_mp4"])):
            with self.subTest(control=kind):
                src = os.path.join(tmp, f"in-{kind}" +
                                   os.path.splitext(fname)[1])
                with open(src, "wb") as f:
                    f.write(data)
                outs, receipts = [], []
                for tag, mod in (("base", base), ("hard", hardened)):
                    out = os.path.join(
                        tmp, f"out-{kind}-{tag}" +
                        (".mp4" if kind == "video" else ".mp3"))
                    work = os.path.join(tmp, f"work-{kind}-{tag}")
                    if kind == "video":
                        r = mod.humanize_video(src, out, work)
                    else:
                        r = mod.humanize(src, out, work)
                    outs.append(sha(out))
                    receipts.append(_strip_volatile(r))
                self.assertEqual(receipts[0], receipts[1],
                                 f"{kind} receipt changed by pinning")
                self.assertEqual(outs[0], outs[1],
                                 f"{kind} output bytes changed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
