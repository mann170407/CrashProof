# AtomicDocs Crash Lab — UI ↔ backend contract

The presentation app is a React (Vite + TypeScript) single-page app in `ui/web/`.
It talks to a Python **standard-library** backend `ui/server.py` that runs the
REAL store code (`atomicdocs/atomicdocs.py`, `atomicdocs/simfs.py`) live and
returns animation-ready timelines. Nothing in the UI is faked: every event the
UI animates is a real system call the store made.

    python ui/server.py            # serves ui/web/dist + /api on http://127.0.0.1:8765
    cd ui/web && npm run dev       # Vite dev server on :5173, proxies /api -> :8765

All endpoints return JSON (`Content-Type: application/json`), CORS `*`.
Errors: HTTP 4xx/5xx with `{"error": "message"}`.

## Two kernels: Windows + Linux backends (ADDENDUM — takes precedence)

The demo shows the SAME store on two operating systems. Two copies of the backend run at once:

| Backend | Where it runs | URL from the Windows browser |
|---|---|---|
| windows | `python ui/server.py --port 8765` on Windows (serves the UI too) | `http://127.0.0.1:8765/api` |
| linux   | `ui/server.py --port 8766` inside WSL2 Ubuntu, run from an ext4 copy (`~/crashproof`) | `http://127.0.0.1:8766/api` (WSL2 localhost forwarding) |

The Linux copy must run from the ext4 copy, never from /mnt/c (drvfs is not a real Linux file system,
and the test runner writes logs/results next to the code). `ui/start_linux_backend.sh` syncs
`atomicdocs/` + `ui/*.py` into `~/crashproof/` and starts the server there bound so Windows can reach it.
`ui/start_demo.ps1` (and `start_demo.bat` wrapper) starts both backends and opens the browser.

Additions to the types (all backends):

```ts
// GET /api/info gains:
platform: {
  id: "linux" | "windows" | "darwin";
  label: string;          // e.g. "Linux 6.18 (WSL2) · ext4" or "Windows 11 · NTFS"
  kernel: string;         // uname -r / Windows build
  fs: string;             // file system type of the temp dir used for stores (ext4, NTFS, ...)
  python: string;
  native: Record<string, string>;   // op -> real OS call used on this platform, e.g.
     // linux:   { create: "open(O_CREAT|O_EXCL)", write: "write(2)", fsync: "fsync(2)",
     //            fsync_dir: "fsync(2) on directory fd", rename: "rename(2)", unlink: "unlink(2)",
     //            lock: "flock(LOCK_SH/LOCK_EX)", read: "open(O_RDONLY) + read(2)" }
     // windows: { create: "_wopen(O_CREAT|O_EXCL|O_BINARY)", write: "WriteFile", fsync: "FlushFileBuffers",
     //            fsync_dir: "FlushFileBuffers(directory handle)", rename: "MoveFileExW(REPLACE_EXISTING|WRITE_THROUGH)",
     //            unlink: "DeleteFileW", lock: "LockFileEx", read: "CreateFileW(FILE_SHARE_DELETE) + ReadFile" }
  caveats: string[];      // honest notes, e.g. WSL2: "virtual disk may acknowledge flushes early; correctness is proven by the SimFS power-loss model"
}

// Every syscall Event gains:
native?: string;          // the real OS call for this op on this backend (from platform.native)

// Every ScenarioResult gains:
platform?: { id: string; label: string };
elapsedMs?: number;       // wall-clock runtime of the scenario on this backend
```

Fixtures are per platform: `ui/web/public/fixtures/windows/<name>.json` and `ui/web/public/fixtures/linux/<name>.json`
(same names as before). `ui/record_fixtures.py --base-url URL --out DIR` records from a running backend, so the
Windows run records the windows fixtures and the Linux backend is recorded over HTTP into the linux folder.

Frontend: a global backend switch (Windows | Linux), persisted in localStorage, with each backend's live/replay
status shown separately. Every scene can run "Compare" = the same scenario on BOTH backends, shown side by side
with synchronized playback, each event labelled with its `native` call, plus each side's elapsedMs.

---------------------------------------------------------------------------
## Shared types

```ts
type FileKind = "version" | "current" | "tmp" | "lock" | "journal" | "corrupt" | "other";

interface DiskFile {
  name: string;            // e.g. "v000002.dat", "CURRENT", ".CURRENT.1234.ab12.tmp"
  kind: FileKind;
  size: number;            // bytes
  version: number | null;  // for kind=version: N from vNNNNNN.dat
  valid: boolean | null;   // version files: header+SHA-256 verify? CURRENT: CRC ok? else null
  preview: string;         // first <=24 printable chars of the BODY (version files: body after header;
                           // CURRENT: the pointer line; others ""), non-printables shown as "·"
  fill: string | null;     // single-char summary when body is one repeated byte, e.g. "A" for 64x0x41, else null
  dataDurable: boolean;    // false = written but not yet fsynced (lives only in page cache)
  entryDurable: boolean;   // false = directory entry (create/rename) not yet made durable by fsync(dir)
}

interface JournalRec { t: "BEGIN" | "COMMIT" | "ABORT"; tx: string; old?: number; new?: number; why?: string }

interface DiskSnapshot {
  doc: string;                       // document id shown
  files: DiskFile[];                 // the document directory, sorted by name (.lock files omitted)
  current: { version: number; ok: boolean } | null;   // parsed CURRENT, null if missing; ok=false if torn/bad
  journal: JournalRec[];             // valid records currently in journal.wal
  journalStatus: string;             // "ok" | "torn_tail" | "corrupt" | "no_header" | "missing"
  readable: string | null;           // what get(doc) returns right now, as preview text (null = nothing/none)
  readableFill: string | null;       // e.g. "A" / "B" when the readable body is one repeated byte
}

type Lane = "writer" | "recovery" | "reader" | "system" | `client${number}`;

interface Event {
  seq: number;                  // 0,1,2,... in order
  lane: Lane;                   // who performed it (one swimlane per lane in the UI)
  kind: "phase" | "syscall" | "crash" | "fault" | "result" | "note" | "check";
  op?: "create" | "write" | "fsync" | "fsync_dir" | "rename" | "unlink" | "mkdir" | "lock" | "unlock" | "read";
  label: string;                // short, e.g. "rename .CURRENT.tmp -> CURRENT"
  detail?: string;              // one or two sentences for the presenter
  commit?: boolean;             // true ONLY for the CURRENT rename that commits a save
  path?: string;                // file name affected (basename)
  ok?: boolean;                 // for kind=check / result
  disk?: DiskSnapshot;          // snapshot AFTER this event (present on phase, syscall, crash, fault, result)
  t?: number;                   // ms since scenario start (real wall clock)
}

interface Check { name: string; pass: boolean; detail?: string }

interface ScenarioResult {
  id: string;                   // "plug" | "c1" | "c2" | "c3" | "c4" | "c5"
  title: string;
  params: Record<string, unknown>;
  events: Event[];
  checks: Check[];
  pass: boolean;                // all checks passed
  summary: string;              // one-line outcome for a banner, e.g. "Recovered to A (v1). Rolled back v2."
  extra?: Record<string, unknown>;   // scenario-specific data (see each endpoint)
}
```

Payloads in demos: A = 64 bytes of 0x41, B = 64 bytes of 0x42, C = 64 bytes of 0x43
(unless the endpoint says otherwise). Every scenario uses a fresh temp store and deletes it afterwards.

---------------------------------------------------------------------------
## Endpoints

### GET /api/info
`{ platform, python, crashPoints: string[], recoveryCrashPoints: string[], keepVersions: 3, readRetries: 5 }`

### POST /api/scenario/plug   body `{ "crashAt": string | null, "mode": "atomic" | "naive" }`
"Pull the plug": commit A as v1, then save B and kill the writer at `crashAt`
(one of crashPoints; null = no crash). Then reopen (recovery) and read.
- atomic: real store, crash via store.crash_at (in-process, no cleanup runs after the crash).
- naive: open(O_TRUNC) + write in place; crash point "torn_data" writes half then dies.
Events: writer syscalls, a `crash` event, then `recovery` lane syscalls, then a `result`.
`extra`: `{ outcome: "A" | "B" | "corrupt" | "empty", recovery: <recover() report dict> }`

### POST /api/scenario/c1   body `{}`
KO-04-C1. Two runs back to back: crash `before_pointer_rename` and crash `after_pointer_rename`.
Lanes: `writer`, `recovery`. Phase events separate the two runs.
`extra`: `{ runs: [{ crashAt, expected: "A"|"B", got: "A"|"B"|"corrupt", pass }] }`

### POST /api/scenario/c2   body `{ "clients": number (2..16, default 8) }`
KO-04-C2. Part 1: client1 and client2 both read v1 (=A); client1 put(B, expect=1) commits v2;
client2 put(C, expect=1) -> Conflict; client2 re-reads v2 and retries with expect=2 -> v3.
Lanes `client1`, `client2`.
Part 2 (race): `clients` real OS processes released together, all put(expect=1).
`extra`: `{ race: [{ client: number, outcome: "committed" | "conflict", version: number | null, startMs, endMs }],
            winner: number, contrastNoCas: { clients, finalCount, expectedCount } }`
(contrastNoCas = same race of read-increment-write counter WITHOUT expect: shows lost updates.)

### POST /api/scenario/c3   body `{}`
KO-04-C3. Reader reads CURRENT -> v1 then pauses; writer saves v2..v6 (GC keeps 3);
reader resumes, v1 is gone, retries, returns v6. Then the bound: reader starved every attempt
-> ReadContention after 5 attempts.
Lanes `reader`, `writer`.
`extra`: `{ retention: [{ afterSave: number, onDisk: number[] }], readerAttempts: [{attempt, sawVersion, outcome: "gone"|"ok"}],
            starved: { attempts: number, error: string } }`

### POST /api/scenario/c4   body `{}`
KO-04-C4. v1=A; put(B) on a file system whose fsync(dir) right after the CURRENT rename fails with EIO.
Shows CommitUnknownFsync ("outcome unknown"), poisoned handle, v2 NOT deleted; reopen -> recovery
rolls forward to B. Also the power-loss view of that moment.
`extra`: `{ error: string, poisoned: boolean, afterRecovery: "A"|"B", powerLoss: { states: number, A: number, B: number, corrupt: number } }`

### POST /api/scenario/c5   body `{ "crashPoint": string }`  (one of recoveryCrashPoints)
KO-04-C5. v1=A committed, uncommitted v2=B + leftover temp files (from a put killed at before_pointer_rename).
Run recovery and kill it at `crashPoint`; rerun recovery; rerun again. Lanes: `writer` (setup), `recovery`.
Phase events: "setup", "recovery #1 (interrupted)", "rerun 1", "rerun 2".
`extra`: `{ fingerprints: [string, string], identical: boolean, reports: [report, report] }`

### GET /api/powerloss
Live ALICE-style enumeration with SimFS for one put of B over A, for BOTH the atomic store and the
naive in-place writer. Cached after the first call.
```ts
{
  atomic: { syscalls: { k: number, label: string, commit?: boolean }[],
            perSyscall: { k: number, states: number, A: number, B: number, corrupt: number }[],
            totals: { states, A, B, corrupt } },
  naive:  { same shape },
  sample: { atomic: ("A"|"B"|"X")[], naive: ("A"|"B"|"X")[] }   // flattened per-state outcomes for a dot grid (<= 600 each)
}
```

### GET /api/results
Stored results from the last full test run: `{ explorer, faults, crashMatrix, bench, challenges: {KO-04-C1: {...}, ...} }`
(from atomicdocs/*.json and atomicdocs/challenges/results/*.json; missing files -> null).

### GET /api/tests/stream?only=c1,c2   (Server-Sent Events)
Runs the suites/challenges (same list as atomicdocs/run_all.py; `only` optional) one by one as subprocesses
and streams:
- `event: start` data `{ name, title }`
- `event: line`  data `{ name, text }`        (every output line, as it happens)
- `event: end`   data `{ name, pass, secs, last }`
- `event: done`  data `{ passed, total }`
