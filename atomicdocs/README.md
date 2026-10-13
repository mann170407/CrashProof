# AtomicDocs v2: crash-consistent document storage (Kernel Ops, OSH021)

A crash at any instant during a save leaves each document at exactly its previous
valid version or its new valid version, never a corrupted mixture. Once `put()`
returns, the new version survives power loss.

How: copy-on-write version files + an atomic `rename(2)` of a `CURRENT` pointer
(the same commit protocol LevelDB and RocksDB use) + a salted-CRC write-ahead
intent journal + SHA-256 on every version. Recovery trusts only bytes that verify.

Python 3.8+, standard library only, Linux, macOS or Windows.

| Command | What it shows |
|---|---|
| `./demo.sh` | Live demo: torn write rolled back, post-commit crash rolled forward, naive file corrupted |
| `python3 crash_explorer.py` | **Simulated power loss.** Every legal post-crash disk state (unsynced writes lost, torn 512 B sectors, renames not yet durable) at every system call, recovered with the real code. Also W0 (non-atomic rename), first save, GC, two saves in a row, a process crash at each of the 8 crash points followed by recovery and then power loss, the naive writer, and a mutation test removing each fsync |
| `python3 fault_test.py` | 21 scenarios: fsync EIO at every fsync (fsyncgate), ENOSPC, short writes, bit flips, torn/garbage journal and CURRENT, errors after the commit point (including a failed directory fsync after the CURRENT rename -> `CommitUnknownFsync`), compare-and-swap conflicts, a crash before the CURRENT rename, 16 concurrent writers, first-save races, reader vs GC race, idempotent recovery |
| `python3 crash_test.py` | Process kill (`os._exit`) at 8 named points x 40 trials, vs a naive writer; every point must recover to its expected version (OLD before the CURRENT rename, NEW from it on) |
| `python3 bench.py` | Write latency vs naive writes |
| `crashlab.html` | Open in a browser: interactive "pull the plug" view of the explorer results |

Crash points (`ATOMICDOCS_CRASH_AT`): `after_begin`, `torn_data`, `before_data_fsync`,
`after_data_rename`, `torn_pointer`, `before_pointer_rename`, `after_pointer_rename`,
`after_commit`.

Files: `atomicdocs.py` (store, recovery, CLI), `simfs.py` (power-loss simulator).

CLI:

    python3 atomicdocs.py <root> put <doc> <file|-> [--expect V]   # exit 3 = CAS conflict, 4 = outcome unknown
    python3 atomicdocs.py <root> get <doc>
    python3 atomicdocs.py <root> history <doc>
    python3 atomicdocs.py <root> recover

Results from our run (Windows 11, NTFS, Python 3.13): 417 distinct simulated
power-loss states, 0 corrupted, 0 acknowledged saves lost; naive overwrite
corrupted 11 of 13 states with or without fsync; 320 process kills at 8 crash
points, 0 corrupted, each at its expected version; 21/21 fault scenarios pass.

## Running on Windows

Everything runs natively (no WSL): use `python` (or `py -3`) instead of `python3`
and Git Bash for `demo.sh` (it falls back to `python` when `python3` is absent;
`PYTHON=...` overrides). The store maps each POSIX primitive to its Win32 equivalent:

| POSIX | Windows |
|---|---|
| `flock(LOCK_SH / LOCK_EX)` | `LockFileEx` (shared / `LOCKFILE_EXCLUSIVE_LOCK`) on the lock file |
| `rename(2)` over the old file | `MoveFileExW(MOVEFILE_REPLACE_EXISTING \| MOVEFILE_WRITE_THROUGH)`, bounded retry on sharing violations |
| `fsync(fd)` | `FlushFileBuffers` (via `os.fsync`) |
| `fsync` of a directory fd | `FlushFileBuffers` on a directory handle (`CreateFileW` + `FILE_FLAG_BACKUP_SEMANTICS`) |
| lock-free readers that never block rename/unlink | `CreateFileW` with `FILE_SHARE_READ \| WRITE \| DELETE` |
| binary I/O | `O_BINARY` on every `os.open` (no `\n` -> `\r\n` translation) |

A save costs more on Windows (each directory flush is a full metadata flush;
see `bench.json`), but the guarantees and every test are the same.

Limits: single-document transactions; local file systems (not NFS or SMB shares);
assumes the drive honours flushes; power loss is simulated (SimFS, ALICE-style
model), not a real power cut.
