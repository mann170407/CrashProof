#!/usr/bin/env python3
"""
KO-04-C1  Commit-point crash matrix (Easy)

  "Initialize A, save B with failure before CURRENT rename and after CURRENT
   rename, then reopen through recovery."
  Setup: A = 64 bytes 0x41, B = 64 bytes 0x42, local file system,
         controlled process failure only.

What this script does
  1. For each failure point (before_pointer_rename, after_pointer_rename), in a
     fresh store: put A -> v1, then run a REAL child process
     `python atomicdocs.py <root> put doc <fileB>` with ATOMICDOCS_CRASH_AT set,
     so the child dies with os._exit(137) at that exact point (no cleanup, no
     flush - like kill -9). Show the disk right after the crash, reopen the
     store (recovery runs), show the report and the disk again, and check the
     committed-version semantics.
  2. A compact matrix over four crash points x 20 repetitions.
  3. The same commit points under simulated POWER LOSS (SimFS): every legal
     post-crash disk state just before / just after the CURRENT rename.

Run:  python challenges/c1_commit_point.py      (or from inside challenges/)
"""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time

from common import (ad, TOOL, new_root, step, note, show_state, describe_bytes,
                    check, finish)
from simfs import SimFS

DOC = "doc"
A = b"\x41" * 64
B = b"\x42" * 64
REPS = 20
WORK = os.path.dirname(new_root("ko04-c1-"))       # cleaned up by finish()


# ----------------------------------------------------------------- helpers
def fresh_root():
    return tempfile.mkdtemp(dir=WORK) + "/store"


def ddir(root):
    return root + "/docs/" + DOC


def current_version(root):
    """Version named by CURRENT on disk (0 if missing, -1 if unparseable)."""
    p = ddir(root) + "/CURRENT"
    if not os.path.exists(p):
        return 0
    try:
        return ad.decode_pointer(open(p, "rb").read())[0]
    except ad.CorruptVersion:
        return -1


def files(root):
    return sorted(os.listdir(ddir(root)))


def tmp_files(root):
    return [n for n in files(root) if n.endswith(".tmp")]


def open_transactions(root):
    """Journal BEGIN records with no matching COMMIT/ABORT."""
    _, recs, _, _ = ad.scan_journal(open(root + "/journal.wal", "rb").read())
    done = {r["tx"] for r in recs if r["t"] != "BEGIN"}
    return [r for r in recs if r["t"] == "BEGIN" and r["tx"] not in done]


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()[:16]


def init_with_A(root):
    s = ad.AtomicDocs(root)
    v = s.put(DOC, A)
    return v


def crash_child(root, point):
    """Save B in a separate OS process that dies at `point`."""
    fileB = os.path.join(os.path.dirname(root), "B.bin")
    with open(fileB, "wb") as f:
        f.write(B)
    env = dict(os.environ, ATOMICDOCS_CRASH_AT=point)
    p = subprocess.run([sys.executable, TOOL, root, "put", DOC, fileB], env=env,
                       capture_output=True, timeout=60)
    return p.returncode, p


def fmt_report(rep):
    keys = ("rolled_forward", "rolled_back", "tmp_removed", "fell_back", "degraded", "journal")
    return ", ".join("%s=%s" % (k, rep[k]) for k in keys)


# ----------------------------------------------------------------- part 1
def walkthrough(point, expect_body, expect_name):
    root = fresh_root()
    step("Crash point %s  (expected after recovery: %s)" % (point, expect_name))
    v1 = init_with_A(root)
    note("put A in-process -> v%d   (%s)" % (v1, describe_bytes(A)))
    v1_hash = sha(ddir(root) + "/" + ad._vname(1))
    show_state(root, DOC, "disk before the failing save:")

    note("child: python atomicdocs.py <root> put %s B.bin   [ATOMICDOCS_CRASH_AT=%s]" % (DOC, point))
    rc, p = crash_child(root, point)
    check("%s: child process died with exit code 137 (os._exit, no cleanup)" % point,
          rc == 137, "rc=%s%s" % (rc, (" stderr=" + p.stderr.decode(errors="replace")[-200:]) if rc != 137 else ""))
    show_state(root, DOC, "disk right after the crash (nobody has cleaned up):")
    open_tx = open_transactions(root)
    check("%s: journal holds BEGIN v1->v2 without COMMIT" % point,
          len(open_tx) == 1 and open_tx[0]["old"] == 1 and open_tx[0]["new"] == 2)
    check("%s: v000002.dat (B, copy-on-write) already on disk" % point,
          "v000002.dat" in files(root))
    if point == "before_pointer_rename":
        check("%s: .CURRENT temp left behind, CURRENT still names v1" % point,
              len(tmp_files(root)) == 1 and tmp_files(root)[0].startswith(".CURRENT.")
              and current_version(root) == 1, "tmp=%s CURRENT=v%d" % (tmp_files(root), current_version(root)))
    else:
        check("%s: rename already happened - CURRENT names v2, no temp left" % point,
              not tmp_files(root) and current_version(root) == 2,
              "tmp=%s CURRENT=v%d" % (tmp_files(root), current_version(root)))

    note("reopen the store -> recovery runs")
    s = ad.AtomicDocs(root)
    note("recovery report: " + fmt_report(s.last_recovery))
    show_state(root, DOC, "disk after recovery:")
    got = s.get(DOC)
    note("get(%r) = %s" % (DOC, describe_bytes(got)))
    check("%s: get() == %s exactly (64 bytes)" % (point, expect_name), got == expect_body)
    check("%s: no temp files left" % point, not tmp_files(root), str(tmp_files(root)))
    if point == "before_pointer_rename":
        check("%s: recovery reports rolled_back" % point, DOC in s.last_recovery["rolled_back"])
        check("%s: uncommitted v2 removed, CURRENT names v1" % point,
              "v000002.dat" not in files(root) and current_version(root) == 1, str(files(root)))
    else:
        check("%s: recovery reports rolled_forward" % point, DOC in s.last_recovery["rolled_forward"])
        check("%s: CURRENT names v2, v2 present" % point,
              current_version(root) == 2 and "v000002.dat" in files(root), str(files(root)))
        check("%s: previous version v1 still kept" % point, "v000001.dat" in files(root))
    check("%s: v1 file byte-identical throughout (copy-on-write, never overwritten)" % point,
          sha(ddir(root) + "/" + ad._vname(1)) == v1_hash)
    v = s.put(DOC, b"C" * 64)
    check("%s: store is writable again after recovery" % point,
          v == current_version(root) and s.get(DOC) == b"C" * 64, "next put -> v%d" % v)


# ----------------------------------------------------------------- part 2
MATRIX = [
    # point                   expected  why
    ("torn_pointer",          "A", "half a pointer in the temp file; rename never happened"),
    ("before_pointer_rename", "A", "temp pointer complete + fsynced; rename never happened"),
    ("after_pointer_rename",  "B", "rename happened; directory not yet fsynced"),
    ("after_commit",          "B", "COMMIT record written; GC not yet run"),
]


def matrix():
    step("Crash matrix: real child process killed at each point, %d repetitions each" % REPS)
    t0 = time.time()
    rows = []
    for point, exp, why in MATRIX:
        counts, actions, bad = {}, set(), []
        for i in range(REPS):
            root = fresh_root()
            init_with_A(root)
            rc, _ = crash_child(root, point)
            s = ad.AtomicDocs(root)
            got = s.get(DOC)
            name = "A" if got == A else "B" if got == B else "OTHER"
            counts[name] = counts.get(name, 0) + 1
            rep = s.last_recovery
            actions.add("rolled_back" if rep["rolled_back"] else
                        "rolled_forward" if rep["rolled_forward"] else "nothing open")
            want_v = 1 if exp == "A" else 2
            if rc != 137 or name != exp or tmp_files(root) or current_version(root) != want_v:
                bad.append((i, rc, name, tmp_files(root), current_version(root)))
            shutil.rmtree(os.path.dirname(root), ignore_errors=True)
        got_s = " ".join("%s x%d" % (k, n) for k, n in sorted(counts.items()))
        rows.append((point, exp, got_s, "/".join(sorted(actions)), not bad, why))
        if bad:
            note("  failures at %s: %s" % (point, bad[:3]))
    note("")
    note("%-22s | %-8s | %-8s | %-27s | %s" % ("crash point", "expected", "got", "recovery action", "pass"))
    note("-" * 22 + "-+-" + "-" * 8 + "-+-" + "-" * 8 + "-+-" + "-" * 27 + "-+-----")
    for point, exp, got_s, act, ok, why in rows:
        note("%-22s | %-8s | %-8s | %-27s | %s" % (point, exp, got_s, act, "PASS" if ok else "FAIL"))
    note("")
    for point, exp, got_s, act, ok, why in rows:
        note("%-22s %s" % (point, why))
    note("(%d child processes in %.1fs)" % (len(MATRIX) * REPS, time.time() - t0))
    for point, exp, got_s, act, ok, why in rows:
        check("matrix %s: %d/%d runs recovered to exactly %s" % (point, REPS, REPS, exp), ok, got_s)


# ----------------------------------------------------------------- part 3
def classify(sim_state, sim, tag):
    dest = tempfile.mkdtemp(dir=WORK) + "/store"
    sim.materialize(sim_state, dest)
    try:
        got = ad.AtomicDocs(dest).get(DOC)
        out = "A" if got == A else "B" if got == B else "OTHER(%s)" % describe_bytes(got)
    except Exception as e:                                    # recovery itself failed
        out = "ERROR(%s)" % type(e).__name__
    shutil.rmtree(os.path.dirname(dest), ignore_errors=True)
    return out


def power_loss():
    step("Power loss at the same points (SimFS: unflushed writes / directory entries may be lost)")
    note("A process kill keeps the page cache; a power cut does not. SimFS records every")
    note("system call of put(B) as micro-ops and enumerates every disk state a power cut")
    note("could leave (ALICE model), then runs the REAL recovery code on each one.")
    base = fresh_root()
    init_with_A(base)
    ad.AtomicDocs(base)                                       # reopen = checkpoint, durable image
    for model, desc in (("W1", "atomic rename (ext4/xfs/btrfs/NTFS)"),
                        ("W0", "worst case: rename = delete dst, delete src, insert dst")):
        sim = SimFS(base, rename_model=model)
        s = ad.AtomicDocs(base, fs=sim, auto_recover=False)
        s.put(DOC, B)
        calls = sim.syscalls
        i = next(n for n, (lbl, _) in enumerate(calls) if lbl.startswith("rename .CURRENT"))
        k_before, k_after = calls[i - 1][1], calls[i][1]
        flush = next(n for n in range(i + 1, len(calls)) if calls[n][0].startswith("fsync dir"))
        k_flush = calls[flush][1]
        note("")
        note("model %s - %s" % (model, desc))
        note("  syscalls around the commit point:")
        for lbl, k in calls[max(0, i - 3): flush + 1]:
            note("    k=%-3d %s%s" % (k, lbl, "    <-- COMMIT POINT" if lbl.startswith("rename .CURRENT") else ""))
        cases = [("cut just before the CURRENT rename", k_before, {"A"}),
                 ("cut just after the rename (dir not flushed)", k_after, {"A", "B"}),
                 ("cut after the directory fsync", k_flush, {"B"})]
        for name, k, allowed in cases:
            seen, counts = {}, {}
            for mode, nopt, st in sim.crash_states(k):
                fp = st.fingerprint(sim.root)
                if fp not in seen:
                    seen[fp] = classify(st, sim, name)
                    counts[seen[fp]] = counts.get(seen[fp], 0) + 1
            ok = set(counts) <= allowed
            got = " ".join("%s=%d" % kv for kv in sorted(counts.items()))
            note("  %-44s %2d distinct states (%s): %-9s must be %s" %
                 (name, len(seen), mode, got, " or ".join(sorted(allowed))))
            check("SimFS %s: %s -> only %s" % (model, name, "/".join(sorted(allowed))), ok, got)


# ----------------------------------------------------------------- main
def main():
    t0 = time.time()
    print("KO-04-C1  Commit-point crash matrix")
    print("A = %s, B = %s, document %r, real local file system, temp directories only"
          % (describe_bytes(A), describe_bytes(B), DOC))
    step("Why: rename(2) of CURRENT is the single commit point")
    note("put(B) never touches v1. It writes B to a NEW file v000002.dat (copy-on-write),")
    note("fsyncs it, then writes a one-line pointer to a temp file .CURRENT.<pid>.tmp,")
    note("fsyncs it and rename()s it over CURRENT (MoveFileEx REPLACE|WRITE_THROUGH on")
    note("Windows). rename replaces the directory entry atomically: any observer sees")
    note("the old CURRENT or the new one, never a mix. So:")
    note("  dies BEFORE the rename -> CURRENT still names v1 -> A; recovery deletes the")
    note("                            orphan v2 + temp (rolled back)")
    note("  dies AFTER the rename  -> CURRENT names v2 -> B; recovery verifies v2's")
    note("                            SHA-256, keeps it, flushes the directory (rolled forward)")
    note("Recovery's rule: truth = CURRENT if it parses and its target verifies. The")
    note("journal (BEGIN without COMMIT) only says WHICH saves were in flight.")

    walkthrough("before_pointer_rename", A, "A")
    walkthrough("after_pointer_rename", B, "B")
    matrix()
    power_loss()

    step("Assumptions")
    note("- controlled failure = os._exit(137) in a real child process at a named point")
    note("  (ATOMICDOCS_CRASH_AT); the OS keeps the page cache, so this is a process crash;")
    note("- local file system with atomic same-directory rename (NTFS here; ext4/xfs/APFS);")
    note("- power loss is simulated (SimFS), not a real power cut; drive honours flushes.")
    note("total time %.1fs" % (time.time() - t0))
    finish("KO-04-C1", "Commit-point crash matrix")


if __name__ == "__main__":
    main()
