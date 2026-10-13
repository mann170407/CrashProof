#!/usr/bin/env python3
"""
KO-04-C2  Lost-update rejection (compare-and-swap + writer serialization)

Organisers: "Use two clients reading version v1. Commit one update, then try
the other with expect=v1."   Setup: v1=A; client1 writes B expect=v1;
client2 writes C expect=v1.

Part A  the exact scenario with two separate client PROCESSES driven through
        the real CLI (python atomicdocs.py <root> put <doc> - --expect V),
        then client2's correct re-read / merge / retry loop.
Part B  writer serialization under a real race: 12 processes released by a
        barrier all try put(expect=1) at the same instant (3 rounds); then the
        contrast WITHOUT expect (blind read-modify-write -> lost updates), and
        the same counter WITH CAS + retry loop (no update lost).

Run:  python challenges/c2_lost_update.py      (or from inside challenges/)
"""
import ast
import json
import os
import random
import subprocess
import sys
import time

import common
from common import ad, check, describe_bytes, finish, new_root, note, show_state, step

ID, TITLE = "KO-04-C2", "Lost-update rejection"
N_CLIENTS = 12
CAS_ROUNDS = 3
SELF = os.path.abspath(__file__)

A = b"A|owner=alice;status=draft"
B = b"B|owner=alice;status=review"                   # client1: draft -> review
C = b"C|owner=alice;status=draft;tags=urgent"        # client2: adds a tag (based on v1)


# ----------------------------------------------------------------- helpers
def cli(root, *args, data=None):
    """Run the real CLI as a separate OS process."""
    p = subprocess.run([sys.executable, common.TOOL, root] + list(args),
                       input=data, capture_output=True)
    return p.returncode, p.stdout, p.stderr.decode("utf-8", "replace").strip()


def cli_read(root, doc):
    """A client's read: committed version number first, then the body.
    (Version first: if a commit sneaks in between, the CAS below fails safely.)"""
    _, out, _ = cli(root, "history", doc)
    rows = [ast.literal_eval(l) for l in out.decode().splitlines() if l.strip()]
    v = max((r["version"] for r in rows if r["current"]), default=0)
    _, body, _ = cli(root, "get", doc)
    return v, body


def journal_records(root):
    raw = open(root + "/journal.wal", "rb").read()
    _, recs, _, status = ad.scan_journal(raw)
    return raw, recs, status


def files_containing(root, needle):
    hits = []
    for d, _, names in os.walk(root):
        for n in names:
            p = os.path.join(d, n)
            try:
                with open(p, "rb") as f:
                    if needle in f.read():
                        hits.append(os.path.relpath(p, root))
            except OSError:
                pass
    return hits


def disk_versions(root, doc):
    ddir = root + "/docs/" + doc
    names = sorted(os.listdir(ddir))
    return ([int(n[1:7]) for n in names if ad._is_vname(n)],
            [n for n in names if n.endswith(".tmp")])


def all_valid(root, doc):
    s = ad.RealFS()
    ddir = root + "/docs/" + doc
    vs, _ = disk_versions(root, doc)
    for v in vs:
        try:
            ad.decode_version(doc, v, s.read(ddir + "/" + ad._vname(v)))
        except ad.CorruptVersion:
            return False
    return True


def current_version(root, doc):
    return ad.decode_pointer(open(root + "/docs/" + doc + "/CURRENT", "rb").read())[0]


def payload(client, rnd):
    return (b"client-%02d round-%d |" % (client, rnd)) + (b"payload-from-client-%02d;" % client) * 40


# ----------------------------------------------------------------- worker process
def read_versioned(s, doc):
    """(version, body) that belong together: pointer, body, pointer again."""
    ddir = s._ddir(doc)
    while True:
        v1, _ = s._read_pointer(ddir)
        body = s.get(doc)
        v2, _ = s._read_pointer(ddir)
        if v1 == v2:
            return v1, body


def worker(mode, root, doc, client, ready, go, rnd):
    """One client process. Opens the store (recovery runs now), reads, signals
    ready, spins on the barrier file, then writes. Exit codes mirror the CLI:
    0 = committed, 3 = CONFLICT."""
    client, rnd = int(client), int(rnd)
    s = ad.AtomicDocs(root)
    v, body = read_versioned(s, doc)
    open(ready, "w").close()
    while not os.path.exists(go):                       # barrier
        time.sleep(0.0002)
    out = {"client": client, "read_version": v, "t_start": time.time_ns()}
    rc = 0
    if mode == "cas-once":
        try:
            out["version"] = s.put(doc, payload(client, rnd), expect=v)
            out["outcome"] = "COMMITTED"
        except ad.Conflict as e:
            out.update(outcome="CONFLICT", expected=e.expected, actual=e.actual)
            rc = 3
    elif mode == "blind-incr":                          # read-modify-write, no CAS
        n = int(body.split(b"=")[1])
        out["version"] = s.put(doc, b"count=%d" % (n + 1))
        out["outcome"] = "COMMITTED"
    elif mode == "cas-incr":                            # the correct retry loop
        attempts = conflicts = 0
        while True:
            attempts += 1
            n = int(body.split(b"=")[1])
            try:
                out["version"] = s.put(doc, b"count=%d" % (n + 1), expect=v)
                break
            except ad.Conflict:
                conflicts += 1
                if attempts >= 500:
                    raise
                time.sleep(random.uniform(0, 0.003))    # small jittered backoff
                v, body = read_versioned(s, doc)        # re-read, re-apply the change
        out.update(outcome="COMMITTED", attempts=attempts, conflicts=conflicts)
    out["t_end"] = time.time_ns()
    print(json.dumps(out))
    return rc


def race(root, doc, mode, n, rnd=0):
    """Start n client processes, wait until every one has opened the store and
    read the document, then release them all at once through a go-file."""
    tmp = os.path.dirname(root)
    go = os.path.join(tmp, "go-%s" % doc)
    procs, readies = [], []
    for i in range(n):
        ready = os.path.join(tmp, "ready-%s-%d" % (doc, i))
        readies.append(ready)
        procs.append(subprocess.Popen(
            [sys.executable, SELF, "--worker", mode, root, doc, str(i), ready, go, str(rnd)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE))
    deadline = time.time() + 60
    while not all(os.path.exists(r) for r in readies):
        if time.time() > deadline or any(p.poll() not in (None, 0, 3) for p in procs):
            for p in procs:
                p.kill()
            raise RuntimeError("worker failed to start: %r" %
                               [p.communicate()[1][-300:] for p in procs])
        time.sleep(0.005)
    open(go, "w").close()                               # release all at once
    results = []
    for p in procs:
        out, err = p.communicate(timeout=60)
        if p.returncode not in (0, 3):
            raise RuntimeError("worker crashed: %s" % err.decode()[-500:])
        r = json.loads(out.decode().strip().splitlines()[-1])
        r["rc"] = p.returncode
        results.append(r)
    t0 = min(r["t_start"] for r in results)
    for r in results:
        r["start_ms"] = (r["t_start"] - t0) / 1e6
        r["end_ms"] = (r["t_end"] - t0) / 1e6
    return sorted(results, key=lambda r: r["client"])


# ----------------------------------------------------------------- part A
def part_a():
    root, doc = new_root("ko04-c2a-"), "plan"
    step("PART A - the organisers' scenario, two client processes via the real CLI")
    note("store: %s   document: %r" % (root, doc))

    rc, out, _ = cli(root, "put", doc, "-", "--expect", "0", data=A)
    note("setup  $ atomicdocs.py put plan - --expect 0   <- A   => v%s (rc=%d)" % (out.decode().strip(), rc))
    show_state(root, doc, "on disk after v1:")

    step("A1  both clients read the document (each is its own process)")
    v1c1, b1 = cli_read(root, doc)
    v1c2, b2 = cli_read(root, doc)
    note("client1 read: version %d  body %s" % (v1c1, describe_bytes(b1, 40)))
    note("client2 read: version %d  body %s" % (v1c2, describe_bytes(b2, 40)))
    check("both clients saw v1 = A", v1c1 == v1c2 == 1 and b1 == b2 == A)

    step("A2  client1 commits B with --expect 1")
    rc1, out1, err1 = cli(root, "put", doc, "-", "--expect", "1", data=B)
    note("$ atomicdocs.py put plan - --expect 1   <- B   => rc=%d stdout=%r" % (rc1, out1.decode().strip()))
    check("client1 CAS succeeds and creates v2", rc1 == 0 and out1.strip() == b"2")
    show_state(root, doc, "on disk after client1:")
    _, recs, status = journal_records(root)
    v2sha = ad.decode_version(doc, 2, open(root + "/docs/plan/" + ad._vname(2), "rb").read())[1]
    begins = [r for r in recs if r["t"] == "BEGIN"]
    commits = [r for r in recs if r["t"] == "COMMIT"]
    note("journal now: %s" % [(r["t"], r.get("doc"), r.get("old"), r.get("new"), r["tx"]) for r in recs])
    check("journal: exactly one BEGIN/COMMIT pair, for the B save (v1->v2, sha matches v2 file)",
          len(recs) == 2 and len(begins) == 1 and len(commits) == 1
          and begins[0]["tx"] == commits[0]["tx"] and begins[0]["old"] == 1
          and begins[0]["new"] == 2 and begins[0]["sha"] == v2sha and status == "ok",
          "tx %s" % (begins[0]["tx"] if begins else "-"))

    step("A3  client2 tries its stale update C with --expect 1")
    rc2, out2, err2 = cli(root, "put", doc, "-", "--expect", "1", data=C)
    note("$ atomicdocs.py put plan - --expect 1   <- C   => rc=%d" % rc2)
    note("stderr: %s" % err2)
    check("client2 rejected: exit code 3 + CONFLICT (expected 1, current 2)",
          rc2 == 3 and err2.startswith("CONFLICT") and "expected version 1, current is 2" in err2)
    show_state(root, doc, "on disk after client2's rejected attempt:")
    rcg, body, _ = cli(root, "get", doc)
    check("get() == B (client1's update survives, nothing was overwritten)", rcg == 0 and body == B,
          describe_bytes(body, 40))
    hits = files_containing(root, C)
    check("no file anywhere in the store contains C", not hits, "hits=%r" % hits)
    vs, tmps = disk_versions(root, doc)
    check("history shows exactly v1, v2 (both verify), CURRENT=v2, no temp files",
          vs == [1, 2] and all_valid(root, doc) and current_version(root, doc) == 2 and not tmps,
          "versions=%r tmp=%r" % (vs, tmps))
    raw, recs, status = journal_records(root)
    note("journal after client2: %d bytes, records=%r" % (len(raw), recs))
    note("(client2's process ran recovery on open, which checkpointed the journal to a")
    note(" fresh 16-byte header; any BEGIN for C would have been appended after it)")
    check("journal: no BEGIN (or any record) written for C - CAS failed before the journal",
          len(raw) == ad.JHDR.size and recs == [] and not any(r.get("t") == "BEGIN" for r in recs))

    step("A4  client2 does it right: re-read, merge its change onto v2, retry with the new version")
    attempt = 0
    while True:
        attempt += 1
        v, cur = cli_read(root, doc)
        fields = dict(kv.split("=") for kv in cur.split(b"|", 1)[1].decode().split(";"))
        fields["tags"] = "urgent"                           # client2's intended change
        merged = ("C'|" + ";".join("%s=%s" % kv for kv in fields.items())).encode()
        rc, out, err = cli(root, "put", doc, "-", "--expect", str(v), data=merged)
        note("attempt %d: read v%d %s -> merged %s -> put --expect %d => rc=%d %s"
             % (attempt, v, cur.decode(), merged.decode(), v, rc, out.decode().strip() or err))
        if rc == 0:
            break
        if rc != 3 or attempt >= 5:
            break
    rcg, body, _ = cli(root, "get", doc)
    check("retry commits v3 = B's status + C's tag (both updates kept)",
          rc == 0 and out.strip() == b"3" and body == b"C'|owner=alice;status=review;tags=urgent",
          describe_bytes(body, 48))
    vs, _ = disk_versions(root, doc)
    check("history now v1, v2, v3, all verify", vs == [1, 2, 3] and all_valid(root, doc))
    show_state(root, doc, "final state of part A:")


# ----------------------------------------------------------------- part B
def part_b():
    root = new_root("ko04-c2b-")
    s = ad.AtomicDocs(root)
    step("PART B - writer serialization: %d client processes race put(expect=1) at the same instant"
         % N_CLIENTS)
    note("each worker process: open store -> read (v1) -> wait on a go-file barrier -> put(expect=1)")

    for rnd in range(1, CAS_ROUNDS + 1):
        doc = "race-%d" % rnd
        s.put(doc, b"base document for round %d" % rnd)
        res = race(root, doc, "cas-once", N_CLIENTS, rnd)
        wins = [r for r in res if r["outcome"] == "COMMITTED"]
        losers = [r for r in res if r["outcome"] == "CONFLICT"]
        spread = max(r["start_ms"] for r in res)
        step("B%d  round %d: %d racers released within %.1f ms of each other" % (rnd, rnd, N_CLIENTS, spread))
        if rnd == 1:
            note("client  read  start(ms)  end(ms)  outcome")
            for r in res:
                note("  %02d     v%d   %7.2f  %7.2f   %s" % (
                    r["client"], r["read_version"], r["start_ms"], r["end_ms"],
                    "COMMITTED v%d" % r["version"] if r["outcome"] == "COMMITTED"
                    else "CONFLICT (expected %d, current %d) rc=%d" % (r["expected"], r["actual"], r["rc"])))
        winner = wins[0]["client"] if len(wins) == 1 else None
        note("winner: client %s; %d CONFLICTs" % ("%02d" % winner if winner is not None else "??", len(losers)))
        body = s.get(doc)
        vs, tmps = disk_versions(root, doc)
        _, recs, status = journal_records(root)
        check("round %d: exactly ONE writer commits, %d get CONFLICT (exit 3, saw current=2)"
              % (rnd, N_CLIENTS - 1),
              len(wins) == 1 and wins[0]["version"] == 2 and len(losers) == N_CLIENTS - 1
              and all(r["rc"] == 3 and r["expected"] == 1 and r["actual"] == 2 for r in losers))
        check("round %d: document == the winner's bytes exactly, CURRENT=v2" % rnd,
              winner is not None and body == payload(winner, rnd) and current_version(root, doc) == 2,
              describe_bytes(body, 30))
        check("round %d: only v1+v2 on disk, both verify, no partial/temp files" % rnd,
              vs == [1, 2] and all_valid(root, doc) and not tmps, "versions=%r tmp=%r" % (vs, tmps))
        check("round %d: journal holds one BEGIN/COMMIT pair (the winner), none from the %d losers"
              % (rnd, N_CLIENTS - 1),
              [r["t"] for r in recs] == ["BEGIN", "COMMIT"] and recs[0]["tx"] == recs[1]["tx"]
              and recs[0]["new"] == 2 and status == "ok")
    show_state(root, "race-%d" % CAS_ROUNDS, "on disk after round %d:" % CAS_ROUNDS)

    step("B%d  CONTRAST: the same race WITHOUT expect (blind read-modify-write of a counter)"
         % (CAS_ROUNDS + 1))
    doc = "counter-blind"
    s.put(doc, b"count=0")
    res = race(root, doc, "blind-incr", N_CLIENTS)
    vers = sorted(r["version"] for r in res)
    body = s.get(doc)
    _, recs, _ = journal_records(root)
    note("all %d clients read count=0, each wrote count=1; versions committed: %s" % (N_CLIENTS, vers))
    note("final document: %s   (should be count=%d)" % (body.decode(), N_CLIENTS))
    serial = (len(recs) == 2 * N_CLIENTS and all(
        recs[2 * i]["t"] == "BEGIN" and recs[2 * i + 1]["t"] == "COMMIT"
        and recs[2 * i]["tx"] == recs[2 * i + 1]["tx"]
        and recs[2 * i]["old"] == i + 1 and recs[2 * i]["new"] == i + 2 for i in range(N_CLIENTS)))
    check("no CAS: every write still serialized - versions 2..%d, journal BEGIN/COMMIT never interleave"
          % (N_CLIENTS + 1), vers == list(range(2, N_CLIENTS + 2)) and serial and all_valid(root, doc))
    check("no CAS: %d of %d updates silently LOST (count=1, not %d) - this is why CAS is needed"
          % (N_CLIENTS - 1, N_CLIENTS, N_CLIENTS),
          all(r["outcome"] == "COMMITTED" for r in res) and body == b"count=1")

    step("B%d  FIX: same counter, every client uses CAS + re-read/retry loop" % (CAS_ROUNDS + 2))
    doc = "counter-cas"
    s.put(doc, b"count=0")
    res = race(root, doc, "cas-incr", N_CLIENTS)
    body = s.get(doc)
    conflicts = sum(r["conflicts"] for r in res)
    note("conflicts absorbed by retries: %d; attempts per client: %s"
         % (conflicts, [r["attempts"] for r in res]))
    note("final document: %s, CURRENT=v%d" % (body.decode(), current_version(root, doc)))
    check("CAS + retry: all %d increments applied exactly once (count=%d, v%d)"
          % (N_CLIENTS, N_CLIENTS, N_CLIENTS + 1),
          body == b"count=%d" % N_CLIENTS and current_version(root, doc) == N_CLIENTS + 1
          and sorted(r["version"] for r in res) == list(range(2, N_CLIENTS + 2)))


def explain():
    step("HOW IT WORKS (OS mechanism)")
    for line in [
        "put() takes the store lock SHARED, then the per-document lock EXCLUSIVE",
        "  (flock(LOCK_EX) on Linux/macOS, LockFileEx(LOCKFILE_EXCLUSIVE_LOCK) on Windows).",
        "Under that lock it reads CURRENT and compares with expect; on mismatch it raises",
        "  Conflict BEFORE any journal record, temp file or version file is written.",
        "So check-then-commit is one critical section: no writer can commit between",
        "  another writer's check and its commit -> the race has exactly one winner.",
        "The atomic rename of CURRENT (rename(2) / MoveFileExW REPLACE_EXISTING|WRITE_THROUGH)",
        "  is the linearization point: a version 'exists' for readers from that instant.",
        "The lock alone serializes writes (no torn/duplicate versions, B%d) but cannot know"
        % (CAS_ROUNDS + 1),
        "  what a client READ; expect=v carries that, turning lost updates into CONFLICTs",
        "  that the client resolves by re-reading and re-applying its change (A4, B%d)." % (CAS_ROUNDS + 2),
        "Assumptions: clients read version THEN body (CAS stays safe even if a commit",
        "  sneaks in between); locks are advisory, so all writers must go through AtomicDocs;",
        "  local file system (byte-range/flock locks are not reliable over NFS/SMB).",
    ]:
        note(line)


def main():
    t0 = time.time()
    print("%s %s  (python %s, %s, N=%d client processes)" % (
        ID, TITLE, sys.version.split()[0], sys.platform, N_CLIENTS))
    part_a()
    part_b()
    explain()
    note("elapsed %.1f s" % (time.time() - t0))
    finish(ID, TITLE)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        sys.exit(worker(*sys.argv[2:9]))
    main()
