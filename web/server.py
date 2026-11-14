#!/usr/bin/env python3
"""Cyan Eclipse Cadence — free web humanizer service v1.

Public face: https://cetech.ai/free-tools/cadence-voice/ (page) calling
https://hosted.cetech.ai/cadence-voice/api/* (this service, behind the
H1 Caddy reverse proxy; ufw admits only H1 to the service port).

Process-and-discard: job files live in a private work dir, are deleted
on download + TTL, and are swept at TTL regardless. Counters only are
kept; file names and content are never logged.

Caps (SPEC §4, lane defaults): <=10 min, <=25 MB, <=5 jobs/IP/day,
<=2 concurrent, 15-minute output TTL. Design: SPEC.md in
cadence-web-surface-20261114/.
"""
import json
import os
import threading
import time
import uuid
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from humanize import Refusal, humanize, humanize_video, probe_duration

BASE = "/cadence-voice"
WORK_ROOT = os.environ.get("CADENCE_WEB_WORK", "/var/tmp/cadence-web")
STATE_FILE = os.path.join(WORK_ROOT, "counters.json")
MAX_BYTES = 25 * 1024 * 1024            # audio cap (SPEC section 4)
MAX_SECONDS = 600
VIDEO_MAX_BYTES = 100 * 1024 * 1024    # video caps (SPEC section 10)
VIDEO_MAX_SECONDS = 180
UPLOAD_CEILING = VIDEO_MAX_BYTES + 65536
DAILY_PER_IP = 5
TTL_SECONDS = 15 * 60
AUDIO_EXT = {".mp3", ".wav", ".m4a"}
VIDEO_EXT = {".mp4", ".mov", ".webm"}
ALLOWED_EXT = AUDIO_EXT | VIDEO_EXT
DOWNLOAD_TYPES = {".mp3": "audio/mpeg", ".mp4": "video/mp4",
                  ".mov": "video/quicktime", ".webm": "video/webm"}

os.makedirs(WORK_ROOT, exist_ok=True)
_jobs = {}
_jobs_lock = threading.Lock()
_sema = threading.Semaphore(2)
_counter_lock = threading.Lock()


def _counters():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _count(ip, kind):
    today = date.today().isoformat()
    with _counter_lock:
        c = _counters()
        day = c.setdefault(today, {})
        rec = day.setdefault(ip, {"jobs": 0, "refusals": 0})
        rec[kind] = rec.get(kind, 0) + 1
        c = {today: day}  # keep only today
        with open(STATE_FILE, "w") as f:
            json.dump(c, f)


def _jobs_today(ip):
    today = date.today().isoformat()
    return _counters().get(today, {}).get(ip, {}).get("jobs", 0)


def _sweeper():
    while True:
        time.sleep(60)
        now = time.time()
        with _jobs_lock:
            dead = [j for j, r in _jobs.items()
                    if now - r["created"] > TTL_SECONDS]
            for j in dead:
                _rm_job(_jobs.pop(j, None))


def _rm_job(rec):
    if not rec:
        return
    import shutil
    shutil.rmtree(rec["dir"], ignore_errors=True)


def _parse_multipart(body, content_type):
    """Minimal single-file multipart parser (stdlib only)."""
    boundary = None
    for part in content_type.split(";"):
        part = part.strip()
        if part.startswith("boundary="):
            boundary = part[len("boundary="):].strip('"')
    if not boundary:
        return None, None
    delim = b"--" + boundary.encode()
    for chunk in body.split(delim):
        if b'name="audio"' in chunk[:400]:
            head, sep, data = chunk.partition(b"\r\n\r\n")
            if not sep:
                continue
            if data.endswith(b"\r\n"):
                data = data[:-2]
            fname = "upload"
            disposition = b""
            for line in head.split(b"\r\n"):
                if line.lower().startswith(b"content-disposition:"):
                    disposition = line
                    break
            for tok in disposition.split(b";"):
                tok = tok.strip()
                if tok.startswith(b"filename="):
                    fname = tok[len(b"filename="):].strip(b'"').decode(
                        "utf-8", "replace")
            return fname, data
    return None, None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # counters only; no paths logged
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "https://cetech.ai")
        self.end_headers()
        self.wfile.write(body)

    def _err(self, code, msg, ip=None):
        if ip:
            _count(ip, "refusals")
        self._json(code, {"ok": False, "error": msg})

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "https://cetech.ai")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == BASE + "/api/health":
            self._json(200, {"ok": True, "service": "cadence-web",
                             "version": "1.0.0",
                             "jobs_today": sum(
                                 v.get("jobs", 0) for v in
                                 _counters().get(date.today().isoformat(),
                                                 {}).values())})
            return
        if path == BASE + "/" or path == BASE:
            self._json(200, {"service": "Cyan Eclipse Cadence \u2014 Voice Humanizer",
                             "page": "https://cetech.ai/free-tools/cadence-voice/",
                             "api": BASE + "/api/jobs"})
            return
        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[:2] == ["cadence-voice", "api"] and parts[2] == "jobs":
            job_id = parts[3]
            with _jobs_lock:
                rec = _jobs.get(job_id)
            if not rec:
                self._err(404, "That job is gone \u2014 outputs are kept for "
                               "15 minutes, then discarded.")
                return
            self._json(200, {"ok": True, "job": job_id,
                             "status": rec["status"],
                             "kind": rec.get("kind", "audio"),
                             "receipt": rec.get("receipt"),
                             "error": rec.get("error")})
            return
        if len(parts) == 5 and parts[:2] == ["cadence-voice", "api"] and parts[2] == "jobs":
            job_id, kind = parts[3], parts[4]
            with _jobs_lock:
                rec = _jobs.get(job_id)
            if not rec or rec["status"] != "done":
                self._err(404, "Not ready, or the job is gone.")
                return
            if kind == "download":
                ext = rec.get("ext", ".mp3")
                fpath = rec["out"]
                ctype = DOWNLOAD_TYPES.get(ext, "application/octet-stream")
                fname = "cadence-humanized" + ext
            elif kind == "receipt":
                fpath, ctype, fname = (rec["receipt_path"], "application/json",
                                       "cadence-receipt.json")
            else:
                self._err(404, "Unknown file.")
                return
            with open(fpath, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition",
                             f'attachment; filename="{fname}"')
            self.send_header("Access-Control-Allow-Origin", "https://cetech.ai")
            self.end_headers()
            self.wfile.write(data)
            return
        self._err(404, "Not found.")

    def do_POST(self):
        ip = self.client_address[0]
        # Behind the H1 proxy the client is H1; prefer X-Forwarded-For.
        xff = self.headers.get("X-Forwarded-For")
        if xff:
            ip = xff.split(",")[0].strip()
        path = self.path.split("?")[0]
        if path != BASE + "/api/jobs":
            self._err(404, "Not found.", ip)
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > UPLOAD_CEILING:
            self._err(413, "That file is over the free-tier size limit "
                           "(25 MB audio, 100 MB video).", ip)
            return
        if _jobs_today(ip) >= DAILY_PER_IP:
            self._err(429, "That's the free daily limit (5 conversions). "
                           "Cadence itself is free forever \u2014 see "
                           "cetech.ai/free-tools.", ip)
            return
        body = self.rfile.read(length)
        fname, data = _parse_multipart(
            body, self.headers.get("Content-Type", ""))
        if data is None:
            self._err(400, "No file arrived. Drop an mp3, wav or m4a "
                           "audio file, or an mp4, mov or webm video.", ip)
            return
        ext = os.path.splitext(fname or "")[1].lower()
        if ext not in ALLOWED_EXT:
            self._err(400, "That format is not one we take \u2014 mp3, wav "
                           "or m4a audio; mp4, mov or webm video.", ip)
            return
        is_video = ext in VIDEO_EXT
        size_cap = VIDEO_MAX_BYTES if is_video else MAX_BYTES
        dur_cap = VIDEO_MAX_SECONDS if is_video else MAX_SECONDS
        if len(data) > size_cap:
            self._err(413, "That file is over the free-tier size limit "
                           "(25 MB audio, 100 MB video).", ip)
            return
        job_id = uuid.uuid4().hex
        jdir = os.path.join(WORK_ROOT, job_id)
        os.makedirs(jdir)
        in_path = os.path.join(jdir, "input" + ext)
        with open(in_path, "wb") as f:
            f.write(data)
        try:
            dur = probe_duration(in_path)
        except Exception:
            _rm_job({"dir": jdir})
            self._err(400, "We could not read that file \u2014 mp3, wav or "
                           "m4a audio; mp4, mov or webm video.", ip)
            return
        if dur > dur_cap:
            _rm_job({"dir": jdir})
            self._err(400, "That file runs over the free-tier length "
                           "limit (10 minutes audio, 3 minutes video).",
                      ip)
            return
        _count(ip, "jobs")
        out_name = "humanized" + ext if is_video else "humanized.mp3"
        rec = {"status": "queued", "dir": jdir, "created": time.time(),
               "receipt": None, "error": None, "kind": "video" if is_video
               else "audio", "ext": ext if is_video else ".mp3",
               "out": os.path.join(jdir, out_name),
               "receipt_path": os.path.join(jdir, "receipt.json")}
        with _jobs_lock:
            _jobs[job_id] = rec
        threading.Thread(target=_process, args=(job_id, in_path, rec),
                         daemon=True).start()
        self._json(202, {"ok": True, "job": job_id, "status": "queued"})


def _process(job_id, in_path, rec):
    with _sema:
        rec["status"] = "processing"
        try:
            work = os.path.join(rec["dir"], "work")
            if rec.get("kind") == "video":
                receipt = humanize_video(in_path, rec["out"], work)
            else:
                receipt = humanize(in_path, rec["out"], work)
            with open(rec["receipt_path"], "w") as f:
                json.dump(receipt, f, indent=1)
            rec["receipt"] = receipt
            rec["status"] = "done"
        except Refusal as e:  # named refusal, safe to show the visitor
            rec["status"] = "error"
            rec["error"] = str(e)
        except Exception as e:  # failure is a refusal, never a crash page
            rec["status"] = "error"
            rec["error"] = ("Processing failed on that file. It has been "
                            "discarded; nothing was kept.")
            print(f"job {job_id[:8]} failed: {type(e).__name__}")


def main():
    threading.Thread(target=_sweeper, daemon=True).start()
    host = os.environ.get("CADENCE_WEB_HOST", "0.0.0.0")
    port = int(os.environ.get("CADENCE_WEB_PORT", "8791"))
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
