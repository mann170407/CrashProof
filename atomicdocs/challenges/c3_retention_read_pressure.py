#!/usr/bin/env python3
"""
KO-04-C3  Retention under read pressure (bounded GC / read contention)

Organisers: "Keep retention at three versions and a paused reader of v1 while
writers create v2..v6. Demonstrate your bounded read retry/retention behavior."
Setup: one document, 6 sequential version saves; pause the reader between the
CURRENT read and the version open.

Part A  the exact scenario: a reader thread pauses (read_hook blocks on an
        Event) right after reading CURRENT -> v1; a writer saves v2..v6; the
        on-disk version list is printed after each save. Released, the reader
        finds v1 garbage-collected, re-reads CURRENT and returns v6 whole.
        Plus: a reader that had ALREADY opened its version is unaffected by GC.
Part B  the bound: a pathological reader whose version is GC'd before every
        open gets ReadContention after exactly READ_RETRIES attempts.
Part C  stress: reader threads (with injected delays) vs a writer doing 200
        saves; every read is a whole committed version, disk stays bounded.

Run:  python challenges/c3_retention_read_pressure.py   (or from inside challenges/)
"""
import os
import random
import sys
import threading
import time

import common
from common import ad, check, describe_bytes, finish, new_root, note, show_state, step

ID, TITLE = "KO-04-C3", "Retention under read pressure"
KEEP, RETRIES = ad.KEEP_VERSIONS, ad.READ_RETRIES
STRESS_SAVES, STRESS_READERS = 200, 4


def body(v):
    """Self-describing content: the tag says which version it is, and the whole
    body is derivable from the tag, so any mix of two versions is detectable."""
    return (b"VERSION %06d|" % v) + (b"<%06d>" % v) * 150


def tag_of(b):
    return int(b[8:14])


def on_disk(root, doc):
    names = os.listdir(root + "/docs/" + doc)
    return (sorted(int(n[1:7]) for n in names if ad._is_vname(n)),
            sorted(n for n in names if n.endswith(".tmp")))


def vlist(vs):
    return " ".join("v%d" % v for v in vs) or "-"


# ----------------------------------------------------------------- part A
def part_a():
    root, doc = new_root("ko04-c3a-"), "report"
    step("PART A - paused reader of v1 while a writer creates v2..v6 (KEEP_VERSIONS=%d, READ_RETRIES=%d)"
         % (KEEP, RETRIES))
    writer = ad.AtomicDocs(root)
    writer.put(doc, body(1))
    reader = ad.AtomicDocs(root)               # a second, independent store handle
    show_state(root, doc, "after v1 (store %s):" % root)

    paused, release = threading.Event(), threading.Event()
    attempts, result = [], {}

    def hook(d, v, attempt):                   # runs between CURRENT read and version open
        attempts.append((attempt, v))
        if attempt == 0:
            paused.set()
            release.wait(30)

    reader.read_hook = hook

    def run_reader():
        try:
            result["body"] = reader.get(doc)
        except Exception as e:                 # recorded, judged by the checks below
            result["error"] = e

    t = threading.Thread(target=run_reader, name="reader")
    t.start()
    paused.wait(10)
    step("A1  reader read CURRENT -> v%d and is now PAUSED before opening %s"
         % (attempts[0][1], ad._vname(attempts[0][1])))

    step("A2  writer saves v2..v6 while the reader sleeps")
    max_files, v1_gone_at, ok_retention = 0, None, True
    prev = on_disk(root, doc)[0]
    for v in range(2, 7):
        got = writer.put(doc, body(v))
        vs, tmps = on_disk(root, doc)
        removed = sorted(set(prev) - set(vs))
        prev = vs
        max_files = max(max_files, len(vs))
        ok_retention &= len(vs) <= KEEP and vs == list(range(max(1, got - KEEP + 1), got + 1))
        if 1 in removed and v1_gone_at is None:
            v1_gone_at = got
        note("save v%d committed -> on disk: %-12s (%d version files%s)%s"
             % (got, vlist(vs), len(vs), ", tmp=%s" % tmps if tmps else "",
                "   GC removed %s" % vlist(removed) if removed else ""))
    check("retention: never more than %d version files after a save's GC" % KEEP, ok_retention and max_files <= KEEP,
          "max seen %d" % max_files)
    check("v1 removed exactly when v4 committed (GC deletes v <= current-%d)" % KEEP, v1_gone_at == 4,
          "v1 gone after save v%s" % v1_gone_at)
    check("reader still paused during all saves (it read CURRENT before any of them)",
          t.is_alive() and attempts == [(0, 1)])

    step("A3  release the reader")
    release.set()
    t.join(10)
    for a, v in attempts:
        note("attempt %d: CURRENT -> v%d%s" % (a, v, "  -> open %s: FileNotFoundError (GC'd), retry"
                                                   % ad._vname(v) if v not in on_disk(root, doc)[0]
                                                   else "  -> open %s: OK, SHA-256 verified" % ad._vname(v)))
    got = result.get("body")
    note("reader returned: %s" % (describe_bytes(got, 20) if "body" in result else repr(result.get("error"))))
    check("reader got a whole committed version: v6 exactly (no error, no mix)",
          "error" not in result and got == body(6))
    check("retry was bounded: %d attempts used (<= READ_RETRIES=%d)" % (len(attempts), RETRIES),
          len(attempts) == 2 and len(attempts) <= RETRIES and attempts[1] == (1, 6))
    vs, tmps = on_disk(root, doc)
    check("at the end exactly v4, v5, v6 on disk, no temp files", vs == [4, 5, 6] and not tmps,
          vlist(vs))
    show_state(root, doc, "final state of part A:")

    step("A4  trade-off: a reader that ALREADY OPENED its version file is not hurt by GC")
    f = writer.fs._open_read(root + "/docs/%s/%s" % (doc, ad._vname(4)))  # FILE_SHARE_DELETE on Windows
    head = f.read(100)
    writer.put(doc, body(7))                   # GC now deletes v4 under the open handle
    vs, _ = on_disk(root, doc)
    rest = f.read()
    f.close()
    try:
        bd, _ = ad.decode_version(doc, 4, head + rest)
        ok = bd == body(4)
    except ad.CorruptVersion:
        ok = False
    note("opened v4, read 100 B, writer committed v7 (GC unlinked v4: on disk now %s)" % vlist(vs))
    note("then read the remaining %d B through the still-open handle: SHA-256 %s"
         % (len(rest), "verifies" if ok else "FAILS"))
    check("open handle survives GC unlink (POSIX unlink / Windows FILE_SHARE_DELETE)",
          ok and 4 not in vs)


# ----------------------------------------------------------------- part B
def part_b():
    root, doc = new_root("ko04-c3b-"), "hot"
    step("PART B - the bound: a reader whose version is GC'd before EVERY open")
    writer = ad.AtomicDocs(root)
    writer.put(doc, body(1))
    reader = ad.AtomicDocs(root)
    log = []

    def hostile_hook(d, v, attempt):           # each time the reader pauses, 3 commits land
        made = [writer.put(doc, body(writer_next[0] + i)) for i in range(KEEP)]
        writer_next[0] += KEEP
        log.append((attempt, v, made, on_disk(root, doc)[0]))

    writer_next = [2]
    reader.read_hook = hostile_hook
    t0 = time.perf_counter()
    err = None
    try:
        reader.get(doc)
    except ad.ReadContention as e:
        err = e
    ms = (time.perf_counter() - t0) * 1000
    for a, v, made, vs in log:
        note("attempt %d: CURRENT -> v%-2d | meanwhile writers commit %s | on disk %s -> open v%d: gone"
             % (a, v, vlist(made), vlist(vs), v))
    note("get() raised: %s: %s  (after %.0f ms)" % (type(err).__name__ if err else None, err, ms))
    check("ReadContention raised after exactly READ_RETRIES=%d attempts (no infinite loop)" % RETRIES,
          err is not None and len(log) == RETRIES and [a for a, *_ in log] == list(range(RETRIES)))
    check("each retry re-read CURRENT and saw a NEWER version (v1, v4, v7, ...)",
          [v for _, v, _, _ in log] == [1 + KEEP * i for i in range(RETRIES)])
    vs, tmps = on_disk(root, doc)
    check("retention still bounded while the reader starved: %d version files on disk" % len(vs),
          len(vs) == KEEP and all(len(x[3]) <= KEEP for x in log) and not tmps, vlist(vs))
    reader.read_hook = None
    latest = writer_next[0] - 1
    b = reader.get(doc)
    check("once the pressure stops, a normal get() returns the newest version v%d whole" % latest,
          b == body(latest))


# ----------------------------------------------------------------- part C
def part_c():
    root, doc = new_root("ko04-c3c-"), "stress"
    step("PART C - stress: %d reader threads looping get() vs a writer doing %d saves"
         % (STRESS_READERS, STRESS_SAVES))
    note("readers sleep 0-2 ms between CURRENT read and open; 5% of reads are 'preempted'")
    note("there until %d newer versions have committed (so their version is surely GC'd)" % KEEP)
    writer = ad.AtomicDocs(root)
    writer.put(doc, body(1))
    last = 1 + STRESS_SAVES
    done = threading.Event()
    committed = [1]
    stats = [{"reads": 0, "retries": 0, "contention": 0, "bad": [], "errors": [], "nonmono": 0,
              "preempted": 0, "max_att": 0}
             for _ in range(STRESS_READERS)]
    samples = {"n": 0, "max_v": 0, "max_tmp": 0, "hist": {}}

    def reader_loop(i):
        s = ad.AtomicDocs(root, auto_recover=False)   # recovery must not run under live writers
        st = stats[i]
        rng = random.Random(i)

        def hook(d, v, attempt):
            st["max_att"] = max(st["max_att"], attempt + 1)
            if attempt:
                st["retries"] += 1
            if attempt == 0 and rng.random() < 0.05:
                # preempted reader: stays descheduled until KEEP newer versions committed
                st["preempted"] += 1
                deadline = time.time() + 2
                while committed[0] < v + KEEP and time.time() < deadline and not done.is_set():
                    time.sleep(0.001)
            else:
                time.sleep(rng.uniform(0, 0.002 if attempt == 0 else 0.0005))

        s.read_hook = hook
        seen = 0
        while not done.is_set():
            try:
                b = s.get(doc)
            except ad.ReadContention:
                st["contention"] += 1
                continue
            except Exception as e:
                st["errors"].append(repr(e))
                continue
            st["reads"] += 1
            v = tag_of(b)
            if b != body(v) or not 1 <= v <= last:
                st["bad"].append(v)
            if v < seen:
                st["nonmono"] += 1
            seen = v

    def sampler():
        ddir = root + "/docs/" + doc
        while not done.is_set():
            try:
                names = os.listdir(ddir)
            except OSError:
                continue
            nv = sum(1 for n in names if ad._is_vname(n))
            nt = sum(1 for n in names if n.endswith(".tmp"))
            samples["n"] += 1
            samples["max_v"] = max(samples["max_v"], nv)
            samples["max_tmp"] = max(samples["max_tmp"], nt)
            samples["hist"][nv] = samples["hist"].get(nv, 0) + 1
            time.sleep(0.0005)

    threads = [threading.Thread(target=reader_loop, args=(i,)) for i in range(STRESS_READERS)]
    threads.append(threading.Thread(target=sampler))
    for t in threads:
        t.start()
    t0 = time.perf_counter()
    for v in range(2, last + 1):
        assert writer.put(doc, body(v)) == v
        committed[0] = v
    secs = time.perf_counter() - t0
    time.sleep(0.05)
    done.set()
    for t in threads:
        t.join(30)

    reads = sum(s["reads"] for s in stats)
    retries = sum(s["retries"] for s in stats)
    cont = sum(s["contention"] for s in stats)
    bad = sum(len(s["bad"]) for s in stats)
    errs = [e for s in stats for e in s["errors"]]
    note("writer: %d saves in %.1f s (%.1f ms/save, each = 6+ flushes)" % (STRESS_SAVES, secs,
                                                                           secs * 1000 / STRESS_SAVES))
    pre = sum(s["preempted"] for s in stats)
    max_att = max(s["max_att"] for s in stats)
    note("readers: %d reads (%d preempted), %d retries after a GC'd version, %d ReadContention,"
         % (reads, pre, retries, cont))
    note("         %d bad reads, %d errors; max attempts used by a single get(): %d of %d"
         % (bad, len(errs), max_att, RETRIES))
    note("directory sampled %d times; version-file count histogram: %s; max temp files: %d"
         % (samples["n"], dict(sorted(samples["hist"].items())), samples["max_tmp"]))
    note("(%d = steady state; %d appears only inside a save: new version renamed in, or the"
         % (KEEP, KEEP + 1))
    note(" commit done and the oldest version not yet unlinked - GC runs right after the commit)")
    check("every read returned a whole committed version (tag + full body verified)",
          reads > 0 and bad == 0 and not errs, "errors=%r" % errs[:3])
    check("reads are monotonic per reader (never went back to an older version)",
          all(s["nonmono"] == 0 for s in stats))
    check("GC'd-version retries happened and were absorbed within the bound (no ReadContention)",
          retries > 0 and cont == 0 and max_att <= RETRIES,
          "%d retries, max %d attempts per get" % (retries, max_att))
    check("disk never held more than KEEP_VERSIONS+1 = %d version files, temp files <= 2" % (KEEP + 1),
          samples["n"] > 0 and samples["max_v"] <= KEEP + 1 and samples["max_tmp"] <= 2,
          "max %d version files, %d temp" % (samples["max_v"], samples["max_tmp"]))
    vs, tmps = on_disk(root, doc)
    check("at the end: exactly the newest %d versions, CURRENT=v%d" % (KEEP, last),
          vs == list(range(last - KEEP + 1, last + 1)) and not tmps
          and ad.AtomicDocs(root).get(doc) == body(last), vlist(vs))


def explain():
    step("HOW IT WORKS (OS mechanism)")
    for line in [
        "Version files are immutable (copy-on-write): a reader never needs a lock, it reads",
        "  CURRENT (atomically replaced by rename), then opens that version and checks SHA-256.",
        "GC runs only AFTER the new CURRENT is durable and deletes only v <= current-%d," % KEEP,
        "  so the committed version and its predecessors are never touched; disk usage is",
        "  bounded at %d versions (+1 transiently inside a save)." % KEEP,
        "The only race is the window between reading CURRENT and opening the file. Losing it",
        "  means FileNotFoundError, which proves a newer version committed -> re-read CURRENT.",
        "Retries are bounded (READ_RETRIES=%d); a reader that keeps losing gets ReadContention" % RETRIES,
        "  instead of looping forever, and writers are never blocked to protect slow readers.",
        "Once a reader has OPENED the file it is safe: POSIX unlink only removes the name, the",
        "  inode lives until the last close; on Windows reads open with FILE_SHARE_DELETE so",
        "  DeleteFile succeeds and the open handle keeps reading (A4).",
        "Trade-off: we chose bounded retention + retry over pinning versions for readers",
        "  (leases/refcounts), which would let one stuck reader grow disk usage without bound.",
        "Assumption: version numbers are never reused, so a stale name can never resolve to",
        "  different content; any mix would be caught by the SHA-256 check anyway.",
    ]:
        note(line)


def main():
    t0 = time.time()
    print("%s %s  (python %s, %s)" % (ID, TITLE, sys.version.split()[0], sys.platform))
    part_a()
    part_b()
    part_c()
    explain()
    note("elapsed %.1f s" % (time.time() - t0))
    finish(ID, TITLE)


if __name__ == "__main__":
    main()
