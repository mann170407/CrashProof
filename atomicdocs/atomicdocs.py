#!/usr/bin/env python3
"""
AtomicDocs v2 - a crash-consistent document store.
Kernel Ops hackathon, problem osh021 (Crash-Consistent Document Storage).

GUARANTEE
    If the process or the machine dies at ANY instant during put(), the
    document is afterwards readable as exactly the previous valid version or
    exactly the new valid version - never a mixture. Once put() has returned,
    the new version survives power loss (durability), provided the disk
    honours flush requests.

DESIGN (the same commit protocol LevelDB / RocksDB use for their CURRENT file)
    1. Copy-on-write: the new content goes into a fresh immutable version file
       (unique O_EXCL temp name -> fsync -> rename -> fsync directory).
    2. Commit point: a one-line CURRENT pointer, written to a temp file,
       fsynced, and renamed over the old pointer (rename(2) is atomic), then
       the directory is fsynced. put() returns only after this.
    3. Write-ahead intent journal (BEGIN / COMMIT / ABORT) with salted CRC32
       records, used by recovery for cleanup, audit and in-flight detection.
    4. Everything is self-validating: version files carry a SHA-256 over
       header + body; CURRENT carries its own CRC and its target's SHA-256.
       Recovery trusts only bytes that verify (OptFS-style: checksums, not
       write ordering, are what guarantee atomicity).

LESSONS APPLIED FROM REAL FAILURES (see design document for sources)
    - fsync before rename          (ext4 zero-length files, 2009)
    - fsync the directory          (PostgreSQL durable_rename, 2015)
    - fsync error is fatal, never retried  (PostgreSQL "fsyncgate", 2018)
    - F_FULLFSYNC on macOS         (LevelDB SyncFd, git wrapper.c)
    - O_EXCL unique temp names in the same directory  (git lockfile, renameio)
    - salted record CRCs so stale journal bytes never validate (SQLite WAL)
    - torn tail truncated, mid-log corruption reported (LevelDB/RocksDB/etcd)

All I/O goes through an FS object, so the identical store code runs on the
real file system (RealFS) and on the power-loss simulator (simfs.SimFS).
"""
import errno
import hashlib
import json
import os
import struct
import sys
import time
import zlib

try:
    import fcntl                        # Linux / macOS
except ImportError:                     # Windows: see the _win layer below
    fcntl = None

WINDOWS = os.name == "nt"
O_BINARY = getattr(os, "O_BINARY", 0)  # Windows text mode would turn \n into \r\n

FORMAT = 2
KEEP_VERSIONS = 3
VERIFY = True          # set False only by the mutation test (negative control)
SECTOR = 512
READ_RETRIES = 5       # bounded retries when GC removes a version mid-read

# ----------------------------------------------------------------- errors
class StoreError(Exception):
    pass


class FsyncError(StoreError):
    """A flush failed. The outcome of the operation is unknown, the page
    cache can no longer be trusted (fsyncgate), so the store is poisoned
    until it is reopened and recovery reads the truth back from disk."""


class CorruptVersion(StoreError):
    pass


class Poisoned(StoreError):
    pass


class CommitUnknown(StoreError):
    """An error after the commit point. The new version may or may not be the
    current one; the store is poisoned and recovery decides on reopen."""


class CommitUnknownFsync(CommitUnknown, FsyncError):
    """A flush failed after the CURRENT rename (e.g. directory fsync EIO).
    Both an fsync failure and an outcome-unknown commit."""


class Conflict(StoreError):
    """Compare-and-swap failed: someone committed after the caller's read."""

    def __init__(self, doc, expected, actual):
        super().__init__("conflict on %r: expected version %d, current is %d"
                         % (doc, expected, actual))
        self.doc, self.expected, self.actual = doc, expected, actual


class ReadContention(StoreError):
    """get() lost the race with writers + GC READ_RETRIES times in a row."""


# ----------------------------------------------------------------- crash hooks
# Process-crash testing: ATOMICDOCS_CRASH_AT=<point> makes the process die
# with os._exit at that point (no flush, no cleanup, like kill -9).
# Tests can also set store.crash_at directly.
CRASH_POINTS = [
    "after_begin", "torn_data", "before_data_fsync", "after_data_rename",
    "torn_pointer", "before_pointer_rename", "after_pointer_rename", "after_commit",
]
# Crash points inside recovery itself (interrupted-recovery testing).
RECOVERY_CRASH_POINTS = [
    "recover_after_tmp_unlink", "recover_after_version_unlink",
    "recover_after_pointer_rewrite", "recover_before_checkpoint",
]
_CRASH_AT = os.environ.get("ATOMICDOCS_CRASH_AT")


# ----------------------------------------------------------------- Windows layer
# Windows has no flock(2), no fsync of a directory fd, and by default refuses
# to rename over or delete a file another process has open. These helpers give
# the same guarantees through the Win32 API (LockFileEx, MoveFileExW with
# MOVEFILE_WRITE_THROUGH, FlushFileBuffers on a directory handle, and
# FILE_SHARE_DELETE opens for lock-free readers).
if WINDOWS:
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _OVERLAPPED(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
                    ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                    ("hEvent", ctypes.c_void_p)]

    _k32.CreateFileW.restype = ctypes.c_void_p
    _k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                 wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    _k32.CloseHandle.argtypes = [ctypes.c_void_p]
    _k32.FlushFileBuffers.argtypes = [ctypes.c_void_p]
    _k32.MoveFileExW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    _k32.LockFileEx.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                wintypes.DWORD, ctypes.POINTER(_OVERLAPPED)]
    _k32.UnlockFileEx.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.POINTER(_OVERLAPPED)]
    _INVALID = ctypes.c_void_p(-1).value
    _GENERIC_READ, _GENERIC_WRITE = 0x80000000, 0x40000000
    _SHARE_ALL = 0x1 | 0x2 | 0x4                 # read | write | delete
    _OPEN_EXISTING, _BACKUP_SEMANTICS = 3, 0x02000000
    _SHARING_ERRORS = (5, 32, 33)                # access denied, sharing/lock violation

    def _win_err(path=None):
        e = ctypes.WinError(ctypes.get_last_error())
        e.filename = path
        return e

    def _win_retry(fn, *args):
        """Rename/unlink can transiently fail while a lock-free reader holds the
        file open. These calls are not flushes, so retrying them is safe."""
        for i in range(200):
            try:
                return fn(*args)
            except PermissionError as e:
                if getattr(e, "winerror", None) not in _SHARING_ERRORS or i == 199:
                    raise
                time.sleep(0.005)

    def _win_open_read(p):
        h = _k32.CreateFileW(p, _GENERIC_READ, _SHARE_ALL, None, _OPEN_EXISTING, 0x80, None)
        if h in (None, _INVALID):
            raise _win_err(p)
        fd = msvcrt.open_osfhandle(h, os.O_RDONLY | O_BINARY)
        return os.fdopen(fd, "rb")

    def _win_move(a, b):
        if not _k32.MoveFileExW(a, b, 0x1 | 0x8):  # REPLACE_EXISTING | WRITE_THROUGH
            raise _win_err(a)

    def _win_open_dir(p):
        """CRT fd on a directory handle; os.fsync on it is FlushFileBuffers."""
        h = _k32.CreateFileW(p, _GENERIC_READ | _GENERIC_WRITE, _SHARE_ALL, None,
                             _OPEN_EXISTING, _BACKUP_SEMANTICS, None)
        if h in (None, _INVALID):
            raise _win_err(p)
        try:
            return msvcrt.open_osfhandle(h, os.O_RDONLY | O_BINARY)
        except BaseException:
            _k32.CloseHandle(h)
            raise

    def _win_lock(fd, exclusive):
        ov = _OVERLAPPED()
        flags = 0x2 if exclusive else 0x0        # LOCKFILE_EXCLUSIVE_LOCK; blocking
        if not _k32.LockFileEx(msvcrt.get_osfhandle(fd), flags, 0, 1, 0, ctypes.byref(ov)):
            raise _win_err()

    def _win_unlock(fd):
        ov = _OVERLAPPED()
        _k32.UnlockFileEx(msvcrt.get_osfhandle(fd), 0, 1, 0, ctypes.byref(ov))


# ----------------------------------------------------------------- real FS
class RealFS:
    """Thin, explicit wrapper over the POSIX calls the store needs
    (Win32 equivalents on Windows)."""
    name = "real"

    def exists(self, p): return os.path.exists(p)
    def isdir(self, p): return os.path.isdir(p)
    def listdir(self, p): return sorted(os.listdir(p))
    def getsize(self, p): return os.path.getsize(p)

    def _open_read(self, p):
        return _win_open_read(p) if WINDOWS else open(p, "rb")

    def read(self, p):
        with self._open_read(p) as f:
            return f.read()

    def read_head(self, p, n):
        with self._open_read(p) as f:
            return f.read(n)

    def mkdir(self, p):
        os.mkdir(p, 0o755)

    def create(self, p):
        # O_EXCL: never clobber another writer's temp file (git lockfile rule)
        return os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL | O_BINARY, 0o644)

    def open_append(self, p):
        return os.open(p, os.O_WRONLY | os.O_APPEND | O_BINARY)

    def open_trunc(self, p):           # used only by the naive baseline
        return os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | O_BINARY, 0o644)

    def write(self, fd, data):
        view = memoryview(data)
        while view:                   # short writes are legal; loop until done
            n = os.write(fd, view)
            if n <= 0:
                raise OSError(errno.EIO, "write returned %d" % n)
            view = view[n:]

    def fsync(self, fd):
        try:
            if fcntl is not None and hasattr(fcntl, "F_FULLFSYNC"):   # macOS: fsync alone does
                try:                                   # not flush the drive cache
                    fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
                    return
                except OSError as e:                   # fall back ONLY if unsupported;
                    if e.errno not in (errno.ENOTSUP, errno.EINVAL, errno.ENOTTY):
                        raise                          # EIO etc. must not be retried
            os.fsync(fd)
        except OSError as e:
            raise FsyncError("fsync failed: %s" % e) from e

    def fsync_path(self, p):
        # Windows FlushFileBuffers needs a writable handle
        fd = os.open(p, (os.O_RDWR | O_BINARY) if WINDOWS else os.O_RDONLY)
        try:
            self.fsync(fd)
        finally:
            os.close(fd)

    def fsync_dir(self, p):
        # The flush always goes through self.fsync (FlushFileBuffers on a
        # directory handle on Windows), so the never-retry rule and fault
        # injection cover directory flushes on every platform.
        if WINDOWS:
            try:
                fd = _win_open_dir(p)
            except OSError as e:
                raise FsyncError("directory flush failed: %s" % e) from e
        else:
            fd = os.open(p, os.O_RDONLY)
        try:
            self.fsync(fd)
        finally:
            os.close(fd)

    def close(self, fd): os.close(fd)

    def rename(self, a, b):
        if WINDOWS:
            _win_retry(_win_move, a, b)
        else:
            os.replace(a, b)

    def unlink(self, p):
        if WINDOWS:
            _win_retry(os.unlink, p)
        else:
            os.unlink(p)

    def truncate(self, p, n):
        fd = os.open(p, os.O_WRONLY | O_BINARY)
        try:
            os.ftruncate(fd, n)
            self.fsync(fd)
        finally:
            os.close(fd)

    def lock(self, p, exclusive):
        fd = os.open(p, os.O_RDWR | os.O_CREAT | O_BINARY, 0o644)
        try:
            if WINDOWS:
                _win_lock(fd, exclusive)
            else:
                fcntl.flock(fd, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        except BaseException:
            os.close(fd)
            raise
        return fd

    def unlock(self, fd):
        if WINDOWS:
            _win_unlock(fd)
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    def crash(self):
        os._exit(137)


# ----------------------------------------------------------------- formats
# Version file header: magic, format, flags, version, body length,
# doc-id hash (16 B), SHA-256 over (header with zeroed hash field + body)
VHDR = struct.Struct(">4sHHIQ16s32s")
VMAGIC = b"ADOC"


def _doc_tag(doc):
    return hashlib.sha256(doc.encode()).digest()[:16]


def encode_version(doc, version, body):
    pre = VHDR.pack(VMAGIC, FORMAT, 0, version, len(body), _doc_tag(doc), b"\0" * 32)
    digest = hashlib.sha256(pre + body).digest()
    return VHDR.pack(VMAGIC, FORMAT, 0, version, len(body), _doc_tag(doc), digest) + body, digest.hex()


def decode_version(doc, version, raw, expect_sha=None):
    if len(raw) < VHDR.size:
        raise CorruptVersion("short header")
    magic, fmt, flags, ver, n, tag, digest = VHDR.unpack(raw[:VHDR.size])
    body = raw[VHDR.size:]
    if magic != VMAGIC or fmt != FORMAT:
        raise CorruptVersion("bad magic/format")
    if ver != version or tag != _doc_tag(doc):
        raise CorruptVersion("file belongs to another version/document")
    if not VERIFY:
        return body, digest.hex()
    if len(body) != n:
        raise CorruptVersion("length mismatch (torn or truncated)")
    pre = VHDR.pack(magic, fmt, flags, ver, n, tag, b"\0" * 32)
    if hashlib.sha256(pre + body).digest() != digest:
        raise CorruptVersion("SHA-256 mismatch")
    if expect_sha and digest.hex() != expect_sha:
        raise CorruptVersion("pointer names a different version")
    return body, digest.hex()


def encode_pointer(version, sha):
    line = "ADOC-CURRENT %d %d %s" % (FORMAT, version, sha)
    return ("%s %08x\n" % (line, zlib.crc32(line.encode()))).encode()


def decode_pointer(raw):
    """Strict parse. Returns (version, sha) or raises CorruptVersion."""
    try:
        txt = raw.decode("ascii")
    except UnicodeDecodeError:
        raise CorruptVersion("CURRENT not ascii")
    if not txt.endswith("\n") or txt.count("\n") != 1:
        raise CorruptVersion("CURRENT torn (no single trailing newline)")
    parts = txt.split()
    if len(parts) != 5 or parts[0] != "ADOC-CURRENT":
        raise CorruptVersion("CURRENT malformed")
    line = " ".join(parts[:4])
    if "%08x" % zlib.crc32(line.encode()) != parts[4]:
        raise CorruptVersion("CURRENT CRC mismatch")
    return int(parts[2]), parts[3]


# Journal: 16-byte header (magic, format, salt) then records:
#   len(4) | crc32(4) | payload ; crc = crc32(salt || len || payload)
# A fresh random salt is chosen at every checkpoint, so bytes left over from
# an earlier cycle can never validate (the SQLite WAL salt idea).
JHDR = struct.Struct(">4sIQ")
JMAGIC = b"AJNL"


def _rec_crc(salt, payload):
    return zlib.crc32(struct.pack(">QI", salt, len(payload)) + payload)


def encode_record(salt, rec):
    payload = json.dumps(rec, separators=(",", ":"), sort_keys=True).encode()
    return struct.pack(">II", len(payload), _rec_crc(salt, payload)) + payload


def scan_journal(raw):
    """Returns (salt, records, valid_end, status) where status is
    'ok', 'torn_tail' (harmless crash artefact) or 'corrupt' (media error)."""
    if len(raw) < JHDR.size:
        return None, [], 0, "no_header"
    magic, fmt, salt = JHDR.unpack(raw[:JHDR.size])
    if magic != JMAGIC or fmt != FORMAT:
        return None, [], 0, "no_header"
    off, recs = JHDR.size, []
    while off < len(raw):
        if off + 8 > len(raw):
            return salt, recs, off, "torn_tail"
        n, crc = struct.unpack(">II", raw[off:off + 8])
        body = raw[off + 8: off + 8 + n]
        if len(body) < n or _rec_crc(salt, body) != crc:
            rest = raw[off:]
            # A bad record at the very end is a torn append; a bad record with
            # more than a sector of non-zero data behind it is real corruption.
            tail_after = rest[8 + n:] if len(body) == n else b""
            torn = len(body) < n or not tail_after.strip(b"\0") or len(rest) <= SECTOR
            return salt, recs, off, "torn_tail" if torn else "corrupt"
        recs.append(json.loads(body))
        off += 8 + n
    return salt, recs, off, "ok"


def _vname(v):
    return "v%06d.dat" % v


def _is_vname(n):
    return len(n) == 11 and n.startswith("v") and n.endswith(".dat") and n[1:7].isdigit()


# ----------------------------------------------------------------- store
class AtomicDocs:
    def __init__(self, root, fs=None, auto_recover=True, skip=()):
        self.fs = fs or RealFS()
        self.root = root.rstrip("/")
        self.docs = self.root + "/docs"
        self.jpath = self.root + "/journal.wal"
        self.store_lock = self.root + "/.store.lock"
        self.skip = set(skip)          # mutation testing: fsyncs to omit
        self.crash_at = _CRASH_AT      # crash-injection point (tests may override)
        self.read_hook = None          # called between CURRENT read and version open
        self.poisoned = False
        self.last_recovery = None
        self.tx_counter = 0
        self._init_layout()
        if auto_recover:
            self.last_recovery = self.recover()

    # -- helpers ---------------------------------------------------------
    def _crash(self, point, fd=None, partial=None):
        if self.crash_at != point:
            return
        if fd is not None and partial is not None:
            self.fs.write(fd, partial)
            self.fs.fsync(fd)        # make the torn bytes really reach disk
        self.fs.crash()

    def _sync(self, fd, what):
        if what not in self.skip:
            self.fs.fsync(fd)

    def _dsync(self, path, what):
        if what not in self.skip:
            self.fs.fsync_dir(path)

    def _mkdir_durable(self, path):
        if not self.fs.isdir(path):
            try:
                self.fs.mkdir(path)
            except FileExistsError:                       # another writer won the race
                return
            self.fs.fsync_dir(os.path.dirname(path) or ".")   # new entry durable

    def _init_layout(self):
        fs = self.fs
        if not fs.isdir(self.root):
            if isinstance(fs, RealFS):
                os.makedirs(os.path.dirname(os.path.abspath(self.root)), exist_ok=True)
            self._mkdir_durable(self.root)
        self._mkdir_durable(self.docs)
        if not fs.exists(self.jpath):
            self._new_journal()

    def _new_journal(self):
        """Create/replace the journal atomically with a fresh salt."""
        salt = int.from_bytes(os.urandom(8), "big")
        tmp = "%s.%d.%s.tmp" % (self.jpath, os.getpid(), os.urandom(4).hex())
        fd = self.fs.create(tmp)
        try:
            self.fs.write(fd, JHDR.pack(JMAGIC, FORMAT, salt))
            self.fs.fsync(fd)
        finally:
            self.fs.close(fd)
        self.fs.rename(tmp, self.jpath)
        self.fs.fsync_dir(self.root)
        return salt

    def _journal_salt(self):
        raw = self.fs.read_head(self.jpath, JHDR.size)
        return JHDR.unpack(raw)[2]

    def _log(self, rec):
        rec["ts"] = time.time_ns()
        fd = self.fs.open_append(self.jpath)
        lk = self.fs.lock(self.jpath + ".lock", True)
        try:
            self.fs.write(fd, encode_record(self._journal_salt(), rec))
            self._sync(fd, "journal_fsync")
        finally:
            self.fs.unlock(lk)
            self.fs.close(fd)

    def _ddir(self, doc):
        if not doc or "/" in doc or doc.startswith(".") or len(doc) > 200:
            raise ValueError("bad document id")
        return self.docs + "/" + doc

    def _versions(self, ddir):
        return sorted(int(n[1:7]) for n in self.fs.listdir(ddir) if _is_vname(n))

    def _read_pointer(self, ddir):
        p = ddir + "/CURRENT"
        if not self.fs.exists(p):
            return 0, None
        return decode_pointer(self.fs.read(p))

    def _load(self, doc, ddir, v, sha=None):
        return decode_version(doc, v, self.fs.read(ddir + "/" + _vname(v)), sha)

    def _write_pointer(self, ddir, version, sha, crash_hooks=False):
        tmp = "%s/.CURRENT.%d.%s.tmp" % (ddir, os.getpid(), os.urandom(4).hex())
        data = encode_pointer(version, sha)
        fd = self.fs.create(tmp)
        try:
            if crash_hooks:
                self._crash("torn_pointer", fd, data[: len(data) // 2])
            self.fs.write(fd, data)
            self._sync(fd, "ptr_fsync")
        finally:
            self.fs.close(fd)
        if crash_hooks:
            self._crash("before_pointer_rename")
        self.fs.rename(tmp, ddir + "/CURRENT")          # <-- ATOMIC COMMIT POINT
        self._ptr_renamed = True
        if crash_hooks:
            self._crash("after_pointer_rename")
        self._dsync(ddir, "ptr_dirsync")                 # <-- DURABLE COMMIT

    # -- public API -------------------------------------------------------
    def put(self, doc, data: bytes, expect=None):
        """Atomically and durably replace `doc` with `data`.
        Returns the new version number once the change is durable.
        expect=v makes it a compare-and-swap: raises Conflict unless the
        committed version is still v (0 = document must not exist yet)."""
        if self.poisoned:
            raise Poisoned("an earlier fsync failed; reopen the store to recover")
        fs = self.fs
        ddir = self._ddir(doc)
        slock = fs.lock(self.store_lock, False)      # writers share; recovery excludes
        try:
            self._mkdir_durable(ddir)
            dlock = fs.lock(ddir + "/.lock", True)   # one writer per document
            try:
                return self._put_locked(doc, ddir, data, expect)
            finally:
                fs.unlock(dlock)
        except FsyncError:
            self.poisoned = True                     # never retry fsync
            raise
        finally:
            fs.unlock(slock)

    def _put_locked(self, doc, ddir, data, expect=None):
        fs = self.fs
        old, _ = self._read_pointer(ddir)
        # CAS is checked under the per-document lock, so the check and the
        # commit cannot be interleaved with another writer (no lost updates).
        if expect is not None and old != expect:
            raise Conflict(doc, expect, old)
        new = max([old] + self._versions(ddir)) + 1
        frame, sha = encode_version(doc, new, data)
        self.tx_counter += 1
        tx = "%d-%d-%s" % (os.getpid(), self.tx_counter, os.urandom(3).hex())

        # 1. write-ahead intent
        try:
            self._log({"t": "BEGIN", "tx": tx, "doc": doc, "old": old, "new": new, "sha": sha})
        except FsyncError:
            raise
        except OSError as e:
            raise StoreError("write aborted before any data, document unchanged: %s" % e) from e
        self._crash("after_begin")

        # 2. copy-on-write data: unique temp in the SAME directory (no EXDEV)
        tmp = "%s/.%s.%d.%s.tmp" % (ddir, _vname(new), os.getpid(), os.urandom(4).hex())
        fd = fs.create(tmp)
        renamed = False
        self._ptr_renamed = False
        try:
            try:
                self._crash("torn_data", fd, frame[: max(1, len(frame) // 2)])
                fs.write(fd, frame)                  # ENOSPC / EIO surface here
                self._crash("before_data_fsync")
                self._sync(fd, "data_fsync")
            finally:
                fs.close(fd)
            fs.rename(tmp, ddir + "/" + _vname(new))
            renamed = True
            self._dsync(ddir, "data_dirsync")
            self._crash("after_data_rename")

            # 3. commit: atomic pointer swap + directory fsync
            self._write_pointer(ddir, new, sha, crash_hooks=True)
        except FsyncError as e:
            if self._ptr_renamed:                    # e.g. directory fsync EIO after the
                raise CommitUnknownFsync(            # rename: CURRENT may already name v-new
                    "flush failed after the commit point, outcome unknown; "
                    "reopen to let recovery decide: %s" % e) from e
            raise                                    # before commit: recovery cleans up
        except OSError as e:
            if self._ptr_renamed:                    # past the commit point: never
                self.poisoned = True                 # delete anything, let recovery decide
                raise CommitUnknown("error after commit point, outcome decided by recovery: %s" % e) from e
            try:                                     # before the commit point: clean abort
                fs.unlink(ddir + "/" + _vname(new) if renamed else tmp)
            except OSError:
                pass
            try:
                self._log({"t": "ABORT", "tx": tx, "why": errno.errorcode.get(e.errno, str(e))})
            except OSError:
                pass
            raise StoreError("write aborted, document unchanged: %s" % e) from e

        # The save is now durable. Steps 4-5 are housekeeping: if they fail the
        # save still succeeded, so return it, but stop this handle (reopen
        # runs recovery, which finishes the cleanup).
        try:
            self._log({"t": "COMMIT", "tx": tx})          # 4. completion record
            self._crash("after_commit")
            self._gc(doc, ddir, new)                       # 5. garbage collection
        except OSError:
            self.poisoned = True
        return new

    def get(self, doc):
        """Lock-free read of the committed version. Returns bytes or None."""
        ddir = self._ddir(doc)
        if not self.fs.isdir(ddir):
            return None
        for attempt in range(READ_RETRIES):
            v, sha = self._read_pointer(ddir)
            if v == 0:
                return None
            if self.read_hook:
                self.read_hook(doc, v, attempt)
            try:
                return self._load(doc, ddir, v, sha)[0]
            except FileNotFoundError:            # a writer committed and GC'd meanwhile:
                continue                         # re-read CURRENT, it names a newer version
        raise ReadContention("%r: version removed by GC %d times in a row" % (doc, READ_RETRIES))

    def history(self, doc):
        ddir = self._ddir(doc)
        cur, _ = self._read_pointer(ddir)
        out = []
        for v in self._versions(ddir):
            try:
                body, _ = self._load(doc, ddir, v)
                out.append({"version": v, "bytes": len(body), "valid": True, "current": v == cur})
            except CorruptVersion as e:
                out.append({"version": v, "valid": False, "why": str(e), "current": v == cur})
        return out

    def _gc(self, doc, ddir, current):
        """Delete versions older than the newest KEEP_VERSIONS. Runs only
        after the new CURRENT is durable, never touches current or previous."""
        doomed = [v for v in self._versions(ddir) if v <= current - KEEP_VERSIONS]
        for v in doomed:
            try:
                self.fs.unlink(ddir + "/" + _vname(v))
            except FileNotFoundError:
                pass
        if doomed:
            self.fs.fsync_dir(ddir)

    # -- recovery ------------------------------------------------------------
    def recover(self):
        """Idempotent crash recovery; safe to rerun after a crash during itself.
        Rule: the truth is CURRENT if it parses and its target verifies;
        otherwise the newest version on disk that verifies. The journal is used
        to clean up and to report, never as the source of truth."""
        lk = self.fs.lock(self.store_lock, True)    # waits for live writers
        try:
            return self._recover()
        finally:
            self.fs.unlock(lk)

    def _recover(self):
        fs, t0 = self.fs, time.perf_counter()
        rep = {"rolled_forward": [], "rolled_back": [], "tmp_removed": 0,
               "journal": "ok", "journal_bytes_dropped": 0,
               "fell_back": [], "degraded": [], "docs_checked": 0}
        if fs.exists(self.jpath):
            raw = fs.read(self.jpath)
            salt, recs, end, status = scan_journal(raw)
            rep["journal"] = status
            rep["journal_bytes_dropped"] = len(raw) - end
        else:
            recs, rep["journal"] = [], "missing"
        begun, ended = {}, set()
        for r in recs:
            if r["t"] == "BEGIN":
                begun[r["tx"]] = r
            else:
                ended.add(r["tx"])
        open_tx = {}
        for tx, r in begun.items():
            if tx not in ended:
                open_tx.setdefault(r["doc"], []).append(r)

        # leftover journal temp files from a crash inside _new_journal
        jbase = os.path.basename(self.jpath) + "."
        for n in fs.listdir(self.root):
            if n.startswith(jbase) and n.endswith(".tmp"):
                fs.unlink(self.root + "/" + n)
                rep["tmp_removed"] += 1

        for doc in fs.listdir(self.docs):
            ddir = self.docs + "/" + doc
            if not fs.isdir(ddir):
                continue
            rep["docs_checked"] += 1
            changed = False
            for n in fs.listdir(ddir):
                if n.endswith(".tmp"):
                    fs.unlink(ddir + "/" + n)
                    rep["tmp_removed"] += 1
                    changed = True
            if changed:
                self._crash("recover_after_tmp_unlink")
            versions = self._versions(ddir)
            truth, sha, reason = None, None, None
            try:
                cv, csha = self._read_pointer(ddir)
                pointer_ok = True
            except CorruptVersion as e:
                cv, csha, pointer_ok, reason = None, None, False, str(e)
            if pointer_ok and cv:
                try:
                    _, sha = self._load(doc, ddir, cv, csha)
                    truth = cv
                except (CorruptVersion, FileNotFoundError) as e:
                    reason = "CURRENT -> v%d unusable: %s" % (cv, e)
            if truth is None and (versions or not pointer_ok):
                # CURRENT missing/torn/bad: newest verifying version wins
                # (bbolt's two-meta-page rule). If CURRENT was readable, never go above it.
                limit = cv if (pointer_ok and cv) else None
                for v in reversed(versions):
                    if limit is not None and v > limit:
                        continue
                    try:
                        _, sha = self._load(doc, ddir, v)
                        truth = v
                        break
                    except CorruptVersion:
                        continue
                if truth is not None and (not pointer_ok or cv):
                    rep["fell_back"].append({"doc": doc, "to": truth, "why": reason})
                    known = {t["old"] for t in open_tx.get(doc, [])} | {t["new"] for t in open_tx.get(doc, [])}
                    if pointer_ok and cv and truth not in known:
                        rep["degraded"].append({"doc": doc, "to": truth, "why": reason})
            if truth is None and (cv or (not pointer_ok and versions)):
                # Data was committed but nothing verifies (media corruption).
                # Never delete evidence: leave the files, report loudly.
                rep["degraded"].append({"doc": doc, "to": None, "why": reason or "no version verifies"})
                if changed:
                    fs.fsync_dir(ddir)
                continue
            # delete uncommitted versions above the truth (rolled back writes)
            for v in versions:
                if truth is None or v > truth:
                    fs.unlink(ddir + "/" + _vname(v))
                    changed = True
                    self._crash("recover_after_version_unlink")
            if truth is None:
                if fs.exists(ddir + "/CURRENT"):
                    fs.unlink(ddir + "/CURRENT")
                    changed = True
            elif not (pointer_ok and cv == truth):
                self._write_pointer(ddir, truth, sha)
                self._crash("recover_after_pointer_rewrite")
            if truth is not None:
                # Make the decision durable BEFORE the journal is reset and
                # before anyone reads it: a process crash leaves CURRENT and the
                # version only in the page cache.
                fs.fsync_path(ddir + "/" + _vname(truth))
                fs.fsync_path(ddir + "/CURRENT")
            fs.fsync_dir(ddir)
            for t in open_tx.get(doc, []):
                (rep["rolled_forward"] if truth == t["new"] else rep["rolled_back"]).append(doc)
            if truth:
                self._gc(doc, ddir, truth)
        fs.fsync_dir(self.docs)
        self._crash("recover_before_checkpoint")
        # checkpoint: everything is resolved and durable; start a fresh journal
        self._new_journal()
        rep["ms"] = round((time.perf_counter() - t0) * 1000, 2)
        return rep


# ----------------------------------------------------------------- baseline
def naive_put(fs, path, data, do_fsync=False):
    """What most code does: open with O_TRUNC and write in place."""
    fd = fs.open_trunc(path)
    try:
        if _CRASH_AT == "torn_data":
            fs.write(fd, data[: len(data) // 2]); fs.fsync(fd); fs.crash()
        fs.write(fd, data)
        if do_fsync:
            fs.fsync(fd)
    finally:
        fs.close(fd)


# ----------------------------------------------------------------- CLI
def main(argv):
    if len(argv) < 3:
        print(__doc__.split("DESIGN")[0])
        print("usage: atomicdocs.py <root> put <doc> <file|-> [--expect V] | get <doc> | history <doc> | recover\n"
              "       atomicdocs.py <root> naive-put <path> <file|->")
        return 2
    expect = None
    if "--expect" in argv:
        i = argv.index("--expect")
        expect = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    root, cmd = argv[1], argv[2]
    src = lambda a: sys.stdin.buffer.read() if a == "-" else open(a, "rb").read()
    if cmd == "naive-put":
        naive_put(RealFS(), argv[3], src(argv[4]))
        return 0
    s = AtomicDocs(root, auto_recover=(cmd != "recover"))
    if cmd == "put":
        try:
            print(s.put(argv[3], src(argv[4]), expect=expect))
        except Conflict as e:
            print("CONFLICT: %s" % e, file=sys.stderr)
            return 3
        except CommitUnknown as e:
            print("OUTCOME UNKNOWN: %s" % e, file=sys.stderr)
            return 4
    elif cmd == "get":
        body = s.get(argv[3])
        if body is None:
            return 1
        sys.stdout.buffer.write(body)
    elif cmd == "history":
        for h in s.history(argv[3]):
            print(h)
    elif cmd == "recover":
        print(json.dumps(s.recover()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
