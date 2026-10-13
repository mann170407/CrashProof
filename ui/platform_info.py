"""
platform_info.py - which kernel / file system this backend really runs on
(the "Two kernels" addendum of ui/CONTRACT.md).

    work_dir()   where scenario stores are created. Normally the system temp
                 dir, but if that is a RAM file system (WSL2 mounts /tmp as
                 tmpfs, where fsync is a no-op) we use ui/.work on the real
                 disk instead, so every fsync in the demo hits a real FS.
    info()       the contract `platform` object: id, label, kernel, fs,
                 python, native (op -> the real OS call), caveats.
"""
import os
import platform
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

# The real OS calls behind each RealFS operation (see atomicdocs.RealFS).
NATIVE = {
    "linux": {
        "create": "open(O_CREAT|O_EXCL)", "write": "write(2)", "fsync": "fsync(2)",
        "fsync_dir": "fsync(2) on directory fd", "rename": "rename(2)", "unlink": "unlink(2)",
        "mkdir": "mkdir(2)", "lock": "flock(LOCK_SH/LOCK_EX)", "unlock": "flock(LOCK_UN)",
        "read": "open(O_RDONLY) + read(2)", "open_trunc": "open(O_CREAT|O_TRUNC)",
    },
    "darwin": {
        "create": "open(O_CREAT|O_EXCL)", "write": "write(2)", "fsync": "fcntl(F_FULLFSYNC)",
        "fsync_dir": "fcntl(F_FULLFSYNC) on directory fd", "rename": "rename(2)", "unlink": "unlink(2)",
        "mkdir": "mkdir(2)", "lock": "flock(LOCK_SH/LOCK_EX)", "unlock": "flock(LOCK_UN)",
        "read": "open(O_RDONLY) + read(2)", "open_trunc": "open(O_CREAT|O_TRUNC)",
    },
    "windows": {
        "create": "_wopen(O_CREAT|O_EXCL|O_BINARY)", "write": "WriteFile", "fsync": "FlushFileBuffers",
        "fsync_dir": "FlushFileBuffers(directory handle)",
        "rename": "MoveFileExW(REPLACE_EXISTING|WRITE_THROUGH)", "unlink": "DeleteFileW",
        "mkdir": "CreateDirectoryW", "lock": "LockFileEx", "unlock": "UnlockFileEx",
        "read": "CreateFileW(FILE_SHARE_DELETE) + ReadFile", "open_trunc": "_wopen(O_CREAT|O_TRUNC|O_BINARY)",
    },
}

RAM_FS = {"tmpfs", "ramfs"}


def platform_id():
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "darwin"
    return "linux"


def is_wsl():
    try:
        with open("/proc/version") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def fs_type(path):
    """File system type of `path`: NTFS/ReFS on Windows (GetVolumeInformationW),
    the mountinfo fstype of the longest matching mount point on Linux."""
    path = os.path.abspath(path)
    if platform_id() == "windows":
        try:
            import ctypes
            buf = ctypes.create_unicode_buffer(64)
            root = os.path.splitdrive(path)[0] + "\\"
            if ctypes.windll.kernel32.GetVolumeInformationW(root, None, 0, None, None, None, buf, 64):
                return buf.value or "unknown"
        except Exception:                           # noqa: BLE001 - best effort
            pass
        return "unknown"
    best, fst = "", "unknown"
    try:
        with open("/proc/self/mountinfo") as f:
            for line in f:
                left, _, right = line.partition(" - ")
                mnt = left.split()[4].replace("\\040", " ")
                if (path == mnt or path.startswith(mnt.rstrip("/") + "/")) and len(mnt) >= len(best):
                    best, fst = mnt, right.split()[0]
    except OSError:
        pass
    return fst


_WORK = None


def work_dir():
    """Directory for scenario stores, on a real (non-RAM) file system."""
    global _WORK
    if _WORK is None:
        d = os.environ.get("CRASHLAB_WORK") or tempfile.gettempdir()
        if fs_type(d) in RAM_FS:
            d = os.path.join(HERE, ".work")
        os.makedirs(d, exist_ok=True)
        _WORK = d
    return _WORK


def kernel():
    if platform_id() == "windows":
        v = sys.getwindowsversion()
        return "%d.%d.%d" % (v.major, v.minor, v.build)
    return platform.release()


_INFO = None


def info():
    global _INFO
    if _INFO is not None:
        return _INFO
    pid = platform_id()
    fst = fs_type(work_dir())
    caveats = []
    if pid == "windows":
        build = sys.getwindowsversion().build
        name = "Windows 11" if build >= 22000 else "Windows %s" % platform.release()
        label = "%s · %s" % (name, fst)
        caveats.append("Windows has no fsync of a directory: the store flushes a directory handle "
                       "(FlushFileBuffers) and renames with MOVEFILE_WRITE_THROUGH.")
    elif pid == "darwin":
        label = "macOS %s · %s" % (platform.mac_ver()[0], fst)
        caveats.append("macOS fsync(2) does not flush the drive cache; the store uses F_FULLFSYNC.")
    else:
        rel = platform.release()
        short = ".".join(rel.split("-")[0].split(".")[:2])
        label = "Linux %s%s · %s" % (short, " (WSL2)" if is_wsl() else "", fst)
        if is_wsl():
            caveats.append("WSL2: /tmp is tmpfs (RAM, fsync is a no-op), so stores live on the ext4 "
                           "virtual disk instead. A real power cut is not possible here; correctness "
                           "under power loss is proven by the SimFS model, not by pulling a cable.")
    if fst in RAM_FS:
        caveats.append("Stores live on a RAM file system (%s): fsync is a no-op here." % fst)
    _INFO = {"id": pid, "label": label, "kernel": kernel(), "fs": fst,
             "python": sys.version.split()[0], "native": NATIVE[pid], "caveats": caveats}
    return _INFO


def short_info():
    i = info()
    return {"id": i["id"], "label": i["label"]}
