#!/usr/bin/env python3
"""
KO-04-C4  Failure after commit before flush (Hard)

  "Inject a directory-fsync error after CURRENT rename. Report an
   outcome-unknown error and recover without deleting the newly committed
   version."
  Setup: v1 = A; write B; rename CURRENT to v2; simulated fsync EIO; reopen.

What this script does
  1. In-process: a RealFS subclass that fails (EIO) exactly the directory
     fsync that follows the CURRENT rename. put(B) must raise
     CommitUnknownFsync, poison the handle, and delete nothing. Reopening with
     a healthy file system must roll forward to B and keep v1.
  2. CLI: the same fault inside a real child process running atomicdocs.main;
     it must exit 4 with "OUTCOME UNKNOWN", and a later `get` returns B.
  3. Power loss (SimFS): after the failed flush, enumerate every disk state a
     power cut could leave. Some come back A, some B - which is exactly why the
     only honest answer at that moment is "outcome unknown".

Run:  python challenges/c4_post_commit_fsync.py   (or from inside challenges/)
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

from common import (ad, ROOT, TOOL, new_root, step, note, show_state, describe_bytes,
                    check, finish)
from simfs import SimFS

DOC = "doc"
A = b"\x41" * 64
B = b"\x42" * 64
C = b"\x43" * 64
WORK = os.path.dirname(new_root("ko04-c4-"))        # cleaned up by finish()


# ----------------------------------------------------------------- fault injector
class EIOAfterCommitFS(ad.RealFS):
    """Real file system, except: the first directory fsync after a rename onto
    .../CURRENT fails with EIO. Also records the calls it sees (event trace)."""
    name = "real+fault"

    def __init__(self):
        self.enabled = False
        self.armed = False
        self.injected = 0
        self.events = []
        self._fdname = {}
        self._in_dir_flush = False

    def _ev(self, s):
        if self.enabled:
            self.events.append(s)

    def create(self, p):
        fd = super().create(p)
        self._fdname[fd] = os.path.basename(p)
        self._ev("create   %s" % os.path.basename(p))
        return fd

    def open_append(self, p):
        fd = super().open_append(p)
        self._fdname[fd] = os.path.basename(p)
        return fd

    def write(self, fd, data):
        super().write(fd, data)
        self._ev("write    %s (%d B)" % (self._fdname.get(fd, "fd%d" % fd), len(data)))

    def fsync(self, fd):
        super().fsync(fd)
        if not self._in_dir_flush:          # fsync_dir flushes a directory fd via fsync()
            self._ev("fsync    %s" % self._fdname.get(fd, "fd%d" % fd))

    def close(self, fd):
        self._fdname.pop(fd, None)
        super().close(fd)

    def rename(self, a, b):
        super().rename(a, b)
        self._ev("rename   %s -> %s" % (os.path.basename(a), os.path.basename(b)))
        if self.enabled and b.endswith("/CURRENT"):
            self.armed = True                       # the NEXT fsync_dir will fail

    def fsync_dir(self, p):
        if self.armed:
            self.armed = False
            self.injected += 1
            self._ev("fsync_dir %s  -> EIO (INJECTED)" % os.path.basename(p))
            raise ad.FsyncError("injected EIO")
        self._in_dir_flush = True
        try:
            super().fsync_dir(p)
        finally:
            self._in_dir_flush = False
        self._ev("fsync_dir %s" % os.path.basename(p))

    def unlink(self, p):
        super().unlink(p)
        self._ev("unlink   %s" % os.path.basename(p))


class EIOAfterCommitSim(SimFS):
    """The same fault inside the power-loss simulator: the failed directory
    flush emits NO barrier, so nothing it should have made durable is."""

    def __init__(self, base):
        super().__init__(base)
        self.inject = True
        self.armed = False

    def rename(self, a, b):
        super().rename(a, b)
        if self.inject and b.endswith("/CURRENT"):
            self.armed = True

    def fsync_dir(self, p):
        if self.armed:
            self.armed = False
            self._done("fsync dir %s -> EIO (injected, no barrier)" % os.path.basename(p))
            raise ad.FsyncError("injected EIO")
        super().fsync_dir(p)


# ----------------------------------------------------------------- helpers
def ddir(root):
    return root + "/docs/" + DOC


def files(root):
    return sorted(os.listdir(ddir(root)))


def current_version(root):
    p = ddir(root) + "/CURRENT"
    if not os.path.exists(p):
        return 0
    try:
        return ad.decode_pointer(open(p, "rb").read())[0]
    except ad.CorruptVersion:
        return -1


def open_transactions(root):
    _, recs, _, _ = ad.scan_journal(open(root + "/journal.wal", "rb").read())
    done = {r["tx"] for r in recs if r["t"] != "BEGIN"}
    return [r for r in recs if r["t"] == "BEGIN" and r["tx"] not in done]


def fmt_report(rep):
    keys = ("rolled_forward", "rolled_back", "tmp_removed", "fell_back", "degraded", "journal")
    return ", ".join("%s=%s" % (k, rep[k]) for k in keys)


# ----------------------------------------------------------------- part 1
def in_process():
    root = new_root("ko04-c4-")
    step("Setup: v1 = A")
    v1 = ad.AtomicDocs(root).put(DOC, A)
    note("put A -> v%d   (%s)" % (v1, describe_bytes(A)))
    show_state(root, DOC)

    step("put(B) on a file system whose directory fsync after the CURRENT rename fails (EIO)")
    fs = EIOAfterCommitFS()
    s = ad.AtomicDocs(root, fs=fs)
    fs.enabled = True
    err = None
    try:
        v = s.put(DOC, B)
        note("put returned v%d (unexpected)" % v)
    except Exception as e:                          # noqa: BLE001 - we inspect the type below
        err = e
    fs.enabled = False
    note("recorded system calls of the failing put (commit ordering):")
    for ev in fs.events:
        mark = "   <-- COMMIT POINT (atomic)" if ev.startswith("rename") and ev.endswith("-> CURRENT") else ""
        note("   " + ev + mark)
    note("exception: %s: %s" % (type(err).__name__, err))
    check("fault injected exactly once, on the directory fsync after the CURRENT rename",
          fs.injected == 1, "injected=%d" % fs.injected)
    check("put raised CommitUnknownFsync", isinstance(err, ad.CommitUnknownFsync), type(err).__name__)
    check("... which is an ad.CommitUnknown (outcome unknown, not 'failed')", isinstance(err, ad.CommitUnknown))
    check("... and an ad.FsyncError (fsyncgate: flush failures are fatal for the handle)",
          isinstance(err, ad.FsyncError))
    check("the error message says the outcome is unknown", "outcome unknown" in str(err), str(err)[:60])

    try:
        s.put(DOC, C)
        poisoned = False
    except ad.Poisoned as e:
        poisoned = True
        note("next put on the same handle -> Poisoned: %s" % e)
    check("handle is poisoned: a further put raises ad.Poisoned (no blind fsync retry)", poisoned)

    show_state(root, DOC, "disk right after the failed put (nothing cleaned up, nothing deleted):")
    tx = open_transactions(root)
    check("CURRENT already names v2 (the rename happened before the flush failed)",
          current_version(root) == 2, "CURRENT=v%d" % current_version(root))
    check("v000002.dat was NOT deleted by the failed put",
          "v000002.dat" in files(root) and not any(e.startswith("unlink") for e in fs.events))
    check("journal: BEGIN v1->v2 without COMMIT (in flight)",
          len(tx) == 1 and tx[0]["old"] == 1 and tx[0]["new"] == 2)

    step("Reopen with a healthy file system -> recovery decides from what is on disk")
    s2 = ad.AtomicDocs(root)
    rep = s2.last_recovery
    note("recovery report: " + fmt_report(rep))
    show_state(root, DOC, "disk after recovery:")
    got = s2.get(DOC)
    note("get(%r) = %s" % (DOC, describe_bytes(got)))
    check("recovery reports rolled_forward for the document", DOC in rep["rolled_forward"], str(rep["rolled_forward"]))
    check("get() == B exactly (64 bytes of 0x42)", got == B)
    check("v2 present and CURRENT names v2", "v000002.dat" in files(root) and current_version(root) == 2)
    check("previous version v1 still present", "v000001.dat" in files(root))
    check("no temp files, no open transactions left",
          not [n for n in files(root) if n.endswith(".tmp")] and not open_transactions(root))
    v3 = s2.put(DOC, C)
    check("a further normal put(C) works -> v3", v3 == 3 and s2.get(DOC) == C, "v%d" % v3)
    show_state(root, DOC, "after put(C):")


# ----------------------------------------------------------------- part 2
CHILD = r"""
import sys
sys.path.insert(0, %r)
import atomicdocs as ad
_rename, _fsync_dir = ad.RealFS.rename, ad.RealFS.fsync_dir
armed = []
def rename(self, a, b):
    _rename(self, a, b)
    if b.endswith("/CURRENT"):
        armed.append(1)
def fsync_dir(self, p):
    if armed:
        armed.pop()
        raise ad.FsyncError("injected EIO")
    _fsync_dir(self, p)
ad.RealFS.rename, ad.RealFS.fsync_dir = rename, fsync_dir
sys.exit(ad.main(["atomicdocs.py"] + sys.argv[1:]))
""" % ROOT


def cli():
    step("CLI path: real child process, same injected EIO, then plain CLI get")
    root = new_root("ko04-c4cli-")
    ad.AtomicDocs(root).put(DOC, A)
    fileB = os.path.join(os.path.dirname(root), "B.bin")
    with open(fileB, "wb") as f:
        f.write(B)
    note("child: python -c <inject EIO on dir fsync after CURRENT rename; atomicdocs.main> "
         "<root> put %s B.bin" % DOC)
    p = subprocess.run([sys.executable, "-c", CHILD, root, "put", DOC, fileB],
                       capture_output=True, timeout=60)
    err = p.stderr.decode(errors="replace").strip()
    note("exit code %d, stderr: %s" % (p.returncode, err))
    check("CLI put exits with code 4", p.returncode == 4, "rc=%d" % p.returncode)
    check("CLI prints OUTCOME UNKNOWN", "OUTCOME UNKNOWN" in err)
    g = subprocess.run([sys.executable, TOOL, root, "get", DOC], capture_output=True, timeout=60)
    note("python atomicdocs.py <root> get %s  -> rc=%d, %s" % (DOC, g.returncode, describe_bytes(g.stdout)))
    check("CLI get after reopen (recovery) returns exactly B", g.returncode == 0 and g.stdout == B)
    h = subprocess.run([sys.executable, TOOL, root, "history", DOC], capture_output=True, timeout=60)
    for line in h.stdout.decode().splitlines():
        note("   history: " + line)
    check("CLI history: v1 and v2 both valid, v2 current",
          "'version': 1" in h.stdout.decode() and "'version': 2, 'bytes': 64, 'valid': True, 'current': True"
          in h.stdout.decode())


# ----------------------------------------------------------------- part 3
def classify(st, sim):
    dest = tempfile.mkdtemp(dir=WORK) + "/store"
    sim.materialize(st, dest)
    try:
        got = ad.AtomicDocs(dest).get(DOC)
        out = "A" if got == A else "B" if got == B else "EMPTY" if not got else "MIXED"
    except Exception as e:                          # noqa: BLE001
        out = "ERROR(%s)" % type(e).__name__
    shutil.rmtree(os.path.dirname(dest), ignore_errors=True)
    return out


def enumerate_states(sim, ks):
    seen, counts = {}, {}
    for k in ks:
        for mode, nopt, st in sim.crash_states(k):
            fp = st.fingerprint(sim.root)
            if fp not in seen:
                seen[fp] = classify(st, sim)
                counts[seen[fp]] = counts.get(seen[fp], 0) + 1
    return counts, len(seen)


def power_loss():
    step("Power loss after the failed flush (SimFS)")
    note("The EIO means: the directory entry change (CURRENT -> v2) may or may not have")
    note("reached the disk. SimFS replays the same put(B) with the same fault (the failed")
    note("fsync emits no durability barrier) and enumerates every disk state a power cut")
    note("at that moment could leave; the REAL recovery code reads each one.")
    base = new_root("ko04-c4sim-")
    ad.AtomicDocs(base).put(DOC, A)
    ad.AtomicDocs(base)                             # reopen = checkpoint, durable image
    sim = EIOAfterCommitSim(base)
    s = ad.AtomicDocs(base, fs=sim, auto_recover=False)
    try:
        s.put(DOC, B)
        err = None
    except Exception as e:                          # noqa: BLE001
        err = e
    check("SimFS replay: put raised CommitUnknownFsync", isinstance(err, ad.CommitUnknownFsync),
          type(err).__name__)
    k_fail = len(sim.ops)
    i = next(n for n, (lbl, _) in enumerate(sim.syscalls) if lbl.startswith("rename .CURRENT"))
    for lbl, k in sim.syscalls[max(0, i - 2):]:
        note("   k=%-3d %s" % (k, lbl))
    counts, n = enumerate_states(sim, [k_fail])
    got = " ".join("%s=%d" % kv for kv in sorted(counts.items()))
    note("power cut after the rename, before a successful directory flush:")
    note("   %d distinct disk states -> %s" % (n, got))
    check("every state recovers to exactly A or exactly B (never mixed, never empty)",
          set(counts) <= {"A", "B"}, got)
    check("both outcomes really occur (A=%d, B=%d) -> the outcome IS unknown"
          % (counts.get("A", 0), counts.get("B", 0)), counts.get("A", 0) > 0 and counts.get("B", 0) > 0)

    # Now reopen inside the simulator: recovery rolls forward and flushes.
    sim.inject = False
    r0 = len(sim.ops)
    s2 = ad.AtomicDocs(base, fs=sim)
    served = s2.get(DOC)
    r1 = len(sim.ops)
    note("reopen inside the simulator: recovery %s, serves %s"
         % (fmt_report(s2.last_recovery).split(", rolled_back")[0], describe_bytes(served)))
    during = [k for _, k in sim.syscalls if r0 <= k < r1]
    c_during, n_during = enumerate_states(sim, during)
    got_d = " ".join("%s=%d" % kv for kv in sorted(c_during.items()))
    note("power cut DURING recovery: %d distinct states -> %s" % (n_during, got_d))
    check("power cut during recovery -> still only A or B", set(c_during) <= {"A", "B"}, got_d)
    c_after, n_after = enumerate_states(sim, [r1])
    got_a = " ".join("%s=%d" % kv for kv in sorted(c_after.items()))
    note("power cut AFTER recovery returned: %d distinct states -> %s" % (n_after, got_a))
    check("after recovery served B, every power-loss state is B (decision made durable)",
          served == B and set(c_after) == {"B"}, got_a)


# ----------------------------------------------------------------- main
def main():
    t0 = time.time()
    print("KO-04-C4  Failure after commit, before flush")
    print("A = %s, B = %s, document %r; fault = simulated EIO from the directory fsync"
          % (describe_bytes(A), describe_bytes(B), DOC))
    print("that follows the CURRENT rename (FlushFileBuffers on the directory handle here).")
    step("Mechanism")
    note("rename(CURRENT.tmp -> CURRENT) is the commit point: it is atomic, so every")
    note("reader immediately sees v2. But the rename only changed the directory in")
    note("memory; it becomes durable when the DIRECTORY is flushed (fsync(dirfd) /")
    note("FlushFileBuffers). If that flush fails with EIO, we know neither 'committed and")
    note("durable' nor 'not committed':")
    note(" - reporting FAILURE would be a lie if the rename is already on disk (and")
    note("   deleting v2 to 'undo' it would destroy a committed version);")
    note(" - reporting SUCCESS would be a lie if a power cut loses the rename;")
    note(" - retrying fsync is unsafe (fsyncgate, PostgreSQL 2018: after a failed fsync")
    note("   the kernel may drop the dirty state, so a second fsync 'succeeds' falsely).")
    note("So the store raises CommitUnknownFsync, poisons the handle, deletes nothing past")
    note("the commit point, and lets recovery read the truth back from disk on reopen.")

    in_process()
    cli()
    power_loss()

    step("Assumptions")
    note("- the EIO is simulated (injected in the FS layer); the store code is unmodified;")
    note("- local file system with atomic same-directory rename; drive honours flushes;")
    note("- power loss is simulated by SimFS (ALICE model), not a real power cut.")
    note("total time %.1fs" % (time.time() - t0))
    finish("KO-04-C4", "Failure after commit before flush")


if __name__ == "__main__":
    main()
