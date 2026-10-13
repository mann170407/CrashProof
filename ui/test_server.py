#!/usr/bin/env python3
"""
test_server.py - end-to-end check of the Crash Lab backend against ui/CONTRACT.md.

    python ui/test_server.py                                 # starts server.py in-process on a free port
    python ui/test_server.py --base-url http://127.0.0.1:8766   # tests an already running backend (e.g. Linux/WSL2)
    python ui/test_server.py --skip-sse                       # without the live test-runner stream

Hits every endpoint, validates shapes/types, event ordering, the commit flag
(exactly once per successful atomic save), `native` on every syscall, the
platform fields, every scenario pass==true, and the SSE stream for only=c4.
Exits 0 only if everything passed.
"""
import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

FAILS = []
TIMES = []
BASE = None


def ok(cond, what):
    if not cond:
        FAILS.append(what)
        print("   FAIL", what)
    return cond


def req(method, path, body=None, timeout=180):
    data = None if body is None else json.dumps(body).encode()
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"} if data else {})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw, code, hdrs = resp.read(), resp.status, resp.headers
    except urllib.error.HTTPError as e:
        raw, code, hdrs = e.read(), e.code, e.headers
    ms = (time.perf_counter() - t0) * 1000
    return code, raw, hdrs, ms


def get_json(method, path, body=None, name=None):
    code, raw, hdrs, ms = req(method, path, body)
    TIMES.append((name or path, ms))
    ok(code == 200, "%s %s -> HTTP %d %s" % (method, path, code, raw[:200]))
    ok(hdrs.get("Access-Control-Allow-Origin") == "*", "%s: CORS header" % path)
    ok((hdrs.get("Content-Type") or "").startswith("application/json"), "%s: JSON content type" % path)
    return json.loads(raw)


# ----------------------------------------------------------------- shape checks
def is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def check_snapshot(d, where):
    ok(isinstance(d, dict), where + ": disk is an object")
    for k in ("doc", "files", "current", "journal", "journalStatus", "readable", "readableFill"):
        ok(k in d, "%s: disk.%s present" % (where, k))
    ok(isinstance(d["doc"], str), where + ": disk.doc str")
    names = [f["name"] for f in d["files"]]
    ok(names == sorted(names), where + ": files sorted by name")
    for f in d["files"]:
        for k, t in (("name", str), ("kind", str), ("preview", str), ("dataDurable", bool), ("entryDurable", bool)):
            ok(isinstance(f.get(k), t), "%s: file.%s is %s (%r)" % (where, k, t.__name__, f.get(k)))
        ok(is_num(f.get("size")), where + ": file.size number")
        ok(f["kind"] in ("version", "current", "tmp", "lock", "journal", "corrupt", "other"), where + ": file.kind")
        ok(f["kind"] != "lock" and not f["name"].endswith(".lock"), where + ": .lock files omitted")
        ok(f["version"] is None or isinstance(f["version"], int), where + ": file.version")
        ok(f["valid"] in (None, True, False), where + ": file.valid")
        ok(f["fill"] is None or (isinstance(f["fill"], str) and len(f["fill"]) == 1), where + ": file.fill")
        ok(len(f["preview"]) <= 24, where + ": preview <= 24 chars")
        if f["kind"] == "version":
            ok(isinstance(f["version"], int) and f["valid"] in (True, False), where + ": version file fields")
    c = d["current"]
    ok(c is None or (isinstance(c.get("version"), int) and isinstance(c.get("ok"), bool)), where + ": current")
    ok(isinstance(d["journal"], list), where + ": journal list")
    for r in d["journal"]:
        ok(r.get("t") in ("BEGIN", "COMMIT", "ABORT") and isinstance(r.get("tx"), str), where + ": JournalRec")
        ok(set(r) <= {"t", "tx", "old", "new", "why"}, where + ": JournalRec keys")
    ok(d["journalStatus"] in ("ok", "torn_tail", "corrupt", "no_header", "missing"), where + ": journalStatus")
    ok(d["readable"] is None or isinstance(d["readable"], str), where + ": readable")
    ok(d["readableFill"] is None or isinstance(d["readableFill"], str), where + ": readableFill")


def check_result(r, sid, commits=None, lanes=None):
    where = "%s%s" % (sid, json.dumps(r.get("params", {})))
    for k, t in (("id", str), ("title", str), ("params", dict), ("events", list), ("checks", list),
                 ("pass", bool), ("summary", str)):
        ok(isinstance(r.get(k), t), "%s: %s is %s" % (where, k, t.__name__))
    ok(r["id"] == sid, where + ": id")
    ok(r["pass"] is True, where + ": pass == true  (failed checks: %s)"
       % [c["name"] for c in r["checks"] if not c["pass"]])
    ok(r["checks"] and all(isinstance(c.get("name"), str) and isinstance(c.get("pass"), bool) for c in r["checks"]),
       where + ": checks shape")
    ok(isinstance(r.get("platform"), dict) and isinstance(r["platform"].get("id"), str)
       and isinstance(r["platform"].get("label"), str), where + ": platform {id,label}")
    ok(is_num(r.get("elapsedMs")), where + ": elapsedMs number")
    ev = r["events"]
    ok([e["seq"] for e in ev] == list(range(len(ev))), where + ": seq = 0,1,2,... in order")
    ts = [e.get("t") for e in ev]
    ok(all(is_num(t) for t in ts) and ts == sorted(ts), where + ": t present and non-decreasing")
    for e in ev:
        w = "%s ev%d" % (where, e["seq"])
        ok(e["kind"] in ("phase", "syscall", "crash", "fault", "result", "note", "check"), w + ": kind")
        ok(isinstance(e.get("label"), str) and e["label"], w + ": label")
        lane = e.get("lane")
        ok(lane in ("writer", "recovery", "reader", "system") or (isinstance(lane, str) and lane.startswith("client")
                                                                  and lane[6:].isdigit()), w + ": lane %r" % lane)
        if lanes is not None:
            ok(lane in lanes, w + ": lane %r expected in %s" % (lane, lanes))
        if "op" in e:
            ok(e["op"] in ("create", "write", "fsync", "fsync_dir", "rename", "unlink", "mkdir", "lock", "unlock",
                           "read"), w + ": op")
        if e["kind"] == "syscall":
            ok("op" in e and isinstance(e.get("native"), str) and e["native"], w + ": syscall has op + native")
        if e["kind"] in ("phase", "syscall", "crash", "fault", "result"):
            ok("disk" in e, w + ": disk present on %s" % e["kind"])
            check_snapshot(e["disk"], w)
        if e["kind"] in ("check",):
            ok(isinstance(e.get("ok"), bool), w + ": check has ok")
        if e.get("commit"):
            ok(e["op"] == "rename" and e["label"].endswith("CURRENT") and e["lane"] != "recovery",
               w + ": commit only on a save's CURRENT rename")
    if commits is not None:
        n = sum(1 for e in ev if e.get("commit"))
        ok(n == commits, "%s: commit flag appears %d time(s), expected %d" % (where, n, commits))
    return r


# ----------------------------------------------------------------- tests
def test_info():
    i = get_json("GET", "/api/info")
    for k in ("platform", "python", "crashPoints", "recoveryCrashPoints", "keepVersions", "readRetries"):
        ok(k in i, "info.%s" % k)
    p = i["platform"]
    for k, t in (("id", str), ("label", str), ("kernel", str), ("fs", str), ("python", str), ("native", dict),
                 ("caveats", list)):
        ok(isinstance(p.get(k), t), "info.platform.%s is %s" % (k, t.__name__))
    ok(p["id"] in ("linux", "windows", "darwin"), "info.platform.id")
    for op in ("create", "write", "fsync", "fsync_dir", "rename", "unlink", "lock", "read"):
        ok(isinstance(p["native"].get(op), str), "info.platform.native.%s" % op)
    ok(i["keepVersions"] == 3 and i["readRetries"] == 5, "info constants")
    ok(len(i["crashPoints"]) == 8 and len(i["recoveryCrashPoints"]) == 4, "info crash point lists")
    print("   platform: %s | kernel %s | fs %s | python %s | caveats %s"
          % (p["label"], p["kernel"], p["fs"], p["python"], p["caveats"]))
    return i


def test_plug(info):
    for cp in info["crashPoints"] + [None]:
        r = check_result(get_json("POST", "/api/scenario/plug", {"crashAt": cp, "mode": "atomic"},
                                  name="plug %s" % cp), "plug",
                         commits=1 if cp in (None, "after_pointer_rename", "after_commit") else 0,
                         lanes={"system", "writer", "recovery"})
        x = r.get("extra", {})
        ok(x.get("outcome") in ("A", "B") and isinstance(x.get("recovery"), dict), "plug %s: extra" % cp)
        ok(any(e["kind"] == "crash" for e in r["events"]) == (cp is not None), "plug %s: crash event" % cp)
        kinds = [e["lane"] for e in r["events"] if e["kind"] == "syscall"]
        ok(kinds and kinds[0] == "writer" and kinds[-1] == "recovery", "plug %s: writer then recovery" % cp)
    for cp in ("torn_data", "after_trunc"):
        r = check_result(get_json("POST", "/api/scenario/plug", {"crashAt": cp, "mode": "naive"},
                                  name="plug naive %s" % cp), "plug", commits=0)
        ok(r["extra"]["outcome"] in ("corrupt", "empty"), "naive %s: outcome corrupt/empty" % cp)
    code, raw, _, _ = req("POST", "/api/scenario/plug", {"crashAt": "nope", "mode": "atomic"})
    ok(code == 400 and "error" in json.loads(raw), "plug bad crashAt -> 400 {error}")


def test_c1():
    r = check_result(get_json("POST", "/api/scenario/c1", {}), "c1", commits=1,
                     lanes={"system", "writer", "recovery"})
    runs = r["extra"]["runs"]
    ok([(x["crashAt"], x["expected"], x["got"], x["pass"]) for x in runs] ==
       [("before_pointer_rename", "A", "A", True), ("after_pointer_rename", "B", "B", True)], "c1 runs")
    ok(sum(1 for e in r["events"] if e["kind"] == "phase" and e["lane"] == "system") >= 2, "c1 phases per run")


def test_c2():
    r = check_result(get_json("POST", "/api/scenario/c2", {"clients": 8}), "c2", commits=2,
                     lanes={"system", "client1", "client2"})
    x = r["extra"]
    ok(len(x["race"]) == 8 and all(set(e) == {"client", "outcome", "version", "startMs", "endMs"} for e in x["race"]),
       "c2 race shape")
    ok(sum(e["outcome"] == "committed" for e in x["race"]) == 1, "c2 exactly one winner")
    ok(isinstance(x["winner"], int), "c2 winner")
    c = x["contrastNoCas"]
    ok(c["clients"] == 8 and c["expectedCount"] == 8 and c["finalCount"] < 8, "c2 contrastNoCas")
    code, _, _, _ = req("POST", "/api/scenario/c2", {"clients": 99})
    ok(code == 400, "c2 clients=99 -> 400")


def test_c3():
    r = check_result(get_json("POST", "/api/scenario/c3", {}), "c3", commits=5,
                     lanes={"system", "reader", "writer"})
    x = r["extra"]
    ok([e["afterSave"] for e in x["retention"]] == [2, 3, 4, 5, 6], "c3 retention")
    ok([a["outcome"] for a in x["readerAttempts"]] == ["gone", "ok"], "c3 readerAttempts")
    ok(x["starved"]["attempts"] == 5 and "ReadContention" in x["starved"]["error"], "c3 starved")


def test_c4():
    r = check_result(get_json("POST", "/api/scenario/c4", {}), "c4", commits=1,
                     lanes={"system", "writer", "recovery"})
    x = r["extra"]
    ok("CommitUnknownFsync" in x["error"] and x["poisoned"] is True and x["afterRecovery"] == "B", "c4 extra")
    pl = x["powerLoss"]
    ok(pl["corrupt"] == 0 and pl["A"] > 0 and pl["B"] > 0 and pl["states"] == pl["A"] + pl["B"], "c4 powerLoss")
    ok(sum(1 for e in r["events"] if e["kind"] == "fault") == 1, "c4 one fault event")


def test_c5(info):
    for cp in info["recoveryCrashPoints"]:
        r = check_result(get_json("POST", "/api/scenario/c5", {"crashPoint": cp}, name="c5 %s" % cp), "c5",
                         commits=0, lanes={"system", "writer", "recovery"})
        x = r["extra"]
        ok(len(x["fingerprints"]) == 2 and x["identical"] is True and len(x["reports"]) == 2, "c5 %s extra" % cp)
        phases = [e["label"] for e in r["events"] if e["kind"] == "phase"]
        ok(phases == ["setup", "recovery #1 (interrupted)", "rerun 1", "rerun 2"], "c5 %s phases %s" % (cp, phases))
        ok(any(e["kind"] == "crash" and e["lane"] == "recovery" for e in r["events"]), "c5 %s recovery crash" % cp)


def test_powerloss():
    p = get_json("GET", "/api/powerloss")
    for mode in ("atomic", "naive"):
        m = p[mode]
        ok(all(isinstance(s["k"], int) and isinstance(s["label"], str) for s in m["syscalls"]), mode + " syscalls")
        ok(all(set(("k", "states", "A", "B", "corrupt")) <= set(s) for s in m["perSyscall"]), mode + " perSyscall")
        ok(set(("states", "A", "B", "corrupt")) <= set(m["totals"]), mode + " totals")
        ok(0 < len(p["sample"][mode]) <= 600 and set(p["sample"][mode]) <= {"A", "B", "X"}, mode + " sample")
    ok(sum(1 for s in p["atomic"]["syscalls"] if s.get("commit")) == 1, "powerloss: one commit syscall")
    ok(p["atomic"]["totals"]["corrupt"] == 0, "powerloss: atomic never corrupt")
    ok(p["naive"]["totals"]["corrupt"] > 0, "powerloss: naive has corrupt states")
    print("   atomic %s | naive %s | crossCheck %s" % (p["atomic"]["totals"], p["naive"]["totals"], p.get("crossCheck")))


def test_results():
    r = get_json("GET", "/api/results")
    for k in ("explorer", "faults", "crashMatrix", "bench", "challenges"):
        ok(k in r, "results.%s" % k)
    ok(isinstance(r["challenges"], dict), "results.challenges dict")


def test_misc():
    code, raw, hdrs, _ = req("OPTIONS", "/api/scenario/plug")
    ok(code == 204 and hdrs.get("Access-Control-Allow-Origin") == "*", "OPTIONS -> 204 + CORS")
    code, raw, _, _ = req("GET", "/api/nope")
    ok(code == 404 and "error" in json.loads(raw), "unknown /api -> 404 {error}")
    code, raw, _, _ = req("POST", "/api/scenario/zz", {})
    ok(code == 404, "unknown scenario -> 404")
    code, raw, hdrs, _ = req("GET", "/some/spa/route")
    ok(code in (200, 404) and (hdrs.get("Content-Type") or "").startswith(("text/html", "application/json")),
       "non-api path served (SPA fallback or not-built page)")


def test_500():
    """In-process only: a scenario that raises -> HTTP 500 {error}, server keeps serving."""
    import scenarios
    orig = scenarios.c1
    scenarios.c1 = lambda: 1 / 0
    try:
        code, raw, _, _ = req("POST", "/api/scenario/c1", {})
        ok(code == 500 and "ZeroDivisionError" in json.loads(raw).get("error", ""), "exception -> 500 {error}")
    finally:
        scenarios.c1 = orig
    code, _, _, _ = req("GET", "/api/info")
    ok(code == 200, "server still alive after a 500")


def test_sse():
    t0 = time.perf_counter()
    r = urllib.request.Request(BASE + "/api/tests/stream?only=c4")
    seen, cur = [], None
    with urllib.request.urlopen(r, timeout=600) as resp:
        ok((resp.headers.get("Content-Type") or "").startswith("text/event-stream"), "SSE content type")
        for raw in resp:
            line = raw.decode("utf-8").rstrip("\r\n")
            if line.startswith("event: "):
                cur = line[7:]
            elif line.startswith("data: "):
                seen.append((cur, json.loads(line[6:])))
    TIMES.append(("tests/stream only=c4", (time.perf_counter() - t0) * 1000))
    kinds = [k for k, _ in seen]
    ok(kinds[:1] == ["start"] and kinds[-2:] == ["end", "done"] and kinds.count("line") > 5,
       "SSE start/line.../end/done (got %d events)" % len(kinds))
    ok(seen[0][1] == {"name": "c4", "title": seen[0][1].get("title")} and seen[0][1]["title"], "SSE start data")
    end = seen[-2][1]
    ok(end["name"] == "c4" and end["pass"] is True and is_num(end["secs"]) and isinstance(end["last"], str),
       "SSE end: c4 passed (%s)" % end)
    ok(seen[-1][1] == {"passed": 1, "total": 1}, "SSE done {passed:1,total:1}")
    print("   SSE: %d events, %d lines, last=%r" % (len(kinds), kinds.count("line"), end["last"]))


def main():
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url")
    ap.add_argument("--skip-sse", action="store_true")
    a = ap.parse_args()
    srv = None
    if a.base_url:
        BASE = a.base_url.rstrip("/")
    else:
        import server
        srv = server.make_server("127.0.0.1", 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        BASE = "http://127.0.0.1:%d" % srv.server_address[1]
    print("testing %s" % BASE)
    t0 = time.perf_counter()
    steps = [("info", None), ("plug", None), ("c1", test_c1), ("c2", test_c2), ("c3", test_c3), ("c4", test_c4),
             ("c5", None), ("powerloss", test_powerloss), ("results", test_results), ("misc", test_misc)]
    info = None
    for name, fn in steps:
        print("-- %s" % name)
        try:
            if name == "info":
                info = test_info()
            elif name == "plug":
                test_plug(info)
            elif name == "c5":
                test_c5(info)
            else:
                fn()
        except Exception as e:                          # noqa: BLE001
            ok(False, "%s raised %s: %s" % (name, type(e).__name__, e))
    if srv:
        print("-- error handling (500)")
        test_500()
    if not a.skip_sse:
        print("-- tests/stream (SSE, only=c4)")
        try:
            test_sse()
        except Exception as e:                          # noqa: BLE001
            ok(False, "SSE raised %s: %s" % (type(e).__name__, e))
    print("\nendpoint timings (ms, round trip):")
    for n, ms in TIMES:
        print("   %-36s %8.0f" % (n, ms))
    print("\n%s: %d failure(s) in %.1f s" % ("PASS" if not FAILS else "FAIL", len(FAILS), time.perf_counter() - t0))
    if srv:
        srv.shutdown()
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
