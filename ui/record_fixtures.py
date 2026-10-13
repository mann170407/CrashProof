#!/usr/bin/env python3
"""
record_fixtures.py - record every API response as JSON fixtures, so the UI can
replay a real run when a backend is unreachable (projector-laptop safety).

    python ui/record_fixtures.py                                   # in-process, this OS
    python ui/record_fixtures.py --base-url http://127.0.0.1:8765  # from a running backend
    python ui/record_fixtures.py --base-url http://127.0.0.1:8766  # the Linux (WSL2) backend

Default --out is ui/web/public/fixtures/<platform id>/ (windows | linux | darwin),
taken from the backend's /api/info. Files:

    info.json  results.json  powerloss.json  c1.json  c2.json  c3.json  c4.json
    plug-<crashPoint>.json (atomic, each crash point)   plug-none.json (atomic, no crash)
    plug-naive-torn_data.json  plug-naive-after_trunc.json
    c5-<recoveryCrashPoint>.json                         index.json (what was recorded)
"""
import argparse
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "web", "public", "fixtures")


class Http:
    def __init__(self, base):
        self.base = base.rstrip("/")

    def call(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        r = urllib.request.Request(self.base + path, data=data, method=method,
                                   headers={"Content-Type": "application/json"} if data else {})
        with urllib.request.urlopen(r, timeout=300) as resp:
            return json.loads(resp.read())


class Local:
    """Same calls, answered in-process by scenarios.py (no server needed)."""

    def __init__(self):
        sys.path.insert(0, HERE)
        import scenarios
        self.s = scenarios

    def call(self, method, path, body=None):
        if path == "/api/info":
            return self.s.info()
        if path == "/api/results":
            return self.s.results()
        if path == "/api/powerloss":
            return self.s.powerloss()
        return self.s.run_scenario(path.rsplit("/", 1)[1], body or {})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", help="record from a running backend (default: in-process)")
    ap.add_argument("--out", help="output dir (default ui/web/public/fixtures/<platform id>)")
    a = ap.parse_args()
    api = Http(a.base_url) if a.base_url else Local()
    info = api.call("GET", "/api/info")
    out = a.out or os.path.join(FIXTURES, info["platform"]["id"])
    os.makedirs(out, exist_ok=True)

    jobs = [("info", "GET", "/api/info", None), ("results", "GET", "/api/results", None),
            ("powerloss", "GET", "/api/powerloss", None)]
    for cp in info["crashPoints"]:
        jobs.append(("plug-%s" % cp, "POST", "/api/scenario/plug", {"crashAt": cp, "mode": "atomic"}))
    jobs.append(("plug-none", "POST", "/api/scenario/plug", {"crashAt": None, "mode": "atomic"}))
    for cp in ("torn_data", "after_trunc"):
        jobs.append(("plug-naive-%s" % cp, "POST", "/api/scenario/plug", {"crashAt": cp, "mode": "naive"}))
    for sid in ("c1", "c2", "c3", "c4"):
        jobs.append((sid, "POST", "/api/scenario/" + sid, {}))
    for cp in info["recoveryCrashPoints"]:
        jobs.append(("c5-%s" % cp, "POST", "/api/scenario/c5", {"crashPoint": cp}))

    index, bad = [], 0
    for name, method, path, body in jobs:
        t0 = time.perf_counter()
        data = info if name == "info" else api.call(method, path, body)
        ms = (time.perf_counter() - t0) * 1000
        with open(os.path.join(out, name + ".json"), "w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"))
        ok = data.get("pass", True)
        bad += ok is False
        index.append({"name": name, "method": method, "path": path, "body": body,
                      "pass": data.get("pass"), "ms": round(ms)})
        print("%-40s %6.0f ms %s" % (name, ms, "" if ok is not False else "  (pass=false!)"))
    with open(os.path.join(out, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"platform": info["platform"], "recordedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                   "source": a.base_url or "in-process", "fixtures": index}, f, indent=1)
    print("wrote %d fixtures to %s" % (len(index) + 1, out))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
