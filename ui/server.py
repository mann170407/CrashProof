#!/usr/bin/env python3
"""
server.py - standard-library backend for the AtomicDocs Crash Lab UI.

    python ui/server.py [--host 127.0.0.1] [--port 8765] [--no-static]

Serves the built frontend (ui/web/dist, SPA fallback to index.html) and the
JSON API of ui/CONTRACT.md. Every scenario runs the REAL store code live.

    GET  /api/info                 platform, crash points, constants
    POST /api/scenario/<id>        id = plug | c1 | c2 | c3 | c4 | c5 (JSON body)
    GET  /api/powerloss            SimFS enumeration (cached)
    GET  /api/results              stored results of the last full test run
    GET  /api/tests/stream?only=   Server-Sent Events: run the test suites live

Runs unchanged on Windows and Linux (WSL2). Any exception inside a request
becomes HTTP 500 {"error": ...}; the server itself never goes down.
"""
import argparse
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import scenarios  # noqa: E402
from tracing import STORE_DIR  # noqa: E402

DIST = os.path.join(HERE, "web", "dist")

MIME = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml", ".json": "application/json", ".map": "application/json",
    ".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf", ".png": "image/png",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
    ".ico": "image/x-icon", ".txt": "text/plain; charset=utf-8", ".wasm": "application/wasm",
    ".webmanifest": "application/manifest+json",
}


def suites():
    """The same (name, script, title) list as atomicdocs/run_all.py."""
    sys.path.insert(0, STORE_DIR)
    import run_all
    return run_all.SUITES, run_all.CHALLENGES


def _kill_tree(proc):
    """Kill the child and anything it spawned (crash_test / c2 start their own children)."""
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        else:
            os.killpg(proc.pid, 9)
    except Exception:                                   # noqa: BLE001
        pass
    try:
        proc.kill()
    except Exception:                                   # noqa: BLE001
        pass


class Handler(BaseHTTPRequestHandler):
    server_version = "CrashLab/1.0"
    static = True

    # -- plumbing --------------------------------------------------------
    def log_message(self, fmt, *args):                  # quieter than the default
        sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        raw = self.rfile.read(n)
        if not raw.strip():
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            raise scenarios.BadRequest("request body is not valid JSON")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Max-Age", "86400")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _dispatch(self, method):
        url = urlparse(self.path)
        path = url.path
        try:
            if path.startswith("/api/"):
                self._api(method, path, parse_qs(url.query))
            elif method == "GET":
                self._static(path)
            else:
                self._json(405, {"error": "method not allowed"})
        except scenarios.BadRequest as e:
            self._json(400, {"error": str(e)})
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass                                        # client went away
        except Exception as e:                          # noqa: BLE001 - never kill the server
            traceback.print_exc()
            try:
                self._json(500, {"error": "%s: %s" % (type(e).__name__, e)})
            except Exception:                           # noqa: BLE001
                pass

    # -- API -------------------------------------------------------------
    def _api(self, method, path, q):
        if path == "/api/info":
            return self._json(200, scenarios.info())
        if path == "/api/results":
            return self._json(200, scenarios.results())
        if path == "/api/powerloss":
            return self._json(200, scenarios.powerloss(refresh=q.get("refresh", ["0"])[0] == "1"))
        if path == "/api/tests/stream":
            return self._stream(q)
        if path.startswith("/api/scenario/"):
            sid = path[len("/api/scenario/"):].strip("/")
            if sid not in ("plug", "c1", "c2", "c3", "c4", "c5"):
                return self._json(404, {"error": "unknown scenario %r" % sid})
            body = self._body() if method == "POST" else {
                k: v[0] for k, v in q.items()}           # GET ?crashAt=... for quick manual tests
            if method == "GET":
                if body.get("crashAt") in ("", "null", "none"):
                    body["crashAt"] = None
                if "clients" in body:
                    body["clients"] = int(body["clients"])
            return self._json(200, scenarios.run_scenario(sid, body))
        return self._json(404, {"error": "no such endpoint %s" % path})

    # -- SSE test runner ---------------------------------------------------
    def _sse(self, event, data):
        self.wfile.write(("event: %s\ndata: %s\n\n" % (event, json.dumps(data))).encode("utf-8"))
        self.wfile.flush()

    def _stream(self, q):
        suites_, challenges = suites()
        only = [x for x in ",".join(q.get("only", [])).split(",") if x.strip()]
        if only:
            todo = [c for c in challenges + suites_ if c[0] in only]
            if not todo:
                raise scenarios.BadRequest("only= matches none of %s" % [c[0] for c in suites_ + challenges])
        else:
            todo = suites_ + challenges
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        env.pop("ATOMICDOCS_CRASH_AT", None)
        work = scenarios.platform_info.work_dir()
        if os.path.abspath(work) != os.path.abspath(tempfile.gettempdir()):
            env["TMPDIR"] = work                        # e.g. WSL2: /tmp is tmpfs, keep test stores on ext4
        if q.get("quick", ["0"])[0] == "1":
            env["TRIALS"] = "5"

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self._cors()
        self.end_headers()
        self.close_connection = True

        passed = 0
        for name, script, title in todo:
            self._sse("start", {"name": name, "title": title})
            t0 = time.time()
            kw = {"start_new_session": True} if os.name != "nt" else {}
            proc = subprocess.Popen([sys.executable, "-u"] + script, cwd=STORE_DIR, env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kw)
            lines = queue.Queue()

            def pump(p=proc, out=lines):                # reader thread: never blocks the heartbeat
                for raw in iter(p.stdout.readline, b""):
                    out.put(raw.decode("utf-8", "replace").rstrip("\r\n"))
                out.put(None)

            threading.Thread(target=pump, daemon=True).start()
            last = ""
            try:
                while True:
                    try:
                        line = lines.get(timeout=1.0)
                    except queue.Empty:                 # heartbeat: detects a closed browser tab
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        continue
                    if line is None:
                        break
                    if line.strip():
                        last = line.strip()[:90]
                    self._sse("line", {"name": name, "text": line})
                rc = proc.wait()
            except BaseException:                       # client disconnected (or anything else)
                _kill_tree(proc)
                raise
            ok = rc == 0
            passed += ok
            self._sse("end", {"name": name, "pass": ok, "secs": round(time.time() - t0, 1), "last": last})
        self._sse("done", {"passed": passed, "total": len(todo)})

    # -- static files --------------------------------------------------------
    def _static(self, path):
        if not self.static or not os.path.isdir(DIST):
            body = (b"<!doctype html><title>Crash Lab API</title><p>Frontend not built (ui/web/dist missing). "
                    b"The API is at <a href='/api/info'>/api/info</a>.</p>")
            self.send_response(200 if path == "/" else 404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self._cors()
            self.end_headers()
            self.wfile.write(body)
            return
        rel = os.path.normpath(unquote(path).lstrip("/\\")) if path.strip("/") else "index.html"
        root = os.path.abspath(DIST)
        full = os.path.abspath(os.path.join(root, rel))
        try:
            inside = os.path.commonpath([root, full]) == root
        except ValueError:                              # e.g. another drive on Windows
            inside = False
        if not inside:                                  # path traversal
            return self._json(403, {"error": "forbidden"})
        if not os.path.isfile(full):
            full = os.path.join(DIST, "index.html")      # SPA fallback
            if not os.path.isfile(full):
                return self._json(404, {"error": "not found"})
        ext = os.path.splitext(full)[1].lower()
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache" if ext == ".html" else "public, max-age=3600")
        self._cors()
        self.end_headers()
        self.wfile.write(data)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"               # Windows: SO_REUSEADDR allows port hijack


def make_server(host="127.0.0.1", port=8765, static=True):
    Handler.static = static
    return Server((host, port), Handler)


def main(argv=None):
    ap = argparse.ArgumentParser(description="AtomicDocs Crash Lab backend")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-static", action="store_true", help="API only, do not serve ui/web/dist")
    a = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):           # never die on a console that cannot print "·"
        try:
            stream.reconfigure(errors="replace")
        except Exception:                               # noqa: BLE001
            pass
    srv = make_server(a.host, a.port, not a.no_static)
    p = scenarios.platform_info.info()
    shown = "127.0.0.1" if a.host in ("0.0.0.0", "") else a.host
    print("AtomicDocs Crash Lab backend [%s] on http://%s:%d  (API: /api/info)" % (p["label"], shown, a.port),
          flush=True)
    print("stores in %s; static %s" % (scenarios.platform_info.work_dir(),
                                       DIST if (not a.no_static and os.path.isdir(DIST)) else "(none)"), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
