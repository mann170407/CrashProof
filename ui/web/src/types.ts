// Types mirroring ui/CONTRACT.md exactly. Optional fields stay optional;
// the UI never assumes more than the contract promises.

export type FileKind = "version" | "current" | "tmp" | "lock" | "journal" | "corrupt" | "other";

export interface DiskFile {
  name: string;
  kind: FileKind;
  size: number;
  version: number | null;
  valid: boolean | null;
  preview: string;
  fill: string | null;
  dataDurable: boolean;
  entryDurable: boolean;
}

export interface JournalRec {
  t: "BEGIN" | "COMMIT" | "ABORT";
  tx: string;
  old?: number;
  new?: number;
  why?: string;
}

export interface DiskSnapshot {
  doc: string;
  files: DiskFile[];
  current: { version: number; ok: boolean } | null;
  journal: JournalRec[];
  journalStatus: string;
  readable: string | null;
  readableFill: string | null;
}

export type Lane = "writer" | "recovery" | "reader" | "system" | `client${number}`;

export type EventKind = "phase" | "syscall" | "crash" | "fault" | "result" | "note" | "check";
export type SysOp =
  | "create" | "write" | "fsync" | "fsync_dir" | "rename" | "unlink"
  | "mkdir" | "lock" | "unlock" | "read";

export interface LabEvent {
  seq: number;
  lane: Lane;
  kind: EventKind;
  op?: SysOp;
  label: string;
  detail?: string;
  commit?: boolean;
  path?: string;
  ok?: boolean;
  disk?: DiskSnapshot;
  t?: number;
  /** ADDENDUM: the real OS call for this op on this backend (from platform.native) */
  native?: string;
}

export interface Check {
  name: string;
  pass: boolean;
  detail?: string;
}

export interface ScenarioResult<X = Record<string, unknown>> {
  id: string;
  title: string;
  params: Record<string, unknown>;
  events: LabEvent[];
  checks: Check[];
  pass: boolean;
  summary: string;
  extra?: X;
  /** ADDENDUM */
  platform?: { id: string; label: string };
  elapsedMs?: number;
}

// ---- endpoint payloads -----------------------------------------------------

export type PlatformId = "linux" | "windows" | "darwin";

export interface PlatformInfo {
  id: PlatformId | string;
  label: string;
  kernel: string;
  fs: string;
  python: string;
  native: Record<string, string>;
  caveats: string[];
}

export interface Info {
  /** ADDENDUM: an object. (Pre-addendum servers sent a plain string; api.ts normalises.) */
  platform: PlatformInfo;
  python: string;
  crashPoints: string[];
  recoveryCrashPoints: string[];
  keepVersions: number;
  readRetries: number;
  /** not in the contract text, sent by ui/server.py: crash points of the naive writer */
  naiveCrashPoints?: string[];
}

export type Outcome = "A" | "B" | "corrupt" | "empty";

export interface PlugExtra {
  outcome: Outcome;
  recovery?: Record<string, unknown> | null;
}

export interface C1Extra {
  runs: { crashAt: string; expected: "A" | "B"; got: "A" | "B" | "corrupt"; pass: boolean }[];
}

export interface RaceEntry {
  client: number;
  outcome: "committed" | "conflict";
  version: number | null;
  startMs: number;
  endMs: number;
}

export interface C2Extra {
  race: RaceEntry[];
  winner: number;
  contrastNoCas: { clients: number; finalCount: number; expectedCount: number };
}

export interface C3Extra {
  retention: { afterSave: number; onDisk: number[] }[];
  readerAttempts: { attempt: number; sawVersion: number; outcome: "gone" | "ok" }[];
  starved: { attempts: number; error: string };
}

export interface C4Extra {
  error: string;
  poisoned: boolean;
  afterRecovery: "A" | "B";
  powerLoss: { states: number; A: number; B: number; corrupt: number };
}

export interface C5Extra {
  fingerprints: [string, string];
  identical: boolean;
  reports: Record<string, unknown>[];
}

export interface PowerLossSide {
  syscalls: { k: number; label: string; commit?: boolean }[];
  perSyscall: { k: number; states: number; A: number; B: number; corrupt: number }[];
  totals: { states: number; A: number; B: number; corrupt: number };
}

export interface PowerLoss {
  atomic: PowerLossSide;
  naive: PowerLossSide;
  sample: { atomic: ("A" | "B" | "X")[]; naive: ("A" | "B" | "X")[] };
}

/** /api/results: shapes of the stored files are loosely typed on purpose. */
export interface StoredResults {
  explorer: Record<string, unknown> | null;
  faults: Record<string, unknown> | null;
  crashMatrix: unknown;
  bench: Record<string, number> | null;
  challenges: Record<string, StoredChallenge | null> | null;
}

export interface StoredChallenge {
  id?: string;
  title?: string;
  pass?: boolean;
  passed?: number;
  total?: number;
  checks?: { check?: string; name?: string; pass: boolean; detail?: string }[];
}

// ---- SSE /api/tests/stream ------------------------------------------------
export interface StreamStart { name: string; title: string }
export interface StreamLine { name: string; text: string }
export interface StreamEnd { name: string; pass: boolean; secs: number; last: string }
export interface StreamDone { passed: number; total: number }

/** where a payload came from: live API, recorded fixture, or the built-in offline simulation */
export type DataSource = "live" | "replay" | "mock";
export type BackendId = "windows" | "linux";
