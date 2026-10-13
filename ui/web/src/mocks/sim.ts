/**
 * OFFLINE SIMULATION — used only when neither the live API nor recorded fixtures are reachable.
 * A small model of the AtomicDocs protocol (atomicdocs/atomicdocs.py) that emits events and disk
 * snapshots in the exact shape of ui/CONTRACT.md, so the UI can be built and rehearsed offline.
 * The UI labels anything coming from here as "SIM", never as LIVE.
 */
import type {
  BackendId, Check, DiskFile, DiskSnapshot, EventKind, JournalRec, LabEvent, Lane, ScenarioResult, SysOp,
} from "../types";

export const NATIVE: Record<BackendId, Record<string, string>> = {
  linux: {
    create: "open(O_CREAT|O_EXCL)",
    write: "write(2)",
    fsync: "fsync(2)",
    fsync_dir: "fsync(2) on directory fd",
    rename: "rename(2)",
    unlink: "unlink(2)",
    mkdir: "mkdir(2)",
    lock: "flock(LOCK_SH/LOCK_EX)",
    unlock: "flock(LOCK_UN)",
    read: "open(O_RDONLY) + read(2)",
  },
  windows: {
    create: "_wopen(O_CREAT|O_EXCL|O_BINARY)",
    write: "WriteFile",
    fsync: "FlushFileBuffers",
    fsync_dir: "FlushFileBuffers(directory handle)",
    rename: "MoveFileExW(REPLACE_EXISTING|WRITE_THROUGH)",
    unlink: "DeleteFileW",
    mkdir: "CreateDirectoryW",
    lock: "LockFileEx",
    unlock: "UnlockFileEx",
    read: "CreateFileW(FILE_SHARE_DELETE) + ReadFile",
  },
};

const LAT: Record<BackendId, Partial<Record<SysOp | "default", number>>> = {
  linux: { default: 0.08, fsync: 1.6, fsync_dir: 1.2, rename: 0.1, write: 0.05 },
  windows: { default: 0.25, fsync: 3.4, fsync_dir: 7.5, rename: 0.9, write: 0.12 },
};

const vname = (v: number) => `v${String(v).padStart(6, "0")}.dat`;
let rnd = 7;
const hex = (n: number) => {
  let s = "";
  for (let i = 0; i < n; i++) {
    rnd = (rnd * 1103515245 + 12345) & 0x7fffffff;
    s += "0123456789abcdef"[rnd % 16];
  }
  return s;
};

export class Crash extends Error {
  constructor(public point: string) {
    super("crash at " + point);
  }
}

export class Sim {
  files = new Map<string, DiskFile>();
  journal: JournalRec[] = [];
  journalStatus = "ok";
  events: LabEvent[] = [];
  t = 0;
  pid = 4000 + Math.floor(Math.random() * 4000);
  crashAt: string | null = null;
  readonly doc = "report";

  constructor(public platform: BackendId) {}

  // ---- snapshot ----
  current(): { version: number; ok: boolean } | null {
    const c = this.files.get("CURRENT");
    if (!c) return null;
    const m = /^v(\d+)/.exec(c.preview);
    if (!m || c.valid === false) return { version: 0, ok: false };
    return { version: parseInt(m[1], 10), ok: true };
  }
  readable(): { text: string | null; fill: string | null } {
    const cur = this.current();
    let v = cur && cur.ok ? cur.version : null;
    const versions = [...this.files.values()].filter((f) => f.kind === "version" && f.valid).sort((a, b) => (b.version ?? 0) - (a.version ?? 0));
    let f = v ? versions.find((x) => x.version === v) : undefined;
    if (!f && (!cur || !cur.ok)) f = versions[0];
    if (!f) return { text: null, fill: null };
    v = f.version;
    return { text: f.preview, fill: f.fill };
  }
  snap(): DiskSnapshot {
    const r = this.readable();
    return {
      doc: this.doc,
      files: [...this.files.values()].map((f) => ({ ...f })).sort((a, b) => a.name.localeCompare(b.name)),
      current: this.current(),
      journal: this.journal.map((j) => ({ ...j })),
      journalStatus: this.journalStatus,
      readable: r.text,
      readableFill: r.fill,
    };
  }

  ev(lane: Lane, kind: EventKind, label: string, o: Partial<LabEvent> = {}) {
    const lat = LAT[this.platform];
    this.t += (o.op ? lat[o.op] ?? lat.default! : 0.05) * (0.7 + Math.random() * 0.6);
    const withDisk = ["phase", "syscall", "crash", "fault", "result"].includes(kind);
    const e: LabEvent = {
      seq: this.events.length,
      lane,
      kind,
      label,
      ...o,
      t: Math.round(this.t * 100) / 100,
    };
    if (o.op && kind === "syscall") e.native = NATIVE[this.platform][o.op];
    if (withDisk) e.disk = this.snap();
    this.events.push(e);
    return e;
  }
  maybeCrash(lane: Lane, point: string) {
    if (this.crashAt === point) {
      this.ev(lane, "crash", `CRASH at ${point}`, {
        detail: "Process killed (os._exit) — no cleanup, no flush. Whatever is on disk now is all recovery gets.",
      });
      throw new Crash(point);
    }
  }

  // ---- syscalls ----
  create(lane: Lane, name: string, kind: DiskFile["kind"]) {
    this.files.set(name, { name, kind, size: 0, version: null, valid: null, preview: "", fill: null, dataDurable: true, entryDurable: false });
    this.ev(lane, "syscall", `create ${name}`, { op: "create", path: name });
  }
  write(lane: Lane, name: string, fill: string, size: number, preview?: string) {
    const f = this.files.get(name)!;
    f.size = size;
    f.fill = preview ? null : fill;
    f.preview = preview ?? fill.repeat(24);
    f.dataDurable = false;
    this.ev(lane, "syscall", `write ${size}B ${name}`, { op: "write", path: name });
  }
  fsync(lane: Lane, name: string) {
    const f = this.files.get(name);
    if (f) f.dataDurable = true;
    this.ev(lane, "syscall", `fsync ${name}`, { op: "fsync", path: name });
  }
  fsyncDir(lane: Lane, detail?: string) {
    this.files.forEach((f) => (f.entryDurable = true));
    this.ev(lane, "syscall", `fsync dir ${this.doc}/`, { op: "fsync_dir", path: this.doc, detail });
  }
  rename(lane: Lane, from: string, to: string, commit = false) {
    const f = this.files.get(from)!;
    this.files.delete(from);
    const nf: DiskFile = { ...f, name: to, entryDurable: false };
    if (to === "CURRENT") {
      nf.kind = "current";
      nf.valid = true;
    } else if (/^v\d{6}\.dat$/.test(to)) {
      nf.kind = "version";
      nf.version = parseInt(to.slice(1, 7), 10);
      nf.valid = f.fill !== null;
    }
    this.files.set(to, nf);
    this.ev(lane, "syscall", `rename ${from} -> ${to}`, {
      op: "rename",
      path: to,
      commit: commit || undefined,
      detail: commit
        ? "THE commit point: one atomic rename swaps CURRENT from the old version to the new one. Before it: old version. After it: new version."
        : undefined,
    });
  }
  unlink(lane: Lane, name: string, detail?: string) {
    this.files.delete(name);
    this.ev(lane, "syscall", `unlink ${name}`, { op: "unlink", path: name, detail });
  }
  jlog(lane: Lane, rec: JournalRec) {
    this.journal.push(rec);
    this.ev(lane, "syscall", `append ${rec.t}${rec.t === "BEGIN" ? ` v${rec.old}->v${rec.new}` : ""} to journal.wal`, {
      op: "write",
      path: "journal.wal",
      detail: rec.t === "BEGIN" ? "Write-ahead intent: recovery will know a save of this document was in flight." : undefined,
    });
    this.ev(lane, "syscall", "fsync journal.wal", { op: "fsync", path: "journal.wal" });
  }

  // ---- setup without events (the "already committed" starting state) ----
  seed(fill: string, version = 1) {
    this.files.set(vname(version), {
      name: vname(version), kind: "version", size: 1368, version, valid: true, preview: fill.repeat(24), fill, dataDurable: true, entryDurable: true,
    });
    this.setPointer(version);
  }
  setPointer(version: number) {
    this.files.set("CURRENT", {
      name: "CURRENT", kind: "current", size: 91, version: null, valid: true,
      preview: `${vname(version).slice(0, 7)} sha256=${hex(8)}`.slice(0, 24), fill: null, dataDurable: true, entryDurable: true,
    });
  }

  versions(): number[] {
    return [...this.files.values()].filter((f) => f.kind === "version").map((f) => f.version!).sort((a, b) => a - b);
  }

  /** atomicdocs put(): returns new version. Throws Crash. `faultAfterCommit` = EIO on fsync(dir) after the CURRENT rename. */
  put(lane: Lane, fill: string, opts: { faultAfterCommit?: boolean; keep?: number } = {}): number {
    const old = this.current()?.version ?? 0;
    const nv = Math.max(old, ...this.versions(), 0) + 1;
    const tx = `${this.pid}-${nv - 1}-${hex(6)}`;
    this.ev(lane, "phase", `put(${fill}) -> v${nv}`, { detail: `Save ${fill} as a new copy-on-write version v${nv}; v${old} stays untouched.` });
    this.jlog(lane, { t: "BEGIN", tx, old, new: nv });
    this.maybeCrash(lane, "after_begin");
    const tmp = `.${vname(nv)}.${this.pid}.${hex(8)}.tmp`;
    this.create(lane, tmp, "tmp");
    if (this.crashAt === "torn_data") {
      this.write(lane, tmp, fill, 684, fill.repeat(10) + "··············");
      this.maybeCrash(lane, "torn_data");
    }
    this.write(lane, tmp, fill, 1368);
    this.maybeCrash(lane, "before_data_fsync");
    this.fsync(lane, tmp);
    this.rename(lane, tmp, vname(nv));
    this.fsyncDir(lane, "Makes the new version's directory entry durable.");
    this.maybeCrash(lane, "after_data_rename");
    const ctmp = `.CURRENT.${this.pid}.${hex(8)}.tmp`;
    this.create(lane, ctmp, "tmp");
    if (this.crashAt === "torn_pointer") {
      this.write(lane, ctmp, "", 45, `v${String(nv).padStart(6, "0")} sha2····`);
      this.maybeCrash(lane, "torn_pointer");
    }
    this.write(lane, ctmp, "", 91, `${vname(nv).slice(0, 7)} sha256=${hex(8)}`.slice(0, 24));
    this.fsync(lane, ctmp);
    this.maybeCrash(lane, "before_pointer_rename");
    this.rename(lane, ctmp, "CURRENT", true);
    this.maybeCrash(lane, "after_pointer_rename");
    if (opts.faultAfterCommit) {
      this.ev(lane, "fault", "fsync dir -> EIO (injected)", {
        op: "fsync_dir",
        detail: "The directory flush right after the commit rename failed. CURRENT may or may not be durable: the outcome is UNKNOWN.",
      });
      throw new Error("CommitUnknownFsync");
    }
    this.fsyncDir(lane, "Durable commit: the CURRENT rename survives power loss from here on.");
    this.journal.push({ t: "COMMIT", tx });
    this.ev(lane, "syscall", "append COMMIT to journal.wal", { op: "write", path: "journal.wal" });
    this.ev(lane, "syscall", "fsync journal.wal", { op: "fsync", path: "journal.wal" });
    this.maybeCrash(lane, "after_commit");
    const doomed = this.versions().filter((v) => v <= nv - (opts.keep ?? 3));
    for (const v of doomed) this.unlink(lane, vname(v), "GC: keep the newest 3 versions.");
    if (doomed.length) this.fsyncDir(lane);
    return nv;
  }

  /** atomicdocs recover(). Throws Crash at recovery crash points. */
  recover(lane: Lane = "recovery", title = "reopen -> recovery") {
    const rep = { rolled_forward: [] as string[], rolled_back: [] as string[], tmp_removed: 0, journal: this.journalStatus, fell_back: [] as unknown[], degraded: [] as unknown[], docs_checked: 1 };
    this.ev(lane, "phase", title, { detail: "Recovery trusts only bytes that verify: CURRENT if it parses and its target's SHA-256 checks out, else the newest valid version." });
    this.ev(lane, "syscall", "read journal.wal", { op: "read", path: "journal.wal" });
    const ended = new Set(this.journal.filter((j) => j.t !== "BEGIN").map((j) => j.tx));
    const open = this.journal.filter((j) => j.t === "BEGIN" && !ended.has(j.tx));
    let changed = false;
    for (const f of [...this.files.values()]) {
      if (f.name.endsWith(".tmp")) {
        this.unlink(lane, f.name, "Leftover temp file from the interrupted save.");
        rep.tmp_removed++;
        changed = true;
      }
    }
    if (changed) this.maybeCrash(lane, "recover_after_tmp_unlink");
    this.ev(lane, "syscall", "read CURRENT", { op: "read", path: "CURRENT" });
    const cur = this.current();
    let truth: number | null = null;
    if (cur?.ok && this.files.get(vname(cur.version))?.valid) truth = cur.version;
    else {
      const vs = this.versions().reverse();
      truth = vs.find((v) => this.files.get(vname(v))?.valid) ?? null;
    }
    if (truth) this.ev(lane, "syscall", `read ${vname(truth)} (SHA-256 ✓)`, { op: "read", path: vname(truth) });
    for (const v of this.versions()) {
      if (truth !== null && v > truth) {
        this.unlink(lane, vname(v), "Uncommitted version above the truth: rolled back.");
        changed = true;
        this.maybeCrash(lane, "recover_after_version_unlink");
      }
    }
    if (truth && !(cur?.ok && cur.version === truth)) {
      const ctmp = `.CURRENT.${this.pid}.${hex(8)}.tmp`;
      this.create(lane, ctmp, "tmp");
      this.write(lane, ctmp, "", 91, `${vname(truth).slice(0, 7)} sha256=${hex(8)}`.slice(0, 24));
      this.fsync(lane, ctmp);
      this.rename(lane, ctmp, "CURRENT");
      this.maybeCrash(lane, "recover_after_pointer_rewrite");
    }
    if (truth) {
      this.fsync(lane, vname(truth));
      this.fsync(lane, "CURRENT");
    }
    this.fsyncDir(lane);
    for (const t of open) (truth === t.new ? rep.rolled_forward : rep.rolled_back).push(this.doc);
    this.maybeCrash(lane, "recover_before_checkpoint");
    this.journal = [];
    this.journalStatus = "ok";
    this.ev(lane, "syscall", "checkpoint: fresh journal.wal (write + fsync + rename)", { op: "rename", path: "journal.wal" });
    return { ...rep, ms: Math.round(this.t * 10) / 10 };
  }

  fingerprint() {
    const s = this.snap();
    const raw = s.files.map((f) => `${f.name}:${f.size}:${f.fill ?? f.preview}`).join("|") + `|cur=${s.current?.version}`;
    let h = 2166136261;
    for (let i = 0; i < raw.length; i++) h = Math.imul(h ^ raw.charCodeAt(i), 16777619) >>> 0;
    let out = "";
    for (let i = 0; i < 4; i++) {
      h = Math.imul(h ^ (h >>> 13), 2246822519) >>> 0;
      out += h.toString(16).padStart(8, "0");
    }
    return "sha256:" + out;
  }

  result<X>(o: { checks: Check[]; summary: string; id: string; title: string; params: Record<string, unknown>; extra?: X }): ScenarioResult<X> {
    const pass = o.checks.every((c) => c.pass);
    this.ev("system", "result", o.summary, { ok: pass });
    return {
      id: o.id,
      title: o.title,
      params: o.params,
      events: this.events,
      checks: o.checks,
      pass,
      summary: o.summary,
      extra: o.extra,
      platform: { id: this.platform, label: this.platform === "linux" ? "Linux (simulated) · ext4" : "Windows 11 (simulated) · NTFS" },
      elapsedMs: Math.round(this.t * 10) / 10,
    };
  }
}

export const vfile = vname;
