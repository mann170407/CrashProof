#!/usr/bin/env python3
"""
scenarios.py - the live Crash Lab scenarios behind /api (see ui/CONTRACT.md).

Every scenario builds a fresh store in a temp directory, drives the REAL
AtomicDocs code through a TracingFS (so every event is a system call the
store really made), runs checks, returns a contract ScenarioResult and
deletes the temp directory.

    plug(crashAt, mode)   pull the plug during put(B) over A, then recover
    c1()                  KO-04-C1 commit-point crash matrix
    c2(clients)           KO-04-C2 lost-update rejection (+ real-process race)
    c3()                  KO-04-C3 retention under read pressure
    c4()                  KO-04-C4 EIO from the directory fsync after commit
    c5(crashPoint)        KO-04-C5 interrupted recovery, rerun twice
    powerloss()           ALICE-style SimFS enumeration, atomic vs naive

Also: info(), results(), and the c2 race worker (python scenarios.py --c2-worker ...).
"""
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from tracing import Recorder, TracingFS, _Crash, ad, fingerprint, STORE_DIR
import platform_info
from simfs import SimFS, SimCrash

HERE = os.path.dirname(os.path.abspath(__file__))
SELF = os.path.abspath(__file__)
CACHE_DIR = os.path.join(HERE, ".cache")

DOC = "report"
A, B, C = b"\x41" * 64, b"\x42" * 64, b"\x43" * 64
# The power-loss enumeration uses a 3-sector document so a write can really
# tear across 512 B sectors (a 64 B document fits in one sector and cannot).
PL_A, PL_B = b"\x41" * 1500, b"\x42" * 1500
NAIVE_POINTS = ["torn_data", "after_trunc"]
COMMITTED_AFTER = {"after_pointer_rename", "after_commit"}   # crash points past the commit point


class BadRequest(ValueError):
    """Invalid parameters -> HTTP 400."""


# ----------------------------------------------------------------- helpers
def _new_root(prefix):
    d = tempfile.mkdtemp(prefix=prefix, dir=platform_info.work_dir())
    return d, d + "/store"


def _setup_v1(root, body=A):
    """v1 = body committed durably, then reopen (recovery checkpoints the journal)."""
    ad.AtomicDocs(root).put(DOC, body)
    ad.AtomicDocs(root)


def _name(b):
    if b is None:
        return "none"
    for n, v in (("A", A), ("B", B), ("C", C)):
        if b == v:
            return n
    return "empty" if b == b"" else "corrupt"


def _ddir(root, doc=DOC):
    return root + "/docs/" + doc


def _versions(root, doc=DOC):
    return sorted(int(n[1:7]) for n in os.listdir(_ddir(root, doc)) if ad._is_vname(n))


def _tmps(root, doc=DOC):
    return sorted(n for n in os.listdir(_ddir(root, doc)) if n.endswith(".tmp"))


def _pointer(root, doc=DOC):
    """(version, verifies) of CURRENT on disk; (0, False) if missing/torn."""
    try:
        v, sha = ad.decode_pointer(open(_ddir(root, doc) + "/CURRENT", "rb").read())
        ad.decode_version(doc, v, open(_ddir(root, doc) + "/" + ad._vname(v), "rb").read(), sha)
        return v, True
    except (OSError, ad.CorruptVersion):
        return 0, False


def _open_tx(root):
    _, recs, _, _ = ad.scan_journal(open(root + "/journal.wal", "rb").read())
    done = {r["tx"] for r in recs if r["t"] != "BEGIN"}
    return [r for r in recs if r["t"] == "BEGIN" and r["tx"] not in done]


def _commits(rec, lanes=None):
    return sum(1 for e in rec.events if e.get("commit") and (lanes is None or e["lane"] in lanes))


def _report_notes(rec, rep, lane="recovery"):
    """Turn a recover() report into presenter-readable decision notes."""
    if rep["tmp_removed"]:
        rec.note(lane, "decision: removed %d leftover temp file(s)" % rep["tmp_removed"],
                 "Temp files are never committed (only a rename commits), so deleting them is always safe.")
    for f in rep["fell_back"]:
        rec.note(lane, "decision: CURRENT unusable -> accepted v%s" % f["to"],
                 "Why: %s. Rule: the newest version whose SHA-256 verifies is the truth." % f["why"])
    if rep["rolled_back"]:
        rec.note(lane, "decision: ROLLED BACK the in-flight save",
                 "The journal shows BEGIN without COMMIT and CURRENT never named the new version, so it "
                 "was never committed: its version file is deleted, the old version stays.")
    if rep["rolled_forward"]:
        rec.note(lane, "decision: ROLLED FORWARD the in-flight save",
                 "The journal shows BEGIN without COMMIT, but CURRENT already names the new version and "
                 "it verifies: the commit point was passed, so the save counts as committed.")
    for g in rep["degraded"]:
        rec.note(lane, "decision: DEGRADED %s" % g, "Nothing verifiable was deleted; reported loudly.")
    if rep["journal"] != "ok":
        rec.note(lane, "journal was %s (%d byte(s) dropped)" % (rep["journal"], rep["journal_bytes_dropped"]),
                 "A torn tail is a harmless crash artefact; the checkpoint replaces the journal anyway.")
    if not (rep["tmp_removed"] or rep["fell_back"] or rep["rolled_back"] or rep["rolled_forward"]):
        rec.note(lane, "decision: nothing to do", "Disk already consistent; recovery changed nothing.")


def _recover_and_read(rec, root, label="reopen store -> recovery runs"):
    rec.phase("recovery", label,
              "Opening the store runs crash recovery first: truth = CURRENT if it parses and its target's "
              "SHA-256 verifies, otherwise the newest version that verifies.")
    s = ad.AtomicDocs(root, fs=TracingFS(rec, "recovery", reads=True))
    rep = s.last_recovery
    _report_notes(rec, rep)
    got = s.get(DOC)
    return rep, got


def _finish(rec, sid, title, params, summary, extra, t0):
    out = rec.result_dict(sid, title, params, summary, extra)
    out["platform"] = platform_info.short_info()
    out["elapsedMs"] = round((time.perf_counter() - t0) * 1000, 1)
    return out


def _rm(d):
    shutil.rmtree(d, ignore_errors=True)


# ----------------------------------------------------------------- plug
def plug(crashAt=None, mode="atomic"):
    if mode not in ("atomic", "naive"):
        raise BadRequest("mode must be 'atomic' or 'naive'")
    if mode == "naive":
        return _plug_naive(crashAt)
    if crashAt is not None and crashAt not in ad.CRASH_POINTS:
        raise BadRequest("crashAt must be null or one of %s" % ad.CRASH_POINTS)
    t0 = time.perf_counter()
    d, root = _new_root("crashlab-plug-")
    try:
        _setup_v1(root)
        rec = Recorder(root, DOC)
        rec.phase("system", "setup: v1 = A committed",
                  "Document %r holds A (64 x 0x41) as v1, fully durable. Now we save B over it." % DOC)
        tfs = TracingFS(rec, "writer")
        tfs.torn = crashAt
        store = ad.AtomicDocs(root, fs=tfs, auto_recover=False)
        store.crash_at = crashAt
        rec.phase("writer", "put(%r, B)%s" % (DOC, "  [crash at %s]" % crashAt if crashAt else ""),
                  "The writer saves B. %s" % ("It will be killed at %s." % crashAt if crashAt
                                              else "No crash: the full commit protocol runs."))
        crashed = False
        try:
            v = store.put(DOC, B)
            rec.result("writer", "put() returned v%d - durable" % v, ok=True,
                       detail="put() returns only after the directory fsync that made CURRENT durable.")
        except _Crash:
            crashed = True
            rec.crash("writer", crashAt)
        del store, tfs                                   # the dead process runs no more code
        rep, got = _recover_and_read(rec, root)
        outcome = _name(got)
        expected = "B" if crashAt is None or crashAt in COMMITTED_AFTER else "A"
        cv, cok = _pointer(root)
        rec.result("recovery", "get() = %s (v%d)" % (outcome, cv), ok=outcome == expected,
                   detail="After recovery the document reads as exactly %s - never a mix." % outcome)
        rec.check("document is exactly A or exactly B (never torn, never mixed)", outcome in ("A", "B"),
                  "got %s" % outcome)
        rec.check("outcome follows the commit point: expected %s" % expected, outcome == expected,
                  "crashAt=%s -> %s" % (crashAt, outcome))
        if crashAt:
            rec.check("writer was killed at %s" % crashAt, crashed)
        rec.check("CURRENT verifies and names v%d" % (2 if expected == "B" else 1),
                  cok and cv == (2 if expected == "B" else 1), "CURRENT -> v%d" % cv)
        rec.check("no temp files left after recovery", not _tmps(root), str(_tmps(root)))
        rec.check("journal checkpointed (no open transaction)", not _open_tx(root))
        want = 1 if expected == "B" else 0
        rec.check("commit point (CURRENT rename) recorded %d time(s)" % want, _commits(rec) == want,
                  "%d commit events" % _commits(rec))
        if crashAt is None:
            summary = "No crash: B committed as v2."
        elif expected == "B":
            summary = "Crashed at %s (after the commit point). Recovered to B (v2): rolled forward." % crashAt
        else:
            summary = "Crashed at %s (before the commit point). Recovered to A (v1). Rolled back v2." % crashAt
        return _finish(rec, "plug", "Pull the plug", {"crashAt": crashAt, "mode": mode}, summary,
                       {"outcome": outcome, "expected": expected, "recovery": rep}, t0)
    finally:
        _rm(d)


def _plug_naive(crashAt):
    """The naive in-place writer: open(O_TRUNC) + write over the only copy."""
    t0 = time.perf_counter()
    point, mapped = crashAt, None
    if crashAt is not None and crashAt not in NAIVE_POINTS:
        point = "after_trunc" if crashAt == "after_begin" else "torn_data"
        mapped = crashAt
    d = tempfile.mkdtemp(prefix="crashlab-naive-", dir=platform_info.work_dir())
    try:
        ndir = d + "/naive"
        os.makedirs(ndir)
        path = ndir + "/report.txt"
        with open(path, "wb") as f:
            f.write(A)
            f.flush()
            os.fsync(f.fileno())
        rec = Recorder(ndir, DOC, naive_file=path)
        rec.phase("system", "setup: report.txt = A",
                  "The naive way: one file, A (64 x 0x41), fully durable. Now save B over it in place.")
        if mapped:
            rec.note("system", "naive writer has no %r step: crashing at %s instead" % (mapped, point),
                     "The naive writer only has open(O_TRUNC) and write(); the nearest equivalent moment is used.")
        tfs = TracingFS(rec, "writer")
        tfs.torn = "naive_torn" if point == "torn_data" else "naive"
        rec.phase("writer", "naive save: open(O_TRUNC) + write B%s" % ("  [crash at %s]" % point if point else ""),
                  "What most code does: truncate the file and write the new bytes over the only copy.")
        crashed = False
        try:
            fd = tfs.open_trunc(path)
            try:
                if point == "after_trunc":
                    tfs.crash()
                if point == "torn_data":
                    tfs.write(fd, B[: len(B) // 2])
                    tfs.fsync(fd)                     # make the torn bytes really reach disk
                    tfs.crash()
                tfs.write(fd, B)
            finally:
                tfs.close(fd)
            rec.result("writer", "naive save returned", ok=True)
        except _Crash:
            crashed = True
            rec.crash("writer", point, "The process dies in the middle of overwriting the only copy. "
                                       "There is no old version to go back to.")
        del tfs
        rec.phase("recovery", "reopen: read report.txt (no recovery possible)",
                  "The naive format has no journal, no checksum, no second copy: whatever bytes are in the "
                  "file ARE the document.")
        raw = TracingFS(rec, "recovery", reads=True).read(path)
        outcome = "empty" if raw == b"" else _name(raw) if _name(raw) in ("A", "B") else "corrupt"
        rec.result("recovery", "read() = %s (%d B)" % (outcome, len(raw)), ok=outcome in ("A", "B"),
                   detail={"empty": "The truncate reached the file, the new data never did: the document is GONE.",
                           "corrupt": "Half old size, half new bytes: neither A nor B. The document is CORRUPT.",
                           "B": "No crash: B is in place (but it was never fsynced).",
                           "A": "The old content survived."}[outcome])
        if crashed:
            rec.check("naive in-place save is NOT crash-safe: document is neither A nor B (expected)",
                      outcome in ("corrupt", "empty"), "got %s" % outcome)
            summary = "Naive writer crashed at %s: the document is %s." % (point, outcome.upper())
        else:
            rec.check("without a crash the naive save writes B", outcome == "B", "got %s" % outcome)
            summary = "Naive writer, no crash: B in place (not even fsynced)."
        return _finish(rec, "plug", "Pull the plug (naive in-place writer)",
                       {"crashAt": crashAt, "mode": "naive"}, summary,
                       {"outcome": outcome, "recovery": None, "naivePoint": point}, t0)
    finally:
        _rm(d)


# ----------------------------------------------------------------- c1
def c1():
    t0 = time.perf_counter()
    d, root = _new_root("crashlab-c1-")
    try:
        _setup_v1(root)
        rec = Recorder(root, DOC)
        runs = []
        for i, (point, expected) in enumerate((("before_pointer_rename", "A"), ("after_pointer_rename", "B")), 1):
            rec.phase("system", "run %d: save B, crash %s" % (i, point),
                      "Store holds v1 = A. Save B and kill the writer %s the CURRENT rename (the commit point). "
                      "Expected after recovery: %s." % ("just BEFORE" if expected == "A" else "just AFTER", expected))
            tfs = TracingFS(rec, "writer")
            store = ad.AtomicDocs(root, fs=tfs, auto_recover=False)
            store.crash_at = point
            crashed = False
            try:
                store.put(DOC, B)
            except _Crash:
                crashed = True
                rec.crash("writer", point)
            del store, tfs
            rep, got = _recover_and_read(rec, root)
            g = _name(got)
            g = g if g in ("A", "B") else "corrupt"
            ok = crashed and g == expected
            rec.result("recovery", "run %d: get() = %s (expected %s)" % (i, g, expected), ok=ok)
            rec.check("run %d (%s): recovered to %s" % (i, point, expected), ok, "got %s" % g)
            key = "rolled_back" if expected == "A" else "rolled_forward"
            rec.check("run %d: recovery reports %s" % (i, key), DOC in rep[key])
            rec.check("run %d: no temp files, journal checkpointed" % i, not _tmps(root) and not _open_tx(root))
            runs.append({"crashAt": point, "expected": expected, "got": g, "pass": ok})
        rec.check("exactly one commit point recorded (run 2 only)", _commits(rec) == 1)
        return _finish(rec, "c1", "KO-04-C1 Commit-point crash matrix", {},
                       "Crash before the CURRENT rename -> A; crash after it -> B. The rename is the commit point.",
                       {"runs": runs}, t0)
    finally:
        _rm(d)


# ----------------------------------------------------------------- c2
def _race_payload(i):
    return (b"client-%02d|" % i).ljust(64, b"#")


def _c2_worker(mode, root, doc, client, ready, go):
    """One client PROCESS: open the store, read, wait at the barrier, write.
    Prints one JSON line. Times are wall clock (time.time_ns) so they are
    comparable across processes."""
    client = int(client)
    s = ad.AtomicDocs(root, auto_recover=False)
    seen = []
    s.read_hook = lambda d, v, a: seen.append(v)
    body = s.get(doc)
    s.read_hook = None
    open(ready, "w").close()
    while not os.path.exists(go):                      # barrier: released together
        time.sleep(0.0002)
    out = {"client": client, "read": seen[-1] if seen else 0, "t0": time.time_ns()}
    try:
        if mode == "cas":
            out["version"] = s.put(doc, _race_payload(client), expect=1)
        else:                                          # blind read-increment-write, no CAS
            n = int(body.split(b"=")[1])
            out["version"] = s.put(doc, b"count=%d" % (n + 1))
        out["outcome"] = "committed"
    except ad.Conflict as e:
        out.update(outcome="conflict", version=None, actual=e.actual)
    out["t1"] = time.time_ns()
    print(json.dumps(out), flush=True)
    return 0


def _race(root, doc, mode, n):
    """n real OS processes, released at the same instant by a go-file."""
    tmp = os.path.dirname(root)
    go = os.path.join(tmp, "go-%s-%s" % (doc, mode))
    procs, readies = [], []
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    env.pop("ATOMICDOCS_CRASH_AT", None)
    try:
        for i in range(1, n + 1):
            ready = os.path.join(tmp, "ready-%s-%s-%d" % (doc, mode, i))
            readies.append(ready)
            procs.append(subprocess.Popen(
                [sys.executable, "-u", SELF, "--c2-worker", mode, root, doc, str(i), ready, go],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=HERE))
        deadline = time.time() + 20
        while not all(os.path.exists(r) for r in readies):
            if time.time() > deadline or any(p.poll() not in (None, 0) for p in procs):
                raise RuntimeError("c2 race worker failed to start: %r"
                                   % [p.communicate()[1][-300:] for p in procs if p.poll() not in (None, 0)])
            time.sleep(0.005)
        open(go, "w").close()
        res = []
        for p in procs:
            out, err = p.communicate(timeout=30)
            if p.returncode != 0:
                raise RuntimeError("c2 race worker crashed: %s" % err.decode("utf-8", "replace")[-400:])
            res.append(json.loads(out.decode().strip().splitlines()[-1]))
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()
    base = min(r["t0"] for r in res)
    for r in res:
        r["startMs"] = round((r["t0"] - base) / 1e6, 3)
        r["endMs"] = round((r["t1"] - base) / 1e6, 3)
    return sorted(res, key=lambda r: r["client"])


def c2(clients=8):
    try:
        clients = int(clients)
    except (TypeError, ValueError):
        raise BadRequest("clients must be an integer 2..16")
    if not 2 <= clients <= 16:
        raise BadRequest("clients must be 2..16")
    t0 = time.perf_counter()
    d, root = _new_root("crashlab-c2-")
    try:
        _setup_v1(root)
        rec = Recorder(root, DOC)
        rec.phase("system", "setup: v1 = A",
                  "Two clients will both read v1 and both try to update it. Without compare-and-swap the "
                  "second write would silently destroy the first (a lost update).")
        cl = {n: ad.AtomicDocs(root, fs=TracingFS(rec, n, reads=True, locks=True), auto_recover=False)
              for n in ("client1", "client2")}

        def read(lane):
            seen = []
            cl[lane].read_hook = lambda dd, v, a: seen.append(v)
            body = cl[lane].get(DOC)
            cl[lane].read_hook = None
            v = seen[-1] if seen else 0
            rec.result(lane, "%s read v%d = %s" % (lane, v, _name(body)), ok=True)
            return v, body

        rec.phase("system", "both clients read the document")
        v1a, b1a = read("client1")
        v1b, b1b = read("client2")
        rec.check("both clients saw v1 = A", v1a == v1b == 1 and b1a == b1b == A)

        rec.phase("client1", "client1: put(B, expect=1)",
                  "expect=1 means: commit only if the committed version is still 1. Checked under the "
                  "per-document exclusive lock, so check and commit are one critical section.")
        v_c1 = cl["client1"].put(DOC, B, expect=1)
        rec.result("client1", "client1 committed v%d" % v_c1, ok=True)
        rec.check("client1's CAS succeeds and creates v2", v_c1 == 2)

        rec.phase("client2", "client2: put(C, expect=1) - stale",
                  "client2 still believes v1 is current. Its CAS check reads CURRENT under the lock and sees v2.")
        conflict = None
        try:
            cl["client2"].put(DOC, C, expect=1)
        except ad.Conflict as e:
            conflict = e
        rec.result("client2", "Conflict: %s" % conflict if conflict else "client2 committed (WRONG)", ok=False,
                   detail="Rejected BEFORE any journal record, temp file or version file was written: "
                          "nothing to clean up, B is untouched.")
        rec.check("client2 rejected with Conflict (expected 1, current 2)",
                  conflict is not None and conflict.expected == 1 and conflict.actual == 2)
        rec.check("document still B (client1's update survived)", _pointer(root)[0] == 2
                  and ad.AtomicDocs(root, auto_recover=False).get(DOC) == B)

        rec.phase("client2", "client2: re-read, re-apply, retry with expect=2")
        v2b, _ = read("client2")
        v_c2 = cl["client2"].put(DOC, C, expect=v2b)
        rec.result("client2", "client2 committed v%d" % v_c2, ok=True)
        rec.check("retry with expect=2 commits v3", v2b == 2 and v_c2 == 3)
        del cl

        # -- part 2: real OS processes racing
        rdoc = "race"
        ad.AtomicDocs(root, auto_recover=False).put(rdoc, A)
        rec.doc = rdoc
        rec.phase("system", "race: %d processes put(expect=1) at the same instant" % clients,
                  "Each client is a separate OS process: open store, read v1, wait at a go-file barrier, "
                  "then put(expect=1). The kernel lock serializes them; CAS lets exactly one win.")
        race = _race(root, rdoc, "cas", clients)
        wins = [r for r in race if r["outcome"] == "committed"]
        winner = wins[0]["client"] if len(wins) == 1 else None
        body = ad.AtomicDocs(root, auto_recover=False).get(rdoc)
        rec.result("system", "race: client %s committed v2, %d conflicts" % (winner, clients - len(wins)),
                   ok=len(wins) == 1,
                   detail="Released within %.1f ms of each other." % max(r["startMs"] for r in race))
        rec.check("race: exactly ONE process commits, %d get Conflict" % (clients - 1),
                  len(wins) == 1 and wins[0]["version"] == 2
                  and all(r["outcome"] == "conflict" and r.get("actual") == 2 for r in race if r not in wins))
        rec.check("race: document == the winner's bytes, only v1+v2 on disk, no temps",
                  winner is not None and body == _race_payload(winner) and _versions(root, rdoc) == [1, 2]
                  and not _tmps(root, rdoc))

        cdoc = "counter"
        ad.AtomicDocs(root, auto_recover=False).put(cdoc, b"count=0")
        rec.doc = cdoc
        rec.phase("system", "contrast: same race WITHOUT expect (read-increment-write a counter)",
                  "Every process reads count=0, adds 1 and writes - no compare-and-swap.")
        blind = _race(root, cdoc, "blind", clients)
        final = int(ad.AtomicDocs(root, auto_recover=False).get(cdoc).split(b"=")[1])
        rec.result("system", "no CAS: final count=%d, expected %d -> %d updates LOST" % (final, clients, clients - final),
                   ok=False, detail="Every write was serialized and durable, yet %d of %d increments vanished. "
                                    "Locks alone cannot know what a client READ; expect=v carries that." % (clients - final, clients))
        rec.check("no CAS: every write committed (serialized versions 2..%d)" % (clients + 1),
                  sorted(r["version"] for r in blind) == list(range(2, clients + 2)))
        rec.check("no CAS: updates were lost (final count < %d) - why CAS is needed" % clients, final < clients,
                  "count=%d" % final)
        rec.doc = DOC
        return _finish(rec, "c2", "KO-04-C2 Lost-update rejection", {"clients": clients},
                       "Stale write rejected with Conflict; %d racing processes -> exactly one winner (client %s)."
                       % (clients, winner),
                       {"race": [{"client": r["client"], "outcome": r["outcome"], "version": r["version"],
                                  "startMs": r["startMs"], "endMs": r["endMs"]} for r in race],
                        "winner": winner,
                        "contrastNoCas": {"clients": clients, "finalCount": final, "expectedCount": clients}}, t0)
    finally:
        _rm(d)


# ----------------------------------------------------------------- c3
def _letter(v):
    """Version v's body: 64 x the v-th letter (v1=A, v2=B, ...), so fill shows the version."""
    return bytes([0x40 + v]) * 64


def c3():
    t0 = time.perf_counter()
    keep, retries = ad.KEEP_VERSIONS, ad.READ_RETRIES
    d, root = _new_root("crashlab-c3-")
    try:
        _setup_v1(root, _letter(1))
        rec = Recorder(root, DOC)
        rec.phase("system", "setup: v1 = A, retention KEEP_VERSIONS=%d" % keep,
                  "Version n holds 64 copies of the n-th letter. GC keeps only the newest %d versions." % keep)
        writer = ad.AtomicDocs(root, fs=TracingFS(rec, "writer"), auto_recover=False)
        reader = ad.AtomicDocs(root, fs=TracingFS(rec, "reader", reads=True), auto_recover=False)
        paused, release = threading.Event(), threading.Event()
        attempts, result = [], {}

        def hook(doc, v, attempt):              # between the CURRENT read and the version open
            attempts.append([attempt, v])
            if attempt == 0:
                rec.phase("reader", "reader PAUSED after reading CURRENT -> v%d" % v,
                          "The reader thread is descheduled between reading the pointer and opening %s "
                          "(read_hook blocks on a threading.Event). Readers take no lock." % ad._vname(v))
                paused.set()
                release.wait(30)
            else:
                rec.note("reader", "retry %d: CURRENT now names v%d" % (attempt, v),
                         "A FileNotFoundError proves a newer version committed, so re-reading CURRENT is safe.")

        def run():
            try:
                result["body"] = reader.get(DOC)
            except Exception as e:              # judged by the checks
                result["error"] = e

        reader.read_hook = hook
        th = threading.Thread(target=run, name="reader", daemon=True)
        th.start()
        if not paused.wait(10):
            raise RuntimeError("reader never paused")

        retention = []
        for v in range(2, 7):
            rec.phase("writer", "writer: put v%d = %s" % (v, chr(0x40 + v)))
            got = writer.put(DOC, _letter(v))
            vs = _versions(root)
            retention.append({"afterSave": got, "onDisk": vs})
        rec.check("retention: never more than %d version files after a save" % keep,
                  all(len(r["onDisk"]) <= keep for r in retention), str([r["onDisk"] for r in retention]))
        rec.check("v1 was garbage-collected when v4 committed",
                  next((r["afterSave"] for r in retention if 1 not in r["onDisk"]), None) == 4)
        rec.check("reader stayed paused during all 5 saves", th.is_alive() and attempts == [[0, 1]])

        rec.phase("reader", "reader resumes", "It now opens v000001.dat - which GC deleted.")
        release.set()
        th.join(10)
        got = result.get("body")
        rec.result("reader", "get() returned v6 = %s after %d attempts" % (_name(got) if got != _letter(6) else "F",
                                                                         len(attempts)),
                   ok=got == _letter(6),
                   detail="A whole committed version, verified by SHA-256 - never a mix, never an error.")
        rec.check("reader returned v6 exactly (no error, no mix)", "error" not in result and got == _letter(6))
        rec.check("retry bounded: 2 attempts used (<= READ_RETRIES=%d)" % retries,
                  len(attempts) == 2 and attempts[1] == [1, 6])
        reader_attempts = [{"attempt": a, "sawVersion": v,
                            "outcome": "ok" if i == len(attempts) - 1 and "body" in result else "gone"}
                           for i, (a, v) in enumerate(attempts)]

        # -- the bound: a reader starved on every attempt
        rec.phase("system", "the bound: reader starved on EVERY attempt",
                  "Each time the reader pauses, writers commit %d versions, so the version it just read is "
                  "always GC'd before it can open it." % keep)
        qwriter = ad.AtomicDocs(root, fs=TracingFS(rec, "writer", quiet=True), auto_recover=False)
        nxt = [7]
        log = []

        def hostile(doc, v, attempt):
            log.append((attempt, v))
            for _ in range(keep):
                nv = qwriter.put(DOC, _letter(nxt[0]))
                nxt[0] += 1
                rec.result("writer", "put -> v%d committed (on disk %s)" % (nv, " ".join("v%d" % x for x in _versions(root))),
                           ok=True)

        reader.read_hook = hostile
        err = None
        try:
            reader.get(DOC)
        except ad.ReadContention as e:
            err = e
        reader.read_hook = None
        rec.result("reader", "ReadContention after %d attempts" % len(log) if err else "no ReadContention (WRONG)",
                   ok=False, detail="Instead of looping forever or blocking writers, get() gives up after "
                                    "READ_RETRIES=%d attempts with a clear error. %s" % (retries, err or ""))
        rec.check("ReadContention after exactly READ_RETRIES=%d attempts" % retries,
                  err is not None and len(log) == retries)
        rec.check("each retry saw a newer version (v6, v9, v12, ...)",
                  [v for _, v in log] == [6 + keep * i for i in range(retries)])
        rec.check("retention still bounded while starved", len(_versions(root)) == keep and not _tmps(root))
        latest = nxt[0] - 1
        rec.check("pressure gone: a normal get() returns v%d whole" % latest, reader.get(DOC) == _letter(latest))
        return _finish(rec, "c3", "KO-04-C3 Retention under read pressure", {},
                       "Paused reader of v1 retried once and got v6; a starved reader gets ReadContention after %d attempts."
                       % retries,
                       {"retention": retention, "readerAttempts": reader_attempts,
                        "starved": {"attempts": len(log), "error": "%s: %s" % (type(err).__name__, err) if err else None}},
                       t0)
    finally:
        _rm(d)


# ----------------------------------------------------------------- c4
class _EIOAfterCommitSim(SimFS):
    """The C4 fault inside the power-loss simulator: the failed directory flush
    emits NO barrier (see challenges/c4_post_commit_fsync.py)."""

    def __init__(self, base):
        super().__init__(base)
        self.armed = False

    def rename(self, a, b):
        super().rename(a, b)
        if b.endswith("/CURRENT"):
            self.armed = True

    def fsync_dir(self, p):
        if self.armed:
            self.armed = False
            self._done("fsync dir %s -> EIO (injected, no barrier)" % os.path.basename(p))
            raise ad.FsyncError("injected EIO")
        super().fsync_dir(p)


def _c4_powerloss():
    d, base = _new_root("crashlab-c4sim-")
    try:
        _setup_v1(base)
        sim = _EIOAfterCommitSim(base)
        try:
            ad.AtomicDocs(base, fs=sim, auto_recover=False).put(DOC, B)
        except ad.CommitUnknownFsync:
            pass
        seen = {}
        for _, _, st in sim.crash_states(len(sim.ops)):
            fp = st.fingerprint(sim.root)
            if fp not in seen:
                seen[fp] = _classify_sim(st, sim.root)
        vals = list(seen.values())
        return {"states": len(vals), "A": vals.count("A"), "B": vals.count("B"), "corrupt": vals.count("X")}
    finally:
        _rm(d)


def c4():
    t0 = time.perf_counter()
    d, root = _new_root("crashlab-c4-")
    try:
        _setup_v1(root)
        rec = Recorder(root, DOC)
        rec.phase("system", "setup: v1 = A",
                  "The file system will fail (EIO) the directory fsync that follows the CURRENT rename.")
        tfs = TracingFS(rec, "writer", fault="eio_after_commit")
        store = ad.AtomicDocs(root, fs=tfs, auto_recover=False)
        rec.phase("writer", "put(B) on a file system with a failing directory flush")
        err = None
        try:
            store.put(DOC, B)
        except Exception as e:                  # judged below
            err = e
        rec.result("writer", "put(B) raised %s" % type(err).__name__, ok=False,
                   detail="Outcome UNKNOWN: reporting failure would be a lie if the rename is on disk, reporting "
                          "success would be a lie if a power cut loses it. Nothing past the commit point is deleted.")
        poisoned = False
        try:
            store.put(DOC, C)
        except ad.Poisoned:
            poisoned = True
        rec.result("writer", "next put(C) -> Poisoned" if poisoned else "next put(C) accepted (WRONG)", ok=False,
                   detail="fsyncgate rule: after a failed flush the page cache cannot be trusted and fsync must "
                          "never be retried. The handle refuses all writes until the store is reopened.")
        rec.check("fault injected exactly once, on the directory fsync after the CURRENT rename", tfs.injected == 1)
        rec.check("put raised CommitUnknownFsync (a CommitUnknown and an FsyncError)",
                  isinstance(err, ad.CommitUnknownFsync) and isinstance(err, ad.CommitUnknown)
                  and isinstance(err, ad.FsyncError), type(err).__name__)
        rec.check("error message says the outcome is unknown", "outcome unknown" in str(err))
        rec.check("handle poisoned: further put raises Poisoned", poisoned)
        rec.check("CURRENT already names v2 and v000002.dat was NOT deleted",
                  _pointer(root)[0] == 2 and 2 in _versions(root))
        tx = _open_tx(root)
        rec.check("journal: BEGIN v1->v2 without COMMIT", len(tx) == 1 and tx[0]["new"] == 2)
        del store, tfs
        rep, got = _recover_and_read(rec, root, "reopen with a healthy file system -> recovery")
        rec.result("recovery", "get() = %s" % _name(got), ok=got == B,
                   detail="Recovery reads the truth back from disk: CURRENT names v2 and it verifies -> roll forward.")
        rec.check("recovery rolled forward to B", DOC in rep["rolled_forward"] and got == B)
        rec.check("v1 kept, v2 current, no temp files", _versions(root) == [1, 2] and _pointer(root) == (2, True)
                  and not _tmps(root))
        pl = _c4_powerloss()
        rec.note("system", "power cut at that moment: %d disk states -> A=%d, B=%d, corrupt=%d"
                 % (pl["states"], pl["A"], pl["B"], pl["corrupt"]),
                 "SimFS replays the same put with the same fault and enumerates every disk a power cut could "
                 "leave right after the failed flush. Both A and B occur - that is why the answer must be "
                 "'outcome unknown', and never a mix.")
        rec.check("power loss after the failed flush: only A or B, never corrupt", pl["corrupt"] == 0 and pl["states"] > 0)
        rec.check("both A and B really occur -> the outcome IS unknown", pl["A"] > 0 and pl["B"] > 0)
        return _finish(rec, "c4", "KO-04-C4 Failure after commit, before flush", {},
                       "Directory fsync EIO after the commit point -> CommitUnknownFsync, handle poisoned, "
                       "v2 kept; reopen rolled forward to B.",
                       {"error": "%s: %s" % (type(err).__name__, err), "poisoned": poisoned,
                        "afterRecovery": _name(got), "powerLoss": pl}, t0)
    finally:
        _rm(d)


# ----------------------------------------------------------------- c5
STRAY = ".v000003.dat.4242.0badf00d.tmp"


def _reaches(root, point):
    """Does recovery of this disk reach `point`? Dry run inside SimFS (no disk change)."""
    sim = SimFS(root)
    s = ad.AtomicDocs(root, fs=sim, auto_recover=False)
    s.crash_at = point
    try:
        s.recover()
        return False
    except SimCrash:
        return True


def _empty_report(rep):
    return not (rep["tmp_removed"] or rep["rolled_back"] or rep["rolled_forward"]
                or rep["fell_back"] or rep["degraded"])


def c5(crashPoint=None):
    if crashPoint not in ad.RECOVERY_CRASH_POINTS:
        raise BadRequest("crashPoint must be one of %s" % ad.RECOVERY_CRASH_POINTS)
    t0 = time.perf_counter()
    d, root = _new_root("crashlab-c5-")
    try:
        _setup_v1(root)
        rec = Recorder(root, DOC)
        rec.phase("writer", "setup",
                  "v1 = A committed. Now a put(B) is killed just before its CURRENT rename, leaving an "
                  "uncommitted v2 = B and a temp pointer; plus a stray temp from an older crashed writer.")
        tfs = TracingFS(rec, "writer")
        st = ad.AtomicDocs(root, fs=tfs, auto_recover=False)
        st.crash_at = "before_pointer_rename"
        try:
            st.put(DOC, B)
        except _Crash:
            rec.crash("writer", "before_pointer_rename")
        del st, tfs
        frame, _ = ad.encode_version(DOC, 3, C)
        with open(_ddir(root) + "/" + STRAY, "wb") as f:
            f.write(frame[: len(frame) // 2])
        rec.result("writer", "stray temp %s (half a frame) left by an older crashed writer" % STRAY, ok=True)
        variant = "S1"
        if not _reaches(root, crashPoint):
            variant = "S2"
            for name in ("CURRENT", ad._vname(2)):          # simulated media faults
                p = _ddir(root) + "/" + name
                raw = open(p, "rb").read()
                with open(p, "wb") as f:
                    f.write(raw[: len(raw) // 2])
            rec.note("system", "variant setup: CURRENT torn + v2 torn",
                     "%s is never reached with an intact CURRENT (recovery only rewrites CURRENT when it is bad), "
                     "so this run uses the variant of c5_interrupted_recovery.py scenario S2: CURRENT and v2 are "
                     "torn (simulated media faults). Truth is still v1 = A." % crashPoint)
            rec.result("system", "variant S2: CURRENT and v000002.dat torn in half", ok=True)
            if not _reaches(root, crashPoint):
                raise RuntimeError("crash point %s not reachable even in variant S2" % crashPoint)

        rec.phase("recovery", "recovery #1 (interrupted)",
                  "Recovery starts and is killed right after the state-changing step %s." % crashPoint)
        rs = ad.AtomicDocs(root, fs=TracingFS(rec, "recovery", reads=True), auto_recover=False)
        rs.crash_at = crashPoint
        crashed = False
        try:
            rs.recover()
        except _Crash:
            crashed = True
            rec.crash("recovery", crashPoint, "Recovery itself dies half-way. Whatever it already did stays on "
                                              "disk; the rest is undone work for the next run.")
        del rs
        rec.check("recovery #1 was killed at %s" % crashPoint, crashed)
        fps, reps, gots = [], [], []
        for i in (1, 2):
            rec.phase("recovery", "rerun %d" % i,
                      "Recovery runs again from whatever the interrupted run left. Every step is a deterministic "
                      "function of what is on disk, so finished steps find nothing to redo." if i == 1 else
                      "And once more: an idempotent recovery must change nothing now.")
            s = ad.AtomicDocs(root, fs=TracingFS(rec, "recovery", reads=True), auto_recover=False)
            rep = s.recover()
            _report_notes(rec, rep)
            got = s.get(DOC)
            fps.append(fingerprint(root))
            reps.append(rep)
            gots.append(got)
            rec.result("recovery", "rerun %d: get() = %s, disk fingerprint %s" % (i, _name(got), fps[-1]),
                       ok=got == A)
            del s
        rec.check("rerun 1 -> get() == A exactly", gots[0] == A)
        rec.check("rerun 2 -> get() == A exactly", gots[1] == A)
        rec.check("rerun 2 changed nothing (fingerprints identical)", fps[0] == fps[1], "%s vs %s" % tuple(fps))
        rec.check("rerun 2 report is empty", _empty_report(reps[1]))
        rec.check("only v1 remains, CURRENT -> v1 verifies, no temp files",
                  _versions(root) == [1] and _pointer(root) == (1, True) and not _tmps(root))
        rec.check("journal reset (no open BEGIN)", not _open_tx(root))
        return _finish(rec, "c5", "KO-04-C5 Interrupted recovery", {"crashPoint": crashPoint},
                       "Recovery killed at %s, rerun twice: converged to A (v1), rerun 2 changed nothing%s."
                       % (crashPoint, " (variant S2: torn CURRENT + torn v2)" if variant == "S2" else ""),
                       {"fingerprints": fps, "identical": fps[0] == fps[1], "reports": reps,
                        "variant": variant}, t0)
    finally:
        _rm(d)


# ----------------------------------------------------------------- powerloss
def _sim_from_state(st, root):
    """A SimFS whose live disk IS this crash state (no real files)."""
    s = SimFS.__new__(SimFS)
    s.root, s.model = root.rstrip("/"), "W1"
    s.base, s.live = st, st.copy()
    s.next_ino = max(list(st.data) + list(st.size) + [0]) + 1
    s.ops, s.syscalls, s.acks, s.fds, s.next_fd = [], [], [], {}, 3
    return s


def _classify_sim(st, root, doc=DOC, a=A, b=B):
    """Run the REAL recovery + get() on a crash state, inside the simulator
    (same store code as on a materialised directory, but in memory: fast)."""
    try:
        got = ad.AtomicDocs(root, fs=_sim_from_state(st, root)).get(doc)
    except Exception:                                   # noqa: BLE001 - any failure is "X"
        return "X"
    return "A" if got == a else "B" if got == b else "X"


def _classify_real(sim, st, a=A, b=B):
    """The same, on a real materialised directory with RealFS (cross-check)."""
    d = tempfile.mkdtemp(prefix="crashlab-xc-", dir=platform_info.work_dir())
    try:
        dest = d + "/store"
        sim.materialize(st, dest)
        got = ad.AtomicDocs(dest).get(DOC)
        return "A" if got == a else "B" if got == b else "X"
    except Exception:                                   # noqa: BLE001
        return "X"
    finally:
        _rm(d)


def _short_label(label):
    from tracing import short
    return " ".join(short(t) for t in label.split())


def _sample(seq, n=600):
    if len(seq) <= n:
        return list(seq)
    return [seq[int(i * len(seq) / n)] for i in range(n)]


def _enumerate(sim, classify):
    """Per syscall k: distinct crash states of a cut right after syscall k."""
    syscalls, per, flat, total = [], [], [], {}
    for i, (label, k) in enumerate(sim.syscalls, 1):
        s = {"k": i, "label": _short_label(label)}
        if label.startswith("rename .CURRENT"):
            s["commit"] = True
        syscalls.append(s)
        # Every legal crash state of this cut counts (each is a different set of
        # persisted micro-ops, as in crash_explorer's per-syscall trace); `distinct`
        # = how many different disk images they produce. Totals are distinct images.
        seen, outs = set(), []
        for _, _, st in sim.crash_states(k):
            fp = st.fingerprint(sim.root)
            seen.add(fp)
            if fp not in total:
                total[fp] = (classify(st), st)
            outs.append(total[fp][0])
        per.append({"k": i, "states": len(outs), "A": outs.count("A"), "B": outs.count("B"),
                    "corrupt": outs.count("X"), "distinct": len(seen)})
        flat.extend(outs)
    vals = [v for v, _ in total.values()]
    totals = {"states": len(vals), "A": vals.count("A"), "B": vals.count("B"), "corrupt": vals.count("X")}
    return {"syscalls": syscalls, "perSyscall": per, "totals": totals}, flat, total


def _compute_powerloss():
    t0 = time.perf_counter()
    d = tempfile.mkdtemp(prefix="crashlab-pl-", dir=platform_info.work_dir())
    try:
        base = d + "/store"
        _setup_v1(base, PL_A)
        sim = SimFS(base)
        ad.AtomicDocs(base, fs=sim, auto_recover=False).put(DOC, PL_B)
        atomic, aflat, astates = _enumerate(sim, lambda st: _classify_sim(st, sim.root, a=PL_A, b=PL_B))
        # Cross-check the in-memory classification against real directories + RealFS.
        items = list(astates.values())
        pick = [items[int(i * len(items) / 12)] for i in range(min(12, len(items)))]
        mism = sum(1 for v, st in pick if _classify_real(sim, st, PL_A, PL_B) != v)

        ndir = d + "/naive"
        os.makedirs(ndir)
        with open(ndir + "/report.txt", "wb") as f:
            f.write(PL_A)
            f.flush()
            os.fsync(f.fileno())
        nsim = SimFS(ndir)
        ad.naive_put(nsim, ndir + "/report.txt", PL_B)

        def naive_cls(st):
            got = st.reachable_files(ndir).get("report.txt", b"")
            return "A" if got == PL_A else "B" if got == PL_B else "X"
        naive, nflat, _ = _enumerate(nsim, naive_cls)
        return {"atomic": atomic, "naive": naive, "docBytes": len(PL_B),
                "sample": {"atomic": _sample(aflat), "naive": _sample(nflat)},
                "crossCheck": {"states": len(pick), "mismatches": mism},
                "computeMs": round((time.perf_counter() - t0) * 1000, 1)}
    finally:
        _rm(d)


_PL = None
_PL_LOCK = threading.Lock()


def _source_key():
    h = hashlib.sha256()
    for p in (os.path.join(STORE_DIR, "atomicdocs.py"), os.path.join(STORE_DIR, "simfs.py"), SELF):
        with open(p, "rb") as f:
            h.update(f.read())
    return h.hexdigest()[:16]


def powerloss(refresh=False):
    """Cached in memory and in ui/.cache/powerloss.json (keyed by the source code)."""
    global _PL
    t0 = time.perf_counter()
    with _PL_LOCK:
        key = _source_key()
        if _PL is None or refresh or _PL.get("sourceKey") != key:
            path = os.path.join(CACHE_DIR, "powerloss.json")
            data = None
            if not refresh:
                try:
                    with open(path, encoding="utf-8") as f:
                        data = json.load(f)
                    if data.get("sourceKey") != key or data.get("platformId") != platform_info.platform_id():
                        data = None
                except (OSError, ValueError):
                    data = None
            if data is None:
                data = _compute_powerloss()
                data["sourceKey"] = key
                data["platformId"] = platform_info.platform_id()
                os.makedirs(CACHE_DIR, exist_ok=True)
                tmp = path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(data, f)
                os.replace(tmp, path)
            _PL = data
        out = dict(_PL)
    out["platform"] = platform_info.short_info()
    out["elapsedMs"] = round((time.perf_counter() - t0) * 1000, 1)
    return out


# ----------------------------------------------------------------- info / results
def info():
    p = platform_info.info()
    return {"platform": p, "python": p["python"], "crashPoints": list(ad.CRASH_POINTS),
            "recoveryCrashPoints": list(ad.RECOVERY_CRASH_POINTS), "keepVersions": ad.KEEP_VERSIONS,
            "readRetries": ad.READ_RETRIES, "naiveCrashPoints": NAIVE_POINTS}


def results():
    def load(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None
    ch = {}
    for p in sorted(glob.glob(os.path.join(STORE_DIR, "challenges", "results", "*.json"))):
        ch[os.path.splitext(os.path.basename(p))[0]] = load(p)
    return {"explorer": load(os.path.join(STORE_DIR, "explorer_results.json")),
            "faults": load(os.path.join(STORE_DIR, "fault_results.json")),
            "crashMatrix": load(os.path.join(STORE_DIR, "results.json")),
            "bench": load(os.path.join(STORE_DIR, "bench.json")),
            "challenges": ch, "platform": platform_info.short_info()}


# ----------------------------------------------------------------- dispatch
def run_scenario(sid, body):
    """sid in plug|c1..c5, body = parsed JSON request (dict)."""
    body = body or {}
    if not isinstance(body, dict):
        raise BadRequest("body must be a JSON object")
    if sid == "plug":
        return plug(body.get("crashAt"), body.get("mode") or "atomic")
    if sid == "c1":
        return c1()
    if sid == "c2":
        return c2(body.get("clients", 8) if body.get("clients") is not None else 8)
    if sid == "c3":
        return c3()
    if sid == "c4":
        return c4()
    if sid == "c5":
        return c5(body.get("crashPoint") or "recover_after_tmp_unlink")
    raise KeyError(sid)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--c2-worker":
        sys.exit(_c2_worker(*sys.argv[2:8]))
    # quick manual run: python scenarios.py plug before_pointer_rename
    sid = sys.argv[1] if len(sys.argv) > 1 else "plug"
    args = sys.argv[2:]
    t = time.perf_counter()
    if sid == "powerloss":
        r = powerloss(refresh="--refresh" in args)
        print(json.dumps(r["atomic"]["totals"]), json.dumps(r["naive"]["totals"]), r.get("crossCheck"))
    else:
        body = {"plug": lambda: {"crashAt": args[0] if args else None, "mode": args[1] if len(args) > 1 else "atomic"},
                "c5": lambda: {"crashPoint": args[0] if args else None},
                "c2": lambda: {"clients": int(args[0]) if args else 8}}.get(sid, lambda: {})()
        r = run_scenario(sid, body)
        for e in r["events"]:
            print("%3d %-9s %-8s %s%s" % (e["seq"], e["lane"], e["kind"], e["label"], "  [COMMIT]" if e.get("commit") else ""))
        for c in r["checks"]:
            print("  %s %s %s" % ("PASS" if c["pass"] else "FAIL", c["name"], c.get("detail", "")))
        print(r["summary"], "pass=%s" % r["pass"])
    print("%.0f ms" % ((time.perf_counter() - t) * 1000))
