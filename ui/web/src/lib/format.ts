import type { DiskSnapshot, LabEvent, ScenarioResult } from "../types";

/** Fill letter -> identity colours. A/B/C are categorical; anything unknown is neutral. */
export function letterColor(fill: string | null | undefined): { mark: string; text: string; bg: string } {
  switch (fill) {
    case "A": return { mark: "var(--color-va)", text: "var(--color-va-text)", bg: "rgb(59 147 245 / 0.14)" };
    case "B": return { mark: "var(--color-vb)", text: "var(--color-vb-text)", bg: "rgb(204 126 20 / 0.16)" };
    case "C": return { mark: "var(--color-vc)", text: "var(--color-vc-text)", bg: "rgb(192 101 240 / 0.15)" };
    case "X": return { mark: "var(--color-vx)", text: "var(--color-vx-text)", bg: "rgb(240 67 106 / 0.15)" };
    default: return { mark: "var(--color-fg-3)", text: "var(--color-fg)", bg: "rgb(255 255 255 / 0.05)" };
  }
}

const CLIENT_HUES = ["#8b7bff", "#f472b6", "#38bdf8", "#a3e635", "#fb923c", "#22d3ee", "#e879f9", "#facc15"];

export function laneColor(lane: string): string {
  if (lane === "writer") return "var(--color-brand)";
  if (lane === "recovery") return "var(--color-recover)";
  if (lane === "reader") return "var(--color-reader)";
  if (lane === "system") return "var(--color-fg-3)";
  const m = /^client(\d+)$/.exec(lane);
  if (m) return CLIENT_HUES[(parseInt(m[1], 10) - 1) % CLIENT_HUES.length];
  return "var(--color-fg-2)";
}

export function laneLabel(lane: string): string {
  const m = /^client(\d+)$/.exec(lane);
  if (m) return `client ${m[1]}`;
  return lane;
}

/** ordered lanes for a list of events */
export function lanesOf(events: LabEvent[]): string[] {
  const order = (l: string) =>
    l === "writer" ? 0 : l.startsWith("client") ? 1 + parseInt(l.slice(6), 10) / 100 : l === "reader" ? 2 : l === "recovery" ? 3 : 9;
  const set = new Set(events.filter((e) => !isSpanning(e)).map((e) => e.lane as string));
  return [...set].sort((a, b) => order(a) - order(b));
}

/** events drawn across all lanes */
export function isSpanning(e: LabEvent): boolean {
  return e.kind === "phase" || e.kind === "crash" || e.kind === "result" || (e.lane === "system" && e.kind !== "syscall");
}

/** the disk as it was right after event `idx` (walk back to the last event carrying a snapshot) */
export function snapshotAt(events: LabEvent[], idx: number): DiskSnapshot | null {
  for (let i = Math.min(idx, events.length - 1); i >= 0; i--) if (events[i].disk) return events[i].disk!;
  // before the first event: first snapshot available (the starting state)
  return events.find((e) => e.disk)?.disk ?? null;
}

export interface CrashState {
  crashed: boolean;      // a crash happened and recovery has not started yet
  crashIdx: number;
  recovering: boolean;   // the current event is a recovery-lane event
  faulted: boolean;      // a fault event has happened at or before idx
}
export function crashStateAt(events: LabEvent[], idx: number): CrashState {
  let crashIdx = -1;
  let faulted = false;
  for (let i = 0; i <= idx && i < events.length; i++) {
    if (events[i].kind === "crash") crashIdx = i;
    if (events[i].kind === "fault") faulted = true;
  }
  let crashed = crashIdx >= 0;
  if (crashed) for (let i = crashIdx + 1; i <= idx && i < events.length; i++) if (events[i].lane === "recovery" || events[i].kind === "phase") crashed = false;
  const cur = events[Math.max(0, Math.min(idx, events.length - 1))];
  return { crashed, crashIdx, recovering: !!cur && cur.lane === "recovery", faulted };
}

/** comparable outcome of a scenario, for the "same result on both kernels" row */
export function outcomeKey(r: ScenarioResult<unknown>): string {
  const ex = (r.extra ?? {}) as Record<string, unknown>;
  if (typeof ex.outcome === "string") return ex.outcome;
  if (Array.isArray(ex.runs)) return (ex.runs as { got: string }[]).map((x) => x.got).join("/");
  if (typeof ex.afterRecovery === "string") return ex.afterRecovery;
  const last = [...r.events].reverse().find((e) => e.disk)?.disk;
  return `${r.pass ? "pass" : "fail"}:${last?.readableFill ?? last?.readable ?? "∅"}`;
}

export function fmtMs(ms: number | undefined | null): string {
  if (ms == null || !isFinite(ms)) return "—";
  if (ms < 1) return `${ms.toFixed(2)} ms`;
  if (ms < 100) return `${ms.toFixed(1)} ms`;
  if (ms < 10000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

export const prettyPoint = (p: string | null) => (p ? p.replace(/^recover_/, "").replace(/_/g, " ") : "no crash");
