"""
tracing.py - record every system call the REAL AtomicDocs store makes, as
animation-ready events for the Crash Lab presentation UI (see ui/CONTRACT.md).

    Recorder    the timeline of one scenario: events (seq-ordered, thread-safe),
                checks, wall-clock offsets, and the per-file durability model
                shared by every TracingFS that works on the same store.
    TracingFS   atomicdocs.RealFS + recording. The store code is unmodified; it
                simply receives this object as its `fs`. After every recorded
                syscall the Recorder takes a DiskSnapshot of the document.
    snapshot()  what is on disk right now: files (kind, size, verified?,
                durable?), the parsed CURRENT, the valid journal records, and
                what a lock-free reader would get - computed read-only, without
                taking any store lock.

Durability model (page cache vs. stable storage, per file name):
    dataDurable  false after write() until fsync() of that file
    entryDurable false after create()/rename() until fsync() of its directory
    rename(a, b) carries a's dataDurable over to b (the inode moves, not the data)
A process crash keeps the page cache (the flags stay as they are); only an
fsync - by the writer or later by recovery - turns them true.

Crashes are in-process: store.crash_at makes the store call fs.crash(), which
raises the private _Crash (a BaseException, so no `except Exception` in the
store can swallow it). The scenario catches it and discards the store object.
Only the `finally` blocks that close fds / release locks still run - exactly
what the kernel does for a process killed with kill -9. Every later
state-changing call on a crashed TracingFS raises _Crash again.
"""
import hashlib
import json
import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STORE_DIR = os.path.join(os.path.dirname(HERE), "atomicdocs")
if STORE_DIR not in sys.path:
    sys.path.insert(0, STORE_DIR)

import atomicdocs as ad  # noqa: E402
import platform_info  # noqa: E402

ad._CRASH_AT = None                      # never let an env var crash the server
os.environ.pop("ATOMICDOCS_CRASH_AT", None)

_PLAIN = ad.RealFS()                     # untraced reads for snapshots (FILE_SHARE_DELETE on Windows)


class _Crash(BaseException):
    """Private: the traced process 'died' here (kill -9 semantics)."""


# ----------------------------------------------------------------- small helpers
def short(name):
    """Readable label for a temp name: '.v000002.dat.1234.ab12cd34.tmp' -> '.v000002.dat.tmp'."""
    if name.endswith(".tmp"):
        parts = name[:-4].split(".")
        if len(parts) >= 3 and parts[-2].isdigit():
            return ".".join(parts[:-2]) + ".tmp"
    return name


def preview(b, n=24):
    """First n printable chars; non-printables shown as a middle dot."""
    return "".join(chr(c) if 32 <= c < 127 else "·" for c in b[:n])


def fill(b):
    """Single-char summary when the bytes are one repeated byte, else None."""
    if b and b.count(b[:1]) == len(b):
        return chr(b[0]) if 32 <= b[0] < 127 else "·"
    return None


def _key(p):
    return os.path.normcase(os.path.abspath(p))


def _vnum(name):
    """N from 'v00000N.dat', or from a data temp '.v00000N.dat.<pid>.<hex>.tmp'."""
    if ad._is_vname(name):
        return int(name[1:7])
    if name.startswith(".v") and name.endswith(".tmp") and ad._is_vname(name[1:12]):
        return int(name[2:8])
    return None


def classify_kind(name):
    if name.endswith(".lock"):
        return "lock"
    if name.endswith(".tmp"):
        return "tmp"
    if name == "CURRENT":
        return "current"
    if ad._is_vname(name):
        return "version"
    if name == "journal.wal":
        return "journal"
    return "other"


def journal_recs(recs):
    """Contract JournalRec: t, tx, old?, new?, why? (doc/sha/ts dropped)."""
    out = []
    for r in recs:
        j = {"t": r.get("t"), "tx": r.get("tx")}
        for k in ("old", "new", "why"):
            if k in r:
                j[k] = r[k]
        out.append(j)
    return out


def read_disk_body(doc, ddir):
    """What a lock-free get() would return right now (no locks, no writes):
    CURRENT must parse and name a version whose SHA-256 verifies."""
    try:
        v, sha = ad.decode_pointer(_PLAIN.read(ddir + "/CURRENT"))
        body, _ = ad.decode_version(doc, v, _PLAIN.read(ddir + "/" + ad._vname(v)), sha)
        return v, body
    except (OSError, ad.CorruptVersion, ValueError):
        return None, None


# ----------------------------------------------------------------- recorder
class Recorder:
    """Timeline of one scenario. Thread-safe: seq is assigned under the lock,
    so events from a reader thread and a writer thread interleave in true
    chronological order."""

    def __init__(self, root, doc, naive_file=None):
        self.root = root                    # store root (atomic) or directory (naive)
        self.doc = doc                      # document shown in snapshots
        self.naive_file = naive_file        # naive mode: path of the in-place file
        self.events = []
        self.checks = []
        self.lock = threading.RLock()
        self.t0 = time.perf_counter()
        self.dur = {}                       # _key(path) -> [dataDurable, entryDurable]

    # -- durability model ------------------------------------------------
    def d_get(self, p):
        return self.dur.get(_key(p), [True, True])     # untouched files: durable

    def d_set(self, p, data=None, entry=None):
        cur = list(self.d_get(p))
        if data is not None:
            cur[0] = data
        if entry is not None:
            cur[1] = entry
        self.dur[_key(p)] = cur

    def d_rename(self, a, b):
        src = self.dur.pop(_key(a), [True, True])
        self.dur[_key(b)] = [src[0], False]

    def d_unlink(self, p):
        self.dur.pop(_key(p), None)

    def d_dirsync(self, d):
        kd = _key(d)
        for k, v in self.dur.items():
            if os.path.dirname(k) == kd:
                v[1] = True

    # -- snapshots ---------------------------------------------------------
    def ddir(self):
        if self.naive_file:
            return os.path.dirname(self.naive_file)
        return self.root + "/docs/" + self.doc

    def snapshot(self):
        if self.naive_file:
            return self._snapshot_naive()
        ddir = self.ddir()
        try:
            names = sorted(os.listdir(ddir))
        except OSError:
            names = []
        files, current = [], None
        for n in names:
            if n.endswith(".lock"):
                continue
            p = ddir + "/" + n
            try:
                raw = _PLAIN.read(p)
            except OSError:                 # vanished / is a directory
                continue
            kind = classify_kind(n)
            f = {"name": n, "kind": kind, "size": len(raw), "version": None, "valid": None,
                 "preview": "", "fill": None}
            if kind == "version":
                f["version"] = int(n[1:7])
                body = raw[ad.VHDR.size:]
                try:
                    ad.decode_version(self.doc, f["version"], raw)
                    f["valid"] = True
                except ad.CorruptVersion:
                    f["valid"] = False
                f["preview"], f["fill"] = preview(body), fill(body)
            elif kind == "current":
                try:
                    v, _ = ad.decode_pointer(raw)
                    f["valid"], current = True, {"version": v, "ok": True}
                except ad.CorruptVersion:
                    f["valid"] = False
                    parts = raw.decode("ascii", "replace").split()
                    v = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
                    current = {"version": v, "ok": False}
                f["preview"] = preview(raw.rstrip(b"\n"))
            elif kind == "tmp":
                # Interpretation: temp files get a preview too (the frame body of a
                # data temp, the pointer line of a CURRENT temp) so the UI can show
                # a half-written temp. version/valid stay null as the contract says.
                body = raw[ad.VHDR.size:] if _vnum(n) is not None else raw.rstrip(b"\n")
                f["preview"], f["fill"] = preview(body), fill(body)
            dd, de = self.d_get(p)
            f["dataDurable"], f["entryDurable"] = dd, de
            files.append(f)
        jp = self.root + "/journal.wal"
        try:
            _, recs, _, status = ad.scan_journal(_PLAIN.read(jp))
        except FileNotFoundError:
            recs, status = [], "missing"
        except OSError:
            recs, status = [], "missing"
        _, body = read_disk_body(self.doc, ddir)
        return {"doc": self.doc, "files": files, "current": current,
                "journal": journal_recs(recs), "journalStatus": status,
                "readable": None if body is None else preview(body),
                "readableFill": None if body is None else fill(body)}

    def _snapshot_naive(self):
        p = self.naive_file
        files, body = [], None
        try:
            body = _PLAIN.read(p)
            n = os.path.basename(p)
            dd, de = self.d_get(p)
            # Interpretation: the naive document is a plain file (kind "other");
            # its raw bytes ARE the body, so preview/fill show them.
            files.append({"name": n, "kind": "other", "size": len(body), "version": None,
                          "valid": None, "preview": preview(body), "fill": fill(body),
                          "dataDurable": dd, "entryDurable": de})
        except OSError:
            pass
        return {"doc": self.doc, "files": files, "current": None, "journal": [],
                "journalStatus": "missing",
                "readable": None if body is None else preview(body),
                "readableFill": None if body is None else fill(body)}

    # -- events --------------------------------------------------------------
    def ms(self):
        return round((time.perf_counter() - self.t0) * 1000, 2)

    def emit(self, kind, lane, label, detail=None, disk=None, **kw):
        """Append one contract Event. disk=None -> snapshot for the kinds that
        carry one (phase, syscall, crash, fault, result)."""
        with self.lock:
            ev = {"seq": len(self.events), "lane": lane, "kind": kind, "label": label}
            for k in ("op", "path"):
                if kw.get(k) is not None:
                    ev[k] = kw[k]
            if kw.get("op"):                # the real OS call behind this op on this kernel
                ev["native"] = kw.get("native") or platform_info.info()["native"].get(kw["op"], kw["op"])
            if detail:
                ev["detail"] = detail
            if kw.get("commit"):
                ev["commit"] = True
            if kw.get("ok") is not None:
                ev["ok"] = bool(kw["ok"])
            if disk is None and kind in ("phase", "syscall", "crash", "fault", "result"):
                disk = self.snapshot()
            if disk:
                ev["disk"] = disk
            ev["t"] = self.ms()
            self.events.append(ev)
            return ev

    def phase(self, lane, label, detail=None):
        return self.emit("phase", lane, label, detail)

    def note(self, lane, label, detail=None):
        return self.emit("note", lane, label, detail)

    def result(self, lane, label, ok=None, detail=None):
        return self.emit("result", lane, label, detail, ok=ok)

    def check(self, name, ok, detail=None, lane="system"):
        ok = bool(ok)
        c = {"name": name, "pass": ok}
        if detail:
            c["detail"] = str(detail)
        with self.lock:
            self.checks.append(c)
            self.emit("check", lane, name, str(detail) if detail else None, ok=ok)
        return ok

    def crash(self, lane, point, detail=None):
        return self.emit("crash", lane, "CRASH at %s (kill -9)" % point, detail or (
            "The process dies right here: no cleanup code, no more system calls. A process "
            "crash keeps the kernel page cache, so everything already written is still there "
            "- but whatever was not fsynced (dataDurable/entryDurable false) would not survive "
            "a power cut."))

    def result_dict(self, sid, title, params, summary, extra=None):
        ok = bool(self.checks) and all(c["pass"] for c in self.checks)
        out = {"id": sid, "title": title, "params": params, "events": self.events,
               "checks": self.checks, "pass": ok, "summary": summary}
        if extra is not None:
            out["extra"] = extra
        return out


# ----------------------------------------------------------------- traced FS
class TracingFS(ad.RealFS):
    """RealFS that reports every state-changing system call to a Recorder.

    lane    swimlane the calls belong to ("writer", "recovery", "client1", ...)
    reads   also record read() calls (readers, recovery)
    locks   also record the per-document lock / unlock
    quiet   keep the durability model up to date but emit no events
    fault   "eio_after_commit": the first fsync of a directory after a rename
            onto CURRENT fails with EIO (KO-04-C4)
    """
    name = "real+trace"

    def __init__(self, rec, lane, reads=False, locks=False, quiet=False, fault=None):
        self.rec, self.lane = rec, lane
        self.reads, self.locks, self.quiet, self.fault = reads, locks, quiet, fault
        self.fds = {}                 # fd -> path, for fds the store opened through us
        self.lockfds = {}             # lock fd -> path
        self.dead = False
        self.torn = None              # "torn_data" / "torn_pointer": label the partial write
        self._written = set()         # paths written at least once (torn detection)
        self._dirflush = 0            # >0 while fsync_dir/fsync_path flush internally
        self._armed = False
        self.injected = 0
        self._last_rename = None      # basename of the last rename destination
        self._last_jrec = None        # type of the last journal record written

    # -- plumbing ------------------------------------------------------------
    def _alive(self):
        if self.dead:
            raise _Crash()

    def _ev(self, op, path, label, detail=None, commit=False, update=None, kind="syscall", ok=None,
            native=None):
        with self.rec.lock:
            if update:
                update()
            if not self.quiet:
                self.rec.emit(kind, self.lane, label, detail, op=op,
                              path=os.path.basename(path.rstrip("/\\")) if path else None,
                              commit=commit, ok=ok, native=native)

    def _recovery(self):
        return self.lane == "recovery"

    def _pointer_version(self, ddir):
        try:
            return ad.decode_pointer(_PLAIN.read(ddir + "/CURRENT"))[0]
        except (OSError, ad.CorruptVersion):
            return None

    # -- directory operations ------------------------------------------------
    def mkdir(self, p):
        self._alive()
        super().mkdir(p)
        n = os.path.basename(p)
        self._ev("mkdir", p, "mkdir %s" % n,
                 "Creates the %r directory. Its entry in the parent is not durable until the parent "
                 "directory is fsynced, which the store does next." % n,
                 update=lambda: self.rec.d_set(p, data=True, entry=False))

    def create(self, p):
        self._alive()
        fd = super().create(p)
        self.fds[fd] = p
        n = os.path.basename(p)
        if n.startswith(".CURRENT."):
            d = ("Recovery prepares a replacement CURRENT pointer in a fresh temp file."
                 if self._recovery() else
                 "The new one-line pointer is prepared in its own temp file (O_CREAT|O_EXCL), "
                 "never by editing CURRENT in place.")
        elif n.startswith("journal.wal."):
            d = ("Checkpoint begins: a brand-new journal (fresh random salt) is built in a temp file."
                 if self._recovery() else "A fresh journal is built in a temp file.")
        elif _vnum(n) is not None:
            d = ("Copy-on-write: v%d goes into a brand-new temp file created with O_CREAT|O_EXCL in "
                 "the same directory. The current version is never modified in place." % _vnum(n))
        else:
            d = None
        self._ev("create", p, "create %s" % short(n), d,
                 update=lambda: self.rec.d_set(p, data=True, entry=False))
        return fd

    def open_append(self, p):
        self._alive()
        fd = super().open_append(p)
        self.fds[fd] = p
        return fd

    def open_trunc(self, p):                 # naive baseline only
        self._alive()
        fd = super().open_trunc(p)
        self.fds[fd] = p
        n = os.path.basename(p)
        self._ev("create", p, "open %s O_TRUNC" % n,
                 "The naive writer opens the ONLY copy of the document with O_TRUNC: the old "
                 "content is thrown away before a single new byte exists.",
                 update=lambda: self.rec.d_set(p, data=False),
                 native=platform_info.info()["native"]["open_trunc"])
        return fd

    def close(self, fd):
        self.fds.pop(fd, None)
        super().close(fd)

    # -- data ------------------------------------------------------------------
    def write(self, fd, data):
        self._alive()
        super().write(fd, data)
        p = self.fds.get(fd)
        if p is None:
            return
        n = os.path.basename(p)
        first = p not in self._written
        self._written.add(p)
        size = len(data)
        torn = first and (
            (self.torn == "torn_data" and _vnum(n) is not None and n.endswith(".tmp")) or
            (self.torn == "torn_pointer" and n.startswith(".CURRENT.")) or
            (self.torn == "naive_torn"))
        if n == "journal.wal":
            rec_t = None
            try:
                rec_t = json.loads(bytes(data[8:]))["t"]
            except (ValueError, KeyError, TypeError):
                pass
            self._last_jrec = rec_t
            label = "append %s record -> journal.wal" % (rec_t or "?")
            d = {
                "BEGIN": "Write-ahead intent: the BEGIN record (old -> new version, SHA-256 of the new "
                         "body, salted CRC) is appended to the journal BEFORE any data is written, so "
                         "recovery can recognise this save if we die mid-way.",
                "COMMIT": "Completion record. The save is already committed by the CURRENT rename; "
                          "COMMIT only tells recovery that this transaction finished cleanly.",
                "ABORT": "Abort record: the save failed before its commit point and was cleaned up.",
            }.get(rec_t)
        elif torn:
            label = "write %d B -> %s (TORN: half, then crash)" % (size, short(n))
            d = ("Only half of the bytes reach the file before the process dies - a torn write. "
                 "The store fsyncs them so the torn bytes really are on disk for the demo.")
        elif n.startswith(".CURRENT."):
            label = "write %d B -> %s" % (size, short(n))
            d = ("Pointer text 'ADOC-CURRENT <format> <version> <sha256> <crc32>': self-validating, "
                 "so a torn pointer can never be mistaken for a good one. Not durable yet.")
        elif n.startswith("journal.wal."):
            label = "write %d B journal header -> %s" % (size, short(n))
            d = None
        elif _vnum(n) is not None:
            label = "write %d B -> %s" % (size, short(n))
            d = ("The version frame (header + SHA-256 + body) is written. It sits only in the kernel "
                 "page cache until fsync: dataDurable=false, a power cut could lose or tear it.")
        else:
            label = "write %d B -> %s" % (size, n)
            d = ("The new bytes overwrite the only copy in place - and live only in the page cache "
                 "until an fsync." if self.torn and self.torn.startswith("naive") else None)
        self._ev("write", p, label, d, update=lambda: self.rec.d_set(p, data=False))

    def fsync(self, fd):
        self._alive()
        super().fsync(fd)
        if self._dirflush:                # inner flush of fsync_dir / fsync_path
            return
        p = self.fds.get(fd)
        if p is None:                     # an fd we did not open: not recorded
            return
        n = os.path.basename(p)
        if n == "journal.wal":
            d = {"BEGIN": "fsync makes the BEGIN record durable: from now on recovery will know this "
                          "save was in flight.",
                 "COMMIT": "fsync makes the COMMIT record durable."}.get(self._last_jrec)
        elif n.startswith(".CURRENT."):
            d = "The pointer bytes are flushed BEFORE the rename that will publish them."
        elif _vnum(n) is not None and n.endswith(".tmp"):
            d = ("fsync BEFORE rename: the data must be on stable storage before any name points at "
                 "it (the ext4 zero-length-file lesson, 2009). dataDurable turns true.")
        else:
            d = None
        self._ev("fsync", p, "fsync %s" % short(n), d, update=lambda: self.rec.d_set(p, data=True))

    def fsync_path(self, p):
        self._alive()
        self._dirflush += 1
        try:
            super().fsync_path(p)
        finally:
            self._dirflush -= 1
        n = os.path.basename(p)
        d = ("Recovery flushes the file its decision rests on before checkpointing the journal, so a "
             "crash right after recovery finds exactly the same answer." if self._recovery() else None)
        self._ev("fsync", p, "fsync %s" % short(n), d, update=lambda: self.rec.d_set(p, data=True))

    def fsync_dir(self, p):
        self._alive()
        n = os.path.basename(p.rstrip("/\\"))
        if self.fault == "eio_after_commit" and self._armed:
            self._armed = False
            self.injected += 1
            self._ev("fsync_dir", p, "fsync dir %s -> EIO (injected)" % n,
                     "FAULT INJECTED: the directory flush that should make the CURRENT rename durable "
                     "fails with EIO. The rename already happened in memory, so readers see v2 - but "
                     "nobody knows whether it reached the disk. Retrying fsync is unsafe (fsyncgate).",
                     kind="fault", ok=False)
            raise ad.FsyncError("fsync failed: [Errno 5] Input/output error (injected)")
        self._dirflush += 1               # RealFS flushes the directory fd via self.fsync()
        try:
            super().fsync_dir(p)
        finally:
            self._dirflush -= 1
        last = self._last_rename
        if last == "CURRENT" and not self._recovery():
            d = ("DURABLE COMMIT: flushing the directory makes the CURRENT swap survive power loss. "
                 "Only now does put() return success to the caller.")
        elif last == "CURRENT":
            d = "The rewritten CURRENT pointer is now durable."
        elif last and ad._is_vname(last):
            d = ("fsync(dir) makes the new name %s durable (PostgreSQL durable_rename lesson). The "
                 "version is safely on disk but is NOT the current version yet." % last)
        elif last == "journal.wal":
            d = ("Checkpoint durable: the fresh journal has replaced the old one. Recovery is complete."
                 if self._recovery() else "The new journal is durable.")
        elif self._recovery():
            d = "Directory flush: the deletions and renames recovery made are now durable."
        else:
            d = "Directory flush: earlier entry changes (creates, renames, unlinks) become durable."
        self._last_rename = None
        self._ev("fsync_dir", p, "fsync dir %s" % n, d, update=lambda: self.rec.d_dirsync(p))

    def rename(self, a, b):
        self._alive()
        super().rename(a, b)
        na, nb = os.path.basename(a), os.path.basename(b)
        self._last_rename = nb
        commit = nb == "CURRENT" and not self._recovery()
        if commit:
            d = ("COMMIT POINT. rename() atomically replaces CURRENT: before this instruction every "
                 "reader and every recovery sees the old version, after it the new one. There is no "
                 "state in between. (Durable only after the directory fsync that follows.)")
            if self.fault == "eio_after_commit":
                self._armed = True
        elif nb == "CURRENT":
            d = ("Recovery atomically installs a CURRENT that names the version it accepted (the "
                 "newest one whose SHA-256 verifies), with the same temp + fsync + rename protocol.")
        elif ad._is_vname(nb):
            d = ("rename(2) atomically gives the finished file its final name %s. It is NOT current: "
                 "CURRENT still names the old version, so readers and recovery ignore it." % nb)
        elif nb == "journal.wal":
            d = ("Checkpoint: the fresh journal (new random salt) atomically replaces the old one; "
                 "stale records can never validate again (the SQLite WAL salt idea).")
        else:
            d = None
        self._ev("rename", b, "rename %s -> %s" % (short(na), nb), d, commit=commit,
                 update=lambda: self.rec.d_rename(a, b))

    def unlink(self, p):
        self._alive()
        super().unlink(p)
        n = os.path.basename(p)
        v = _vnum(n) if ad._is_vname(n) else None
        if n.endswith(".tmp"):
            d = ("Recovery deletes a leftover temp file: only renames commit anything, so a .tmp was "
                 "never committed by construction." if self._recovery() else "Temp file removed.")
            label = "unlink %s" % short(n)
        elif v is not None:
            cur = self._pointer_version(os.path.dirname(p))
            if cur is not None and v <= cur - ad.KEEP_VERSIONS:
                d = ("Garbage collection after a durable commit: only the newest KEEP_VERSIONS=%d "
                     "versions are kept, so v%d is deleted." % (ad.KEEP_VERSIONS, v))
                label = "unlink %s (GC)" % n
            elif self._recovery():
                d = ("Rollback: v%d is above the version recovery accepted, so it was never committed "
                     "and is deleted. The committed version is untouched." % v)
                label = "unlink %s (roll back)" % n
            else:
                d, label = None, "unlink %s" % n
        elif n == "CURRENT":
            d, label = "Recovery removes a CURRENT that names nothing valid.", "unlink CURRENT"
        else:
            d, label = None, "unlink %s" % n
        self._ev("unlink", p, label, d, update=lambda: self.rec.d_unlink(p))

    def truncate(self, p, n):
        self._alive()
        super().truncate(p, n)

    # -- reads -------------------------------------------------------------------
    def read(self, p):
        if not self.reads:
            return super().read(p)
        n = os.path.basename(p)
        try:
            raw = super().read(p)
        except FileNotFoundError:
            d = ("The version named by CURRENT a moment ago is gone: a writer committed newer "
                 "versions and garbage collection deleted it. The reader must re-read CURRENT."
                 if ad._is_vname(n) else None)
            self._ev("read", p, "read %s -> ENOENT" % n, d, ok=False)
            raise
        label, d, ok = "read %s" % short(n), None, None
        if n == "CURRENT":
            try:
                v, _ = ad.decode_pointer(raw)
                label, ok = "read CURRENT -> v%d" % v, True
                d = ("CURRENT parses and its CRC matches: it names v%d." % v if self._recovery()
                     else "Lock-free read: the pointer says the committed version is v%d." % v)
            except ad.CorruptVersion as e:
                label, ok = "read CURRENT -> torn/corrupt", False
                d = ("CURRENT does not verify (%s). Rule: fall back to the newest version whose "
                     "SHA-256 verifies." % e)
        elif ad._is_vname(n):
            try:
                ad.decode_version(self.rec.doc, int(n[1:7]), raw)
                label, ok = "read %s (SHA-256 ok)" % n, True
                d = "Header and SHA-256 verify: %s is a whole version." % n
            except ad.CorruptVersion as e:
                label, ok = "read %s (INVALID)" % n, False
                d = "%s does not verify (%s): it is not a whole version and can never be served." % (n, e)
        elif n == "journal.wal":
            _, recs, _, status = ad.scan_journal(raw)
            done = {r["tx"] for r in recs if r["t"] != "BEGIN"}
            open_tx = [r for r in recs if r["t"] == "BEGIN" and r["tx"] not in done]
            label = "read journal.wal (%d records, %s)" % (len(recs), status)
            d = ("Recovery scans the journal: %d BEGIN without COMMIT = %s. The journal is used for "
                 "cleanup and reporting, never as the source of truth."
                 % (len(open_tx), "a save that was in flight" if open_tx else "nothing in flight"))
        self._ev("read", p, label, d, ok=ok)
        return raw

    # -- locks -------------------------------------------------------------------
    def lock(self, p, exclusive):
        self._alive()
        fd = super().lock(p, exclusive)
        if self.locks and os.path.basename(p) == ".lock":
            self.lockfds[fd] = p
            self._ev("lock", p, "lock %s/.lock (exclusive)" % os.path.basename(os.path.dirname(p)),
                     "Per-document exclusive lock (LockFileEx / flock): only one writer at a time "
                     "may check the version and commit.")
        return fd

    def unlock(self, fd):
        p = self.lockfds.pop(fd, None)
        super().unlock(fd)                # also after a crash: the kernel releases locks
        if p is not None and not self.dead:
            self._ev("unlock", p, "unlock %s/.lock" % os.path.basename(os.path.dirname(p)))

    # -- crash -------------------------------------------------------------------
    def crash(self):
        self.dead = True
        raise _Crash()


# ----------------------------------------------------------------- misc
def fingerprint(root):
    """SHA-256 (16 hex) over relative names + bytes of every file under root,
    excluding journal.wal (fresh salt at each checkpoint) and *.lock files."""
    h = hashlib.sha256()
    for dp, dns, fns in os.walk(root):
        dns.sort()
        for fn in sorted(fns):
            rel = os.path.relpath(os.path.join(dp, fn), root).replace("\\", "/")
            if rel == "journal.wal" or fn.endswith(".lock"):
                continue
            with open(os.path.join(dp, fn), "rb") as f:
                h.update(rel.encode() + b"\0" + f.read() + b"\1")
    return h.hexdigest()[:16]
