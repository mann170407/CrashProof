/**
 * Offline fallbacks that follow ui/CONTRACT.md exactly. Real recorded fixtures in
 * public/fixtures/<platform>/ always win over these (see api.ts).
 */
import type {
  BackendId, C1Extra, C2Extra, C3Extra, C4Extra, C5Extra, Check, Info, PlugExtra, PowerLoss, ScenarioResult, StoredResults,
} from "../types";
import { Crash, NATIVE, Sim, vfile } from "./sim";

const CRASH_POINTS = [
  "after_begin", "torn_data", "before_data_fsync", "after_data_rename",
  "torn_pointer", "before_pointer_rename", "after_pointer_rename", "after_commit",
];
const RECOVERY_CRASH_POINTS = [
  "recover_after_tmp_unlink", "recover_after_version_unlink", "recover_after_pointer_rewrite", "recover_before_checkpoint",
];
const NEW_AFTER = new Set(["after_pointer_rename", "after_commit"]);

export function mockInfo(p: BackendId): Info {
  return {
    platform: {
      id: p,
      label: p === "linux" ? "Linux 6.6 (WSL2) · ext4 — offline sim" : "Windows 11 · NTFS — offline sim",
      kernel: p === "linux" ? "6.6.87.2-microsoft-standard-WSL2" : "10.0.26300",
      fs: p === "linux" ? "ext4" : "NTFS",
      python: "3.13",
      native: NATIVE[p],
      caveats:
        p === "linux"
          ? ["WSL2 virtual disk may acknowledge flushes early; correctness is proven by the SimFS power-loss model, not by pulling a real cord."]
          : ["Each directory flush is a full metadata flush on NTFS, so saves cost more than on Linux; guarantees are identical."],
    },
    python: "3.13",
    crashPoints: CRASH_POINTS,
    recoveryCrashPoints: RECOVERY_CRASH_POINTS,
    keepVersions: 3,
    readRetries: 5,
  };
}

const ck = (name: string, pass: boolean, detail?: string): Check => ({ name, pass, detail });

export function mockPlug(p: BackendId, crashAt: string | null, mode: "atomic" | "naive"): ScenarioResult<PlugExtra> {
  const s = new Sim(p);
  if (mode === "naive") {
    s.files.set("report.txt", { name: "report.txt", kind: "other", size: 64, version: null, valid: null, preview: "A".repeat(24), fill: "A", dataDurable: true, entryDurable: true });
    s.ev("writer", "phase", "naive save: open(O_TRUNC) + write in place", { detail: "What most code does. The old bytes are destroyed before the new ones exist." });
    const f = s.files.get("report.txt")!;
    f.size = 0; f.preview = ""; f.fill = null; f.dataDurable = false;
    s.ev("writer", "syscall", "open report.txt O_TRUNC", { op: "create", path: "report.txt", detail: "Truncate: A is gone right now." });
    let outcome: PlugExtra["outcome"] = "B";
    if (crashAt === "torn_data") {
      f.size = 32; f.preview = "B".repeat(24); f.fill = null;
      s.ev("writer", "syscall", "write 32B report.txt (half)", { op: "write", path: "report.txt" });
      f.dataDurable = true;
      s.ev("writer", "syscall", "fsync report.txt", { op: "fsync", path: "report.txt" });
      s.ev("writer", "crash", "CRASH mid-write (torn_data)", { detail: "Half of B on disk, A destroyed: neither version exists." });
      outcome = "corrupt";
      s.ev("recovery", "phase", "reopen: no recovery possible", { detail: "There is no old copy and no checksum. The reader gets 32 bytes of junk." });
      s.ev("recovery", "syscall", "read report.txt -> 32B", { op: "read", path: "report.txt" });
    } else {
      f.size = 64; f.preview = "B".repeat(24); f.fill = "B";
      s.ev("writer", "syscall", "write 64B report.txt", { op: "write", path: "report.txt" });
      f.dataDurable = true;
      s.ev("writer", "syscall", "fsync report.txt", { op: "fsync", path: "report.txt" });
    }
    const r = s.result({
      id: "plug", title: "Pull the plug (naive)", params: { crashAt, mode },
      checks: [ck("result is exactly A or exactly B", outcome !== "corrupt", outcome)],
      summary: outcome === "corrupt" ? "Torn file: 32 bytes of B, A destroyed. Data lost." : "Save finished (this time).",
      extra: { outcome, recovery: null } as PlugExtra,
    });
    // the naive file's "readable" is its raw content
    r.events.forEach((e) => {
      if (e.disk) {
        const f2 = e.disk.files[0];
        e.disk.readable = f2 && f2.size ? f2.preview : "";
        e.disk.readableFill = f2?.size === 64 ? f2.fill : null;
      }
    });
    return r;
  }
  s.seed("A");
  s.ev("writer", "phase", "v1 = A committed", { detail: "Starting state: A is the durable, committed version." });
  s.crashAt = crashAt;
  let report: Record<string, unknown> = {};
  try {
    s.put("writer", "B");
  } catch (e) {
    if (!(e instanceof Crash)) throw e;
  }
  s.crashAt = null;
  if (crashAt) report = s.recover();
  s.ev("reader", "syscall", "get(report)", { op: "read", path: vfile(s.current()?.version ?? 1) });
  const expected = !crashAt || NEW_AFTER.has(crashAt) ? "B" : "A";
  const got = (s.readable().fill ?? "corrupt") as PlugExtra["outcome"];
  return s.result({
    id: "plug", title: "Pull the plug", params: { crashAt, mode },
    checks: [
      ck("process killed at the chosen point", true, crashAt ?? "no crash"),
      ck(`get() == ${expected} exactly (64 bytes)`, got === expected),
      ck("no temp files left", ![...s.files.keys()].some((n) => n.endsWith(".tmp"))),
      ck("CURRENT names a version whose SHA-256 verifies", true),
    ],
    summary:
      !crashAt ? "Committed v2 = B." : expected === "A" ? "Crash before the commit point: rolled back, A (v1) intact." : "Crash after the commit point: rolled forward, B (v2) durable.",
    extra: { outcome: got, recovery: report } as PlugExtra,
  });
}

export function mockC1(p: BackendId): ScenarioResult<C1Extra> {
  const s = new Sim(p);
  const runs: C1Extra["runs"] = [];
  const checks: Check[] = [];
  for (const [i, point] of ["before_pointer_rename", "after_pointer_rename"].entries()) {
    s.files.clear();
    s.journal = [];
    s.seed("A");
    s.ev("system", "phase", `run ${i + 1}: crash ${point}`, { detail: `Fresh store, v1 = A. Save B and kill the writer ${point.replace(/_/g, " ")}.` });
    s.crashAt = point;
    try {
      s.put("writer", "B");
    } catch {
      /* crashed */
    }
    s.crashAt = null;
    s.recover();
    const got = (s.readable().fill ?? "corrupt") as "A" | "B";
    const expected = i === 0 ? "A" : "B";
    runs.push({ crashAt: point, expected, got, pass: got === expected });
    checks.push(ck(`${point}: get() == ${expected} exactly`, got === expected), ck(`${point}: no temp files left`, true), ck(`${point}: recovery reports ${i ? "rolled_forward" : "rolled_back"}`, true));
  }
  return s.result({
    id: "c1", title: "Commit-point crash matrix", params: {}, checks,
    summary: "Before the CURRENT rename -> A. After it -> B. Never anything else.",
    extra: { runs },
  });
}

export function mockC2(p: BackendId, clients = 8): ScenarioResult<C2Extra> {
  const s = new Sim(p);
  s.seed("A");
  s.ev("system", "phase", "part 1: two clients, both read v1", { detail: "client1 and client2 both read v1 = A." });
  s.ev("client1", "syscall", "get() -> v1 = A", { op: "read", path: "v000001.dat" });
  s.ev("client2", "syscall", "get() -> v1 = A", { op: "read", path: "v000001.dat" });
  s.ev("client1", "note", "put(B, expect=1)", { detail: "Compare-and-swap: commit only if CURRENT still names v1." });
  s.put("client1", "B");
  s.ev("client2", "note", "put(C, expect=1)", { detail: "client2 still thinks v1 is current." });
  s.ev("client2", "syscall", "lock report/.lock (exclusive)", { op: "lock", path: ".lock" });
  s.ev("client2", "syscall", "read CURRENT -> v2", { op: "read", path: "CURRENT" });
  s.ev("client2", "check", "CONFLICT: expected v1, current v2 — rejected before any write", { ok: true });
  s.ev("client2", "syscall", "get() -> v2 = B (re-read)", { op: "read", path: "v000002.dat" });
  s.ev("client2", "note", "merge, then put(C, expect=2)");
  s.put("client2", "C");
  const winner = 1 + Math.floor(Math.random() * clients);
  const race = Array.from({ length: clients }, (_, i) => {
    const start = 2 + Math.random() * 6;
    const won = i + 1 === winner;
    return {
      client: i + 1,
      outcome: (won ? "committed" : "conflict") as "committed" | "conflict",
      version: won ? 2 : null,
      startMs: Math.round(start * 10) / 10,
      endMs: Math.round((start + (won ? (p === "windows" ? 38 : 9) : 3 + Math.random() * 12)) * 10) / 10,
    };
  });
  s.ev("system", "phase", `part 2: ${clients} processes race put(expect=1)`, { detail: "Exactly one wins; every other one gets CONFLICT." });
  return s.result({
    id: "c2", title: "Lost-update rejection", params: { clients },
    checks: [
      ck("both clients saw v1 = A", true), ck("client1 CAS succeeds and creates v2", true), ck("client2 rejected with CONFLICT (expected 1, current 2)", true),
      ck("no file contains C after the rejected put", true), ck("retry with expect=2 commits v3", true), ck(`race: exactly 1 of ${clients} committed`, true),
    ],
    summary: `One winner, ${clients - 1} conflicts, zero lost updates.`,
    extra: { race, winner, contrastNoCas: { clients, finalCount: Math.max(1, Math.round(clients / 3)), expectedCount: clients } },
  });
}

export function mockC3(p: BackendId): ScenarioResult<C3Extra> {
  const s = new Sim(p);
  s.seed("A");
  s.ev("reader", "syscall", "read CURRENT -> v1", { op: "read", path: "CURRENT" });
  s.ev("reader", "note", "reader PAUSED (between CURRENT read and version open)", { detail: "The reader holds the name v1 but has not opened the file yet." });
  const retention: C3Extra["retention"] = [];
  const fills = ["B", "C", "D", "E", "F"];
  for (const f of fills) {
    const v = s.put("writer", f);
    retention.push({ afterSave: v, onDisk: s.versions() });
  }
  s.ev("reader", "note", "reader resumes");
  s.ev("reader", "syscall", "open v000001.dat -> ENOENT (GC removed it)", { op: "read", path: "v000001.dat" });
  s.ev("reader", "syscall", "retry: read CURRENT -> v6", { op: "read", path: "CURRENT" });
  s.ev("reader", "syscall", "open v000006.dat + SHA-256 ✓ -> F", { op: "read", path: "v000006.dat" });
  s.ev("reader", "check", "reader returned v6 whole (2 attempts)", { ok: true });
  s.ev("reader", "check", "starved reader: ReadContention after 5 attempts (bounded)", { ok: true });
  return s.result({
    id: "c3", title: "Retention under read pressure", params: {},
    checks: [
      ck("never more than 3 version files after GC", true, "max seen 3"), ck("v1 removed exactly when v4 committed", true),
      ck("reader got v6 exactly", true), ck("retry bounded: 2 attempts (<= 5)", true), ck("ReadContention after exactly 5 attempts", true),
    ],
    summary: "Retention held at 3. Reader retried once and got v6 whole. Starvation is bounded at 5 attempts.",
    extra: {
      retention,
      readerAttempts: [{ attempt: 1, sawVersion: 1, outcome: "gone" }, { attempt: 2, sawVersion: 6, outcome: "ok" }],
      starved: { attempts: 5, error: "ReadContention: 'report': version removed by GC 5 times in a row" },
    },
  });
}

export function mockC4(p: BackendId): ScenarioResult<C4Extra> {
  const s = new Sim(p);
  s.seed("A");
  s.ev("writer", "phase", "v1 = A committed; fsync(dir) after the CURRENT rename will fail", {});
  let error = "";
  try {
    s.put("writer", "B", { faultAfterCommit: true });
  } catch {
    error = "CommitUnknownFsync: flush failed after the commit point, outcome unknown; reopen to let recovery decide";
  }
  s.ev("writer", "check", "put raised CommitUnknownFsync (outcome UNKNOWN, not 'failed')", { ok: true });
  s.ev("writer", "check", "handle poisoned: next put raises Poisoned (no blind fsync retry)", { ok: true });
  s.ev("writer", "check", "v000002.dat NOT deleted", { ok: true });
  s.recover();
  return s.result({
    id: "c4", title: "Failure after commit before flush", params: {},
    checks: [
      ck("fault injected exactly once, on fsync(dir) after the CURRENT rename", true), ck("put raised CommitUnknownFsync", true),
      ck("handle poisoned", true), ck("v2 not deleted by the failed put", true), ck("recovery rolled forward to B, v1 kept", true),
    ],
    summary: "Outcome reported as UNKNOWN; nothing deleted; recovery rolled forward to B.",
    extra: { error, poisoned: true, afterRecovery: "B", powerLoss: { states: 4, A: 2, B: 2, corrupt: 0 } },
  });
}

export function mockC5(p: BackendId, crashPoint: string): ScenarioResult<C5Extra> {
  const s = new Sim(p);
  s.seed("A");
  s.ev("writer", "phase", "setup", { detail: "v1 = A committed; a save of B killed before the CURRENT rename leaves v2 + temp files." });
  s.crashAt = "before_pointer_rename";
  try {
    s.put("writer", "B");
  } catch {
    /* crashed */
  }
  s.crashAt = crashPoint;
  const reports: Record<string, unknown>[] = [];
  try {
    s.recover("recovery", "recovery #1 (interrupted)");
    s.ev("recovery", "note", `crash point ${crashPoint} not reached in this setup — recovery completed`);
  } catch {
    /* interrupted */
  }
  s.crashAt = null;
  reports.push(s.recover("recovery", "rerun 1"));
  const f1 = s.fingerprint();
  reports.push(s.recover("recovery", "rerun 2"));
  const f2 = s.fingerprint();
  return s.result({
    id: "c5", title: "Interrupted recovery", params: { crashPoint },
    checks: [
      ck(`recovery killed at ${crashPoint}`, true), ck("rerun 1: get() == A exactly", s.readable().fill === "A"),
      ck("uncommitted v2 and temp files gone", true), ck("rerun 2 changes nothing (identical fingerprint)", f1 === f2),
    ],
    summary: "Interrupted recovery, rerun twice: same accepted version A, identical disk.",
    extra: { fingerprints: [f1, f2], identical: f1 === f2, reports },
  });
}

// ---- power loss (numbers follow atomicdocs/explorer_results.json trace) ----
const TRACE: [number, string, number, number, boolean?][] = [
  [0, "start", 1, 0], [2, "write journal.wal (BEGIN)", 4, 0], [3, "fsync journal.wal", 1, 0],
  [5, "create .v000002.dat.tmp", 4, 0], [9, "write 1368B .v000002.dat.tmp", 64, 0], [10, "fsync .v000002.dat.tmp", 2, 0],
  [11, "rename tmp -> v000002.dat", 4, 0], [12, "fsync dir", 1, 0], [14, "create .CURRENT.tmp", 4, 0],
  [16, "write 91B .CURRENT.tmp", 16, 0], [17, "fsync .CURRENT.tmp", 2, 0], [18, "rename .CURRENT.tmp -> CURRENT", 2, 2, true],
  [19, "fsync dir", 0, 1], [21, "write journal.wal (COMMIT)", 0, 4], [22, "fsync journal.wal", 0, 1],
];

export function mockPowerLoss(): PowerLoss {
  const atomicPer = TRACE.map(([k, , A, B]) => ({ k, states: A + B, A, B, corrupt: 0 }));
  const naivePer = [
    { k: 0, states: 1, A: 1, B: 0, corrupt: 0 },
    { k: 1, states: 2, A: 0, B: 0, corrupt: 2 },
    { k: 2, states: 9, A: 0, B: 0, corrupt: 9 },
    { k: 3, states: 1, A: 0, B: 1, corrupt: 0 },
  ];
  const tot = (a: typeof atomicPer) => a.reduce((t, x) => ({ states: t.states + x.states, A: t.A + x.A, B: t.B + x.B, corrupt: t.corrupt + x.corrupt }), { states: 0, A: 0, B: 0, corrupt: 0 });
  const flat = (a: typeof atomicPer) => a.flatMap((x) => [...Array(x.A).fill("A"), ...Array(x.B).fill("B"), ...Array(x.corrupt).fill("X")]) as ("A" | "B" | "X")[];
  return {
    atomic: { syscalls: TRACE.map(([k, label, , , commit]) => ({ k, label, commit })), perSyscall: atomicPer, totals: tot(atomicPer) },
    naive: {
      syscalls: [{ k: 0, label: "start" }, { k: 1, label: "open(O_TRUNC) report" }, { k: 2, label: "write 64B report" }, { k: 3, label: "fsync report" }],
      perSyscall: naivePer, totals: tot(naivePer),
    },
    sample: { atomic: flat(atomicPer), naive: flat(naivePer) },
  };
}

export function mockResults(): StoredResults {
  const ch = (id: string, title: string, n: number) => ({ id, title, pass: true, passed: n, total: n, checks: [] });
  return {
    explorer: { total_states: 417, seconds: 41.1, ours_W1_small: { states: 19, CORRUPT: 0 }, naive_fsync: { states: 13, CORRUPT: 11 } },
    faults: Object.fromEntries(Array.from({ length: 21 }, (_, i) => [`fault scenario ${i + 1}`, { pass: true }])),
    crashMatrix: {
      crash_matrix: CRASH_POINTS.map((point) => ({ point, old: NEW_AFTER.has(point) ? 0 : 40, new: NEW_AFTER.has(point) ? 40 : 0, corrupt: 0, trials: 40 })),
      naive: { old: 0, new: 0, corrupt: 40, trials: 40 },
    },
    bench: { atomicdocs_1000B_ms: 26.2, naive_fsync_1000B_ms: 1.56, naive_nofsync_1000B_ms: 0.35 },
    challenges: {
      "KO-04-C1": ch("KO-04-C1", "Commit-point crash matrix", 31),
      "KO-04-C2": ch("KO-04-C2", "Lost-update rejection", 25),
      "KO-04-C3": ch("KO-04-C3", "Retention under read pressure", 16),
      "KO-04-C4": ch("KO-04-C4", "Failure after commit before flush", 24),
      "KO-04-C5": ch("KO-04-C5", "Interrupted recovery", 142),
    },
  };
}
