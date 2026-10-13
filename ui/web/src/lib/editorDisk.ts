import { clearSceneState, createStore, getSceneState, lsGet, lsSet } from "./store";

/**
 * In-browser model of the AtomicDocs write path for the Live editor scene.
 * Each autosave walks the same steps as put(): write a temp copy, fsync it, rename it into place,
 * rename CURRENT (the commit point), fsync the directory. A simulated power cut stops the walk;
 * "recovery" keeps the new version only if the CURRENT rename already happened.
 * Nothing here touches the real disk except a localStorage copy of the last committed save.
 */

export interface Snapshot {
  text: string;
  cursor: [number, number];
}
export interface Committed extends Snapshot {
  v: number;
  at: number; // epoch ms
}
export interface Inflight extends Snapshot {
  v: number;
  step: number; // index into SAVE_STEPS currently running
}

export const SAVE_STEPS: { id: string; label: (v: number) => string; detail: string; ms: number }[] = [
  { id: "write", label: (v) => `write .v${pad(v)}.dat.tmp`, detail: "new copy, still in the page cache", ms: 520 },
  { id: "fsync", label: () => "fsync temp", detail: "new bytes are on disk", ms: 300 },
  { id: "rename_data", label: (v) => `rename → v${pad(v)}.dat · fsync dir`, detail: "durable copy, not yet current", ms: 300 },
  { id: "commit", label: () => "rename CURRENT", detail: "the single atomic switch", ms: 360 },
  { id: "durable", label: () => "fsync dir · durable", detail: "the rename itself is on disk", ms: 280 },
];
export const COMMIT_STEP = SAVE_STEPS.findIndex((s) => s.id === "commit");
export const KEEP = 3; // GC keeps the newest 3 versions, like the real store

export const pad = (v: number) => String(v).padStart(6, "0");

export interface DiskState {
  committed: Committed | null;
  versions: number[]; // durable vNNNNNN.dat files on disk
  inflight: Inflight | null;
  pending: Snapshot | null; // newer edit waiting for the in-flight save
  saves: number; // completed saves this session
}

const LS_KEY = "crashlab:editor:v1";

function load(): Committed | null {
  try {
    const raw = lsGet(LS_KEY);
    if (!raw) return null;
    const c = JSON.parse(raw) as Committed;
    return typeof c.text === "string" && typeof c.v === "number" ? c : null;
  } catch {
    return null;
  }
}

const initial = load();
export const diskStore = createStore<DiskState>({
  committed: initial,
  versions: initial ? [initial.v] : [],
  inflight: null,
  pending: null,
  saves: 0,
});

let timer = 0;

/** Presenter control: cut the power automatically halfway through this step of the next save (one-shot). */
export const armStore = createStore<number | null>(null);
export const armAt = (step: number | null) => armStore.set(step);

function runStep() {
  const s = diskStore.get();
  const f = s.inflight;
  if (!f) return;
  if (armStore.get() === f.step) {
    timer = window.setTimeout(() => {
      armStore.set(null);
      powerOff();
    }, SAVE_STEPS[f.step].ms / 2);
    return;
  }
  timer = window.setTimeout(() => {
    const cur = diskStore.get().inflight;
    if (!cur || cur.v !== f.v) return;
    const next = cur.step + 1;
    if (next < SAVE_STEPS.length) {
      diskStore.set((st) => ({
        ...st,
        inflight: { ...cur, step: next },
        versions: SAVE_STEPS[cur.step].id === "rename_data" ? [...st.versions, cur.v] : st.versions,
      }));
      runStep();
      return;
    }
    // finished: commit + GC
    const committed: Committed = { v: cur.v, text: cur.text, cursor: cur.cursor, at: Date.now() };
    lsSet(LS_KEY, JSON.stringify(committed));
    diskStore.set((st) => ({
      ...st,
      committed,
      inflight: null,
      versions: st.versions.filter((v) => v > cur.v - KEEP),
      saves: st.saves + 1,
    }));
    const p = diskStore.get().pending;
    if (p) {
      diskStore.set((st) => ({ ...st, pending: null }));
      save(p);
    }
  }, SAVE_STEPS[f.step].ms);
}

/** Autosave entry point: start a save now, or queue it behind the one in flight. */
export function save(snap: Snapshot) {
  const s = diskStore.get();
  if (s.inflight) {
    diskStore.set((st) => ({ ...st, pending: snap }));
    return;
  }
  if (s.committed && s.committed.text === snap.text && s.committed.cursor[0] === snap.cursor[0] && s.committed.cursor[1] === snap.cursor[1]) return;
  const v = Math.max(s.committed?.v ?? 0, ...s.versions) + 1;
  diskStore.set((st) => ({ ...st, inflight: { ...snap, v, step: 0 } }));
  runStep();
}

/** Wipe the simulated disk (presenter reset). */
export function wipeDisk() {
  clearTimeout(timer);
  lsSet(LS_KEY, "");
  diskStore.set({ committed: null, versions: [], inflight: null, pending: null, saves: 0 });
}

// ---------------------------------------------------------------------------------------------
// Simulated power cut. Nothing is shut down: the app's UI is unmounted and the screen goes black.
//   on -> closing (UI collapses) -> black (exactly BLACKOUT_MS) -> logo (Restart) -> booting
//   (recovery runs) -> on
// ---------------------------------------------------------------------------------------------

export const DRAFT_KEY = "editor:draft";
export const BLACKOUT_MS = 5000;
const CLOSING_MS = 550;

export type PowerPhase = "on" | "closing" | "black" | "logo" | "booting";
export interface Recovery {
  action: "rolled-back" | "rolled-forward" | "clean";
  restored: Committed | null;
  crashedV: number | null; // the save that was in flight, if any
  crashedStep: number | null; // SAVE_STEPS index it was running
  lostChars: number; // typed characters that never reached a commit
  log: string[]; // what recovery did, line by line
  at: number;
}
export interface PowerState {
  phase: PowerPhase;
  report: Recovery | null;
}
export const powerStore = createStore<PowerState>({ phase: "on", report: null });

let crashed: { inflight: Inflight | null; draft: string | null } | null = null;
let ptimer = 0;

export function powerOff() {
  if (powerStore.get().phase !== "on") return;
  clearTimeout(timer); // the save in flight dies where it is
  const s = diskStore.get();
  crashed = { inflight: s.inflight, draft: getSceneState<string | null>(DRAFT_KEY) ?? null };
  clearSceneState(DRAFT_KEY); // RAM is gone
  powerStore.set({ phase: "closing", report: null });
  ptimer = window.setTimeout(() => {
    powerStore.set((p) => ({ ...p, phase: "black" }));
    ptimer = window.setTimeout(() => powerStore.set((p) => ({ ...p, phase: "logo" })), BLACKOUT_MS);
  }, CLOSING_MS);
}

/** Run recovery on the simulated disk, show the boot screen briefly, then power the UI back on. */
export function restart(): Recovery {
  clearTimeout(ptimer);
  const s = diskStore.get();
  const f = crashed?.inflight ?? null;
  const log: string[] = ["mount · replay journal.wal"];
  let action: Recovery["action"] = "clean";
  let committed = s.committed;
  let versions = s.versions;
  if (f) {
    const id = SAVE_STEPS[f.step].id;
    if (f.step > COMMIT_STEP) {
      // CURRENT already points at the new version: finish the commit (roll forward)
      action = "rolled-forward";
      committed = { v: f.v, text: f.text, cursor: f.cursor, at: Date.now() };
      lsSet(LS_KEY, JSON.stringify(committed));
      versions = versions.filter((v) => v > f.v - KEEP);
      log.push(`CURRENT → v${pad(f.v)} · commit was done, completing it`);
    } else {
      action = "rolled-back";
      if (versions.includes(f.v)) {
        versions = versions.filter((v) => v !== f.v);
        log.push(`v${pad(f.v)}.dat never became CURRENT · removed`);
      } else {
        log.push(`.v${pad(f.v)}.dat.tmp ${id === "write" ? "half-written" : "uncommitted"} · removed`);
      }
      log.push(committed ? `CURRENT → v${pad(committed.v)} · unchanged` : "CURRENT missing · empty document");
    }
  } else {
    log.push(committed ? `CURRENT → v${pad(committed.v)} · no save was in flight` : "no document yet");
  }
  const draft = crashed?.draft;
  const lostChars = draft != null && draft !== committed?.text ? Math.max(0, draft.length - (committed?.text.length ?? 0)) : 0;
  if (committed) log.push(`open notes.txt · v${committed.v} · cursor at ${committed.cursor[0]}`);
  const report: Recovery = {
    action,
    restored: committed,
    crashedV: f?.v ?? null,
    crashedStep: f?.step ?? null,
    lostChars,
    log,
    at: Date.now(),
  };
  diskStore.set((st) => ({ ...st, committed, versions, inflight: null, pending: null }));
  crashed = null;
  powerStore.set({ phase: "booting", report });
  ptimer = window.setTimeout(() => powerStore.set((p) => ({ ...p, phase: "on" })), 1700);
  return report;
}

export function dismissReport() {
  powerStore.set((p) => (p.report ? { ...p, report: null } : p));
}
