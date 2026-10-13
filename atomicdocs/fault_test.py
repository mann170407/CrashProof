#!/usr/bin/env python3
"""
fault_test.py - injected I/O faults and media corruption.

  1. fsync fails with EIO (fsyncgate): put() must raise, never report success,
     poison the store (no retry), and after reopening the document must be
     exactly OLD or NEW.
  2. Disk full (ENOSPC) mid-write: clean abort, document unchanged, temp file
     removed, store keeps working.
  3. Short writes (the kernel writes fewer bytes than asked): must still store
     the full document.
  4. Bit flip in the committed version: detected by SHA-256, recovery falls
     back to the previous version and reports it as degraded.
  5. Torn / garbage journal tail and mid-log corruption: classified, store opens.
  6. Torn or garbage CURRENT pointer: recovery rebuilds it from verified versions.
  7. Concurrency: 16 processes writing one document, 8 writing distinct docs.
  8. Idempotent recovery: running it twice changes nothing the second time.
"""
import errno, json, os, random, shutil, subprocess, sys, tempfile
from concurrent.futures import ThreadPoolExecutor
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import atomicdocs as ad

TOOL = os.path.join(HERE, "atomicdocs.py")
results = {}


def store(tmp=None):
    d = tempfile.mkdtemp(prefix="fault-") + "/s"
    s = ad.AtomicDocs(d)
    s.put("doc", b"OLD" * 100)
    return d, s


class FaultyFS(ad.RealFS):
    def __init__(self, fsync_fail_at=None, enospc_after=None, short=False):
        self.n_fsync, self.fsync_fail_at = 0, fsync_fail_at
        self.written, self.enospc_after, self.short = 0, enospc_after, short

    def fsync(self, fd):
        self.n_fsync += 1
        if self.n_fsync == self.fsync_fail_at:
            raise ad.FsyncError("injected EIO")
        super().fsync(fd)

    def write(self, fd, data):
        view = memoryview(bytes(data))
        while view:
            chunk = view[: max(1, len(view) // 3)] if self.short else view
            if self.enospc_after is not None and self.written + len(chunk) > self.enospc_after:
                part = max(0, self.enospc_after - self.written)
                if part:
                    os.write(fd, chunk[:part]); self.written += part
                raise OSError(errno.ENOSPC, "No space left on device (injected)")
            n = os.write(fd, chunk)
            self.written += n
            view = view[n:]


def check(name, ok, detail=""):
    results[name] = {"pass": bool(ok), "detail": detail}
    print(("PASS " if ok else "FAIL ") + name + ("  - " + detail if detail else ""))


# 1. fsync EIO at every fsync position inside put()
probe = FaultyFS(); d, s = store()
s.fs = probe; s.put("doc", b"probe")
n_fsyncs = probe.n_fsync
outcomes = []
for i in range(1, n_fsyncs + 1):
    d, s = store()
    s.fs = FaultyFS(fsync_fail_at=i)
    raised = False
    try:
        s.put("doc", b"NEW" * 120)
    except ad.FsyncError:
        raised = True
    try:
        s.put("doc", b"again"); retried = True
    except ad.Poisoned:
        retried = False
    got = ad.AtomicDocs(d).get("doc")
    outcomes.append((i, raised, retried, "OLD" if got == b"OLD" * 100 else "NEW" if got == b"NEW" * 120 else "CORRUPT"))
    shutil.rmtree(os.path.dirname(d))
check("fsync EIO at each of %d fsyncs in put()" % n_fsyncs,
      all(r and not rt and o in ("OLD", "NEW") for _, r, rt, o in outcomes),
      "raised + poisoned every time; recovered to " + ",".join(o for *_, o in outcomes))
results["fsync_eio_detail"] = outcomes

# 2. ENOSPC part-way through the data file
d, s = store()
s.fs = FaultyFS(enospc_after=1500)
try:
    s.put("doc", b"N" * 5000); aborted = False
except ad.StoreError as e:
    aborted = "ENOSPC" in str(e) or "space" in str(e)
leftovers = [n for n in os.listdir(d + "/docs/doc") if n.endswith(".tmp")]
s.fs = ad.RealFS()
after_ok = s.get("doc") == b"OLD" * 100 and s.put("doc", b"later") and s.get("doc") == b"later"
check("disk full (ENOSPC) mid-write", aborted and not leftovers and after_ok,
      "clean abort, no temp files left, document unchanged, store still writable")
shutil.rmtree(os.path.dirname(d))

# 3. short writes
d, s = store()
s.fs = FaultyFS(short=True)
big = os.urandom(100_000)
s.put("doc", big)
check("short writes are completed", ad.AtomicDocs(d).get("doc") == big, "100 KB written in 1/3 chunks")
shutil.rmtree(os.path.dirname(d))

# 4. bit flip in committed version
d, s = store(); s.put("doc", b"version two")
p = d + "/docs/doc/v000002.dat"
b = bytearray(open(p, "rb").read()); b[-2] ^= 0x10; open(p, "wb").write(b)
s2 = ad.AtomicDocs(d)
check("bit flip in committed version", s2.get("doc") == b"OLD" * 100 and s2.last_recovery["degraded"],
      "SHA-256 mismatch detected; fell back to v1 and flagged degraded: %s" % s2.last_recovery["degraded"][0]["why"])
shutil.rmtree(os.path.dirname(d))

# 5. journal: torn tail, garbage tail, mid-log corruption
for kind in ("torn", "zeros", "mid"):
    d, s = store()
    env = dict(os.environ, ATOMICDOCS_CRASH_AT="after_begin")
    subprocess.run([sys.executable, TOOL, d, "put", "doc", "-"], input=b"never", env=env)
    j = d + "/journal.wal"
    raw = bytearray(open(j, "rb").read())
    if kind == "torn":
        raw = raw[:-5]
    elif kind == "zeros":
        raw += b"\0" * 300
    else:
        raw[20] ^= 0xFF; raw += os.urandom(2000)
    open(j, "wb").write(raw)
    s2 = ad.AtomicDocs(d)
    check("journal %s" % kind, s2.get("doc") == b"OLD" * 100,
          "journal classified '%s', store opened, doc intact" % s2.last_recovery["journal"])
    shutil.rmtree(os.path.dirname(d))

# 6. CURRENT pointer torn / garbage / deleted
for kind in ("torn", "garbage", "deleted"):
    d, s = store(); s.put("doc", b"v2-content")
    c = d + "/docs/doc/CURRENT"
    if kind == "torn":
        open(c, "wb").write(open(c, "rb").read()[:20])
    elif kind == "garbage":
        open(c, "wb").write(os.urandom(60))
    else:
        os.unlink(c)
    s2 = ad.AtomicDocs(d)
    check("CURRENT %s" % kind, s2.get("doc") == b"v2-content" and ad.AtomicDocs(d).last_recovery["fell_back"] == [],
          "rebuilt from newest verified version")
    shutil.rmtree(os.path.dirname(d))


# 7. concurrency (real processes)
def child_put(root, doc, data):
    return subprocess.run([sys.executable, TOOL, root, "put", doc, "-"], input=data, capture_output=True).returncode


d, s = store()
blobs = [bytes([65 + k]) * (40_000 + k) for k in range(16)]
with ThreadPoolExecutor(16) as ex:
    rcs = list(ex.map(lambda b: child_put(d, "doc", b), blobs))
final = ad.AtomicDocs(d).get("doc")
vers = [h["version"] for h in ad.AtomicDocs(d).history("doc")]
check("16 concurrent writers, one document", all(r == 0 for r in rcs) and final in blobs and max(vers) == 17,
      "all exited 0, versions 1..17, final = one writer's exact payload")
with ThreadPoolExecutor(8) as ex:
    rcs = list(ex.map(lambda k: child_put(d, "doc%d" % k, b"x%d" % k), range(8)))
s3 = ad.AtomicDocs(d)
check("8 concurrent writers, distinct documents", all(s3.get("doc%d" % k) == b"x%d" % k for k in range(8)))
shutil.rmtree(os.path.dirname(d))

# 8. idempotent recovery
d, s = store()
env = dict(os.environ, ATOMICDOCS_CRASH_AT="after_pointer_rename")
subprocess.run([sys.executable, TOOL, d, "put", "doc", "-"], input=b"NEWER", env=env)
r1 = ad.AtomicDocs(d, auto_recover=False).recover()
r2 = ad.AtomicDocs(d, auto_recover=False).recover()
check("recovery is idempotent", r1["rolled_forward"] == ["doc"] and r2["rolled_forward"] == [] and ad.AtomicDocs(d).get("doc") == b"NEWER",
      "1st run rolled forward, 2nd run found nothing to do")
shutil.rmtree(os.path.dirname(d))

# 9. error after the commit point must not delete the committed version
class FailAfterCommit(ad.RealFS):
    def __init__(self): self.armed = False
    def rename(self, a, b):
        super().rename(a, b)
        if b.endswith("/CURRENT"): self.armed = True
    def fsync_dir(self, p):
        if self.armed:
            self.armed = False
            raise OSError(errno.EMFILE, "Too many open files (injected)")
        super().fsync_dir(p)
d, s = store(); s.fs = FailAfterCommit()
try:
    s.put("doc", b"NEW-after-commit"); outcome = "returned"
except ad.CommitUnknown:
    outcome = "CommitUnknown"
files = os.listdir(d + "/docs/doc")
got = ad.AtomicDocs(d).get("doc")
check("non-fsync error after the commit point", outcome == "CommitUnknown" and s.poisoned and "v000002.dat" in files and got == b"NEW-after-commit",
      "raised CommitUnknown, poisoned, nothing deleted; recovery kept the new version")
shutil.rmtree(os.path.dirname(d))

# 10. disk full on the COMMIT record: the save is already durable, so it succeeds
class FullAtCommit(ad.RealFS):
    def write(self, fd, data):
        if b'"COMMIT"' in bytes(data):
            raise OSError(errno.ENOSPC, "No space left on device (injected)")
        super().write(fd, data)
d, s = store(); s.fs = FullAtCommit()
v = s.put("doc", b"NEW-durable")
check("disk full on the completion record", v == 2 and s.poisoned and ad.AtomicDocs(d).get("doc") == b"NEW-durable",
      "put() returned v2 (already durable), handle poisoned for reopen, no duplicate version")
shutil.rmtree(os.path.dirname(d))

# 11. only version corrupted: never silently delete the document
d = tempfile.mkdtemp(prefix="fault-") + "/s"
ad.AtomicDocs(d).put("doc", b"only version")
p = d + "/docs/doc/v000001.dat"
b = bytearray(open(p, "rb").read()); b[-1] ^= 1; open(p, "wb").write(b)
s2 = ad.AtomicDocs(d)
try:
    s2.get("doc"); readable = True
except ad.CorruptVersion:
    readable = False
check("only version corrupted", not readable and os.path.exists(p) and s2.last_recovery["degraded"],
      "files kept, reported degraded, get() raises CorruptVersion instead of returning nothing")
shutil.rmtree(os.path.dirname(d))

# 12. first save of a brand-new document from 8 processes at once (mkdir race)
fails = 0
for trial in range(10):
    d = tempfile.mkdtemp(prefix="fault-") + "/s"; ad.AtomicDocs(d)
    with ThreadPoolExecutor(8) as ex:
        rcs = list(ex.map(lambda k: child_put(d, "fresh", b"w%d" % k), range(8)))
    fails += sum(r != 0 for r in rcs)
    shutil.rmtree(os.path.dirname(d))
check("8 processes create the same new document", fails == 0, "10 trials x 8 writers, 0 failures")

# 13. lock-free reader racing a writer that garbage-collects old versions
import threading
d, s = store()
stop, errors, reads = False, [], [0]
def reader():
    r = ad.AtomicDocs(d, auto_recover=False)
    while not stop:
        try:
            r.get("doc"); reads[0] += 1
        except Exception as e:
            errors.append(repr(e))
t = threading.Thread(target=reader); t.start()
for i in range(300):
    s.put("doc", b"x%d" % i)
stop = True; t.join()
check("reader racing writer + garbage collection", not errors, "%d reads during 300 saves, 0 errors" % reads[0])
shutil.rmtree(os.path.dirname(d))

# 14. compare-and-swap: a stale expect is rejected before anything is written
def journal_records(root):
    return ad.scan_journal(open(root + "/journal.wal", "rb").read())[1]
d, s = store()
v2 = s.put("doc", b"CAS-v2", expect=1)
files_before = sorted(os.listdir(d + "/docs/doc"))
begins_before = [r for r in journal_records(d) if r["t"] == "BEGIN"]
conflicts = []
for stale in (1, 0, 7):                       # stale reader, "must not exist", future version
    try:
        s.put("doc", b"STALE-%d" % stale, expect=stale); conflicts.append(None)
    except ad.Conflict as e:
        conflicts.append((e.expected, e.actual))
begins_after = [r for r in journal_records(d) if r["t"] == "BEGIN"]
cli = subprocess.run([sys.executable, TOOL, d, "put", "doc", "-", "--expect", "1"],
                     input=b"STALE-cli", capture_output=True)
unchanged = (ad.AtomicDocs(d).get("doc") == b"CAS-v2" and sorted(os.listdir(d + "/docs/doc")) == files_before
             and "v000003.dat" not in files_before)
s4 = ad.AtomicDocs(d)
v3 = s4.put("doc", b"CAS-v3", expect=2)
fresh_ok = s4.put("brand-new", b"first", expect=0) == 1
check("compare-and-swap conflict", v2 == 2 and conflicts == [(1, 2), (0, 2), (7, 2)] and not s.poisoned
      and begins_after == begins_before and len(begins_before) == 2 and cli.returncode == 3 and unchanged
      and v3 == 3 and s4.get("doc") == b"CAS-v3" and fresh_ok,
      "stale expect raised Conflict (API + CLI exit 3), document unchanged, no new version file, "
      "no BEGIN journaled; matching expect then commits")
shutil.rmtree(os.path.dirname(d))

# 15. directory fsync after the CURRENT rename fails: committed but not known durable
class DirFsyncFailsAfterCommit(ad.RealFS):
    def __init__(self): self.armed, self.fired = False, False
    def rename(self, a, b):
        super().rename(a, b)
        if b.endswith("/CURRENT"): self.armed = True
    def fsync(self, fd):                      # the next flush is the directory fsync
        if self.armed:
            self.armed, self.fired = False, True
            raise ad.FsyncError("injected EIO on directory fsync")
        super().fsync(fd)
d, s = store(); fs = DirFsyncFailsAfterCommit(); s.fs = fs
try:
    s.put("doc", b"NEW-unknown"); outcome = "returned"
except ad.CommitUnknownFsync as e:
    outcome = "CommitUnknownFsync" if isinstance(e, ad.CommitUnknown) and isinstance(e, ad.FsyncError) else "wrong-type"
except Exception as e:
    outcome = type(e).__name__
try:
    s.put("doc", b"retry"); poisoned_put = False
except ad.Poisoned:
    poisoned_put = True
files = os.listdir(d + "/docs/doc")
s5 = ad.AtomicDocs(d)
check("directory fsync fails after the CURRENT rename", fs.fired and outcome == "CommitUnknownFsync" and s.poisoned
      and poisoned_put and "v000002.dat" in files and s5.get("doc") == b"NEW-unknown"
      and s5.last_recovery["rolled_forward"] == ["doc"] and os.path.exists(d + "/docs/doc/v000002.dat"),
      "raised CommitUnknownFsync (CommitUnknown + FsyncError), poisoned, v2 kept; reopen rolled forward to NEW")
shutil.rmtree(os.path.dirname(d))

# 16. process killed after the CURRENT temp file is durable but before the rename
d, s = store()
env = dict(os.environ, ATOMICDOCS_CRASH_AT="before_pointer_rename")
rc = subprocess.run([sys.executable, TOOL, d, "put", "doc", "-"], input=b"NEVER-COMMITTED", env=env).returncode
left = sorted(os.listdir(d + "/docs/doc"))
staged = "v000002.dat" in left and any(n.startswith(".CURRENT.") and n.endswith(".tmp") for n in left)
s6 = ad.AtomicDocs(d)
after = sorted(os.listdir(d + "/docs/doc"))
check("crash before the CURRENT rename", rc == 137 and staged and s6.get("doc") == b"OLD" * 100
      and s6.last_recovery["rolled_back"] == ["doc"] and "v000002.dat" not in after
      and not [n for n in after if n.endswith(".tmp")],
      "v2 + CURRENT temp were on disk; recovery rolled back to OLD and removed both")
shutil.rmtree(os.path.dirname(d))

json.dump(results, open(os.path.join(HERE, "fault_results.json"), "w"), indent=1)
bad = [k for k, v in results.items() if isinstance(v, dict) and not v["pass"]]
print("\nRESULT:", "PASS - all %d fault scenarios" % sum(1 for v in results.values() if isinstance(v, dict)) if not bad else "FAIL %s" % bad)
sys.exit(1 if bad else 0)
