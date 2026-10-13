"""
SimFS - a power-loss simulator for crash-consistency testing (ALICE-style).

Killing a process is NOT a power cut: the kernel page cache survives a
process kill, so every write() reaches disk eventually whether or not it was
fsynced. SimFS instead models what a disk can really hold after power loss.

It implements the same interface as atomicdocs.RealFS. Every system call the
store makes is recorded and split into "micro-ops" that persist independently:

    write      -> one micro-op per 512-byte sector  (+ a size change if it grows
                  the file; the size may persist before the data, exposing garbage)
    create     -> directory-entry insert
    rename     -> one atomic directory-entry replace     (model W1: ext4/xfs/btrfs)
                  or delete-dst, delete-src, insert-dst   (model W0: worst case)
    unlink     -> directory-entry delete
    fsync(f)   -> barrier: every earlier data/size micro-op on f is now durable
    fsync(dir) -> barrier: every earlier entry micro-op in dir is now durable
                  (fsync of a file does NOT make its directory entry durable)

A legal post-crash disk after syscall k = the initial image + every micro-op
that a completed fsync made durable + ANY subset of the other micro-ops issued
so far, applied in program order (rules follow Pillai et al., OSDI 2014).
"""
import errno
import hashlib
import itertools
import os
import random

SECTOR = 512
GARBAGE = 0xA5          # bytes exposed by a size change whose data never landed


class SimCrash(Exception):
    pass


class _State:
    """A disk image: directory tree of name -> (kind, ino) and inode bytes."""

    def __init__(self):
        self.dirs = {}        # dirpath -> {name: ("f"|"d", ino)}
        self.data = {}        # ino -> bytearray (backing blocks)
        self.size = {}        # ino -> visible size

    def copy(self):
        s = _State()
        s.dirs = {d: dict(e) for d, e in self.dirs.items()}
        s.data = {i: bytearray(b) for i, b in self.data.items()}
        s.size = dict(self.size)
        return s

    def apply(self, op):
        t = op[0]
        if t == "dset":
            _, d, name, kind, ino = op
            self.dirs.setdefault(d, {})[name] = (kind, ino)
            if kind == "d":
                self.dirs.setdefault(d + "/" + name, {})
        elif t == "ddel":
            self.dirs.get(op[1], {}).pop(op[2], None)
        elif t == "drename":
            _, sd, sn, dd, dn, kind, ino = op
            self.dirs.get(sd, {}).pop(sn, None)
            self.dirs.setdefault(dd, {})[dn] = (kind, ino)
        elif t == "size":
            _, ino, n = op
            buf = self.data.setdefault(ino, bytearray())
            if len(buf) < n:
                buf.extend(bytes([GARBAGE]) * (n - len(buf)))
            self.size[ino] = n
        elif t == "sector":
            _, ino, off, chunk = op
            buf = self.data.setdefault(ino, bytearray())
            if len(buf) < off + len(chunk):
                buf.extend(bytes([GARBAGE]) * (off + len(chunk) - len(buf)))
            buf[off:off + len(chunk)] = chunk

    def lookup(self, path):
        d, n = path.rsplit("/", 1)
        return self.dirs.get(d, {}).get(n)

    def read(self, ino):
        return bytes(self.data.get(ino, b"")[: self.size.get(ino, 0)])

    def reachable_files(self, root):
        """{relative path: bytes} for everything reachable from root."""
        out, stack = {}, [root]
        while stack:
            d = stack.pop()
            for name, (kind, ino) in self.dirs.get(d, {}).items():
                p = d + "/" + name
                if kind == "d":
                    out[os.path.relpath(p, root) + "/"] = None
                    stack.append(p)
                else:
                    out[os.path.relpath(p, root)] = self.read(ino)
        return out

    def fingerprint(self, root):
        h = hashlib.sha256()
        for k, v in sorted(self.reachable_files(root).items()):
            h.update(k.encode() + b"\0" + (v if v is not None else b"<dir>") + b"\1")
        return h.hexdigest()


class SimFS:
    name = "sim"

    def __init__(self, base_dir, rename_model="W1"):
        self.root = base_dir.rstrip("/")
        self.model = rename_model
        self.base = _State()
        self.next_ino = 1
        self._load(self.root)
        self.live = self.base.copy()
        self.ops = []            # micro-ops, in program order
        self.syscalls = []       # (label, index of first micro-op after it)
        self.acks = []           # micro-op index at which put() returned
        self.fds = {}            # fd -> [ino, path, offset, append]
        self.next_fd = 3

    # -- loading the durable starting image from a real directory
    def _load(self, d):
        self.base.dirs.setdefault(d, {})
        for name in sorted(os.listdir(d)):
            p = d + "/" + name
            if os.path.isdir(p):
                self.base.dirs[d][name] = ("d", None)
                self._load(p)
            else:
                ino = self.next_ino; self.next_ino += 1
                b = open(p, "rb").read()
                self.base.dirs[d][name] = ("f", ino)
                self.base.data[ino] = bytearray(b)
                self.base.size[ino] = len(b)

    def _emit(self, op):
        self.ops.append(op)
        self.live.apply(op)

    def _done(self, label):
        self.syscalls.append((label, len(self.ops)))

    def _split(self, p):
        p = p.rstrip("/")
        return p.rsplit("/", 1)

    def _ino(self, p):
        e = self.live.lookup(p.rstrip("/"))
        if e is None:
            raise FileNotFoundError(errno.ENOENT, "no such file", p)
        return e

    # -- interface used by the store
    def exists(self, p):
        p = p.rstrip("/")
        return p == self.root or self.live.lookup(p) is not None

    def isdir(self, p):
        p = p.rstrip("/")
        if p == self.root:
            return True
        e = self.live.lookup(p)
        return e is not None and e[0] == "d"

    def listdir(self, p):
        return sorted(self.live.dirs.get(p.rstrip("/"), {}).keys())

    def getsize(self, p):
        return self.live.size.get(self._ino(p)[1], 0)

    def read(self, p):
        return self.live.read(self._ino(p)[1])

    def read_head(self, p, n):
        return self.read(p)[:n]

    def mkdir(self, p):
        d, n = self._split(p)
        if self.live.lookup(p) is not None:
            raise FileExistsError(p)
        self._emit(("dset", d, n, "d", None)); self._done("mkdir " + n)

    def create(self, p):
        d, n = self._split(p)
        if self.live.lookup(p) is not None:
            raise FileExistsError(p)
        ino = self.next_ino; self.next_ino += 1
        self._emit(("dset", d, n, "f", ino))
        self._emit(("size", ino, 0))
        fd = self.next_fd; self.next_fd += 1
        self.fds[fd] = [ino, p, 0, False]
        self._done("create " + n)
        return fd

    def open_append(self, p):
        kind, ino = self._ino(p)
        fd = self.next_fd; self.next_fd += 1
        self.fds[fd] = [ino, p, 0, True]
        return fd

    def open_trunc(self, p):
        e = self.live.lookup(p)
        if e is None:
            return self.create(p)
        ino = e[1]
        self._emit(("size", ino, 0))
        fd = self.next_fd; self.next_fd += 1
        self.fds[fd] = [ino, p, 0, False]
        self._done("open O_TRUNC " + self._split(p)[1])
        return fd

    def write(self, fd, data):
        ino, path, off, append = self.fds[fd]
        if append:
            off = self.live.size.get(ino, 0)
        data = bytes(data)
        end = off + len(data)
        if end > self.live.size.get(ino, 0):
            self._emit(("size", ino, end))
        pos = off
        while pos < end:                              # sector-granular pieces
            nxt = min(end, (pos // SECTOR + 1) * SECTOR)
            self._emit(("sector", ino, pos, data[pos - off: nxt - off]))
            pos = nxt
        self.fds[fd][2] = end
        self._done("write %dB %s" % (len(data), self._split(path)[1]))

    def fsync(self, fd):
        self._emit(("fsync_f", self.fds[fd][0])); self._done("fsync " + self._split(self.fds[fd][1])[1])

    def fsync_path(self, p):
        ino = self._ino(p)[1]
        self._emit(("fsync_f", ino)); self._done("fsync " + self._split(p)[1])

    def fsync_dir(self, p):
        self._emit(("fsync_d", p.rstrip("/"))); self._done("fsync dir " + os.path.basename(p.rstrip("/")))

    def close(self, fd):
        self.fds.pop(fd, None)

    def rename(self, a, b):
        kind, ino = self._ino(a)
        sd, sn = self._split(a); dd, dn = self._split(b)
        if self.model == "W1":
            self._emit(("drename", sd, sn, dd, dn, kind, ino))
        else:                                         # W0: rename is not atomic
            if self.live.lookup(b) is not None:
                self._emit(("ddel", dd, dn))
            self._emit(("ddel", sd, sn))
            self._emit(("dset", dd, dn, kind, ino))
        self._done("rename %s -> %s" % (sn, dn))

    def unlink(self, p):
        self._ino(p)
        d, n = self._split(p)
        self._emit(("ddel", d, n)); self._done("unlink " + n)

    def truncate(self, p, n):
        ino = self._ino(p)[1]
        self._emit(("size", ino, n)); self._emit(("fsync_f", ino)); self._done("truncate")

    def lock(self, p, exclusive):
        return None

    def unlock(self, h):
        pass

    def crash(self):
        raise SimCrash()

    def mark_ack(self):
        self.acks.append(len(self.ops))

    # -- crash-state enumeration --------------------------------------------
    @staticmethod
    def _covers(barrier, op):
        if barrier[0] == "fsync_f":
            return op[0] in ("size", "sector") and op[1] == barrier[1]
        if barrier[0] == "fsync_d":
            d = barrier[1]
            if op[0] in ("dset", "ddel"):
                return op[1] == d
            if op[0] == "drename":
                return op[1] == d or op[3] == d
        return False

    def split(self, k):
        """For a crash after the first k micro-ops: (forced, optional) indices."""
        forced = set()
        for i in range(k):
            if self.ops[i][0] in ("fsync_f", "fsync_d"):
                forced.update(j for j in range(i) if self._covers(self.ops[i], self.ops[j]))
        optional = [j for j in range(k)
                    if j not in forced and self.ops[j][0] not in ("fsync_f", "fsync_d")]
        return forced, optional

    def state(self, chosen):
        s = self.base.copy()
        for i in sorted(chosen):
            s.apply(self.ops[i])
        return s

    def crash_states(self, k, exhaustive_limit=10, samples=150, seed=0):
        """Yield (description, _State) for legal crash states after k micro-ops.
        Exhaustive when there are <= exhaustive_limit optional micro-ops,
        otherwise ALICE-style targeted states plus seeded random subsets."""
        forced, opt = self.split(k)
        subsets = []
        if len(opt) <= exhaustive_limit:
            for r in range(len(opt) + 1):
                subsets.extend(itertools.combinations(opt, r))
            mode = "exhaustive"
        else:
            mode = "sampled"
            subsets.append(())
            subsets.append(tuple(opt))
            subsets.extend(tuple(opt[:i]) for i in range(1, len(opt)))           # prefixes
            subsets.extend(tuple(o for o in opt if o != x) for x in opt)       # all but one
            subsets.extend((x,) for x in opt)                                  # only one
            rng = random.Random(seed * 1000003 + k)
            for _ in range(samples):
                subsets.append(tuple(o for o in opt if rng.random() < 0.5))
        for sub in subsets:
            yield mode, len(opt), self.state(forced | set(sub))

    def materialize(self, st, dest):
        """Write a crash state out as a real directory tree."""
        os.makedirs(dest, exist_ok=True)
        for rel, data in sorted(st.reachable_files(self.root).items()):
            p = os.path.join(dest, rel)
            if data is None:
                os.makedirs(p, exist_ok=True)
            else:
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "wb") as f:
                    f.write(data)
