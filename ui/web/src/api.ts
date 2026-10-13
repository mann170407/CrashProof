/**
 * Typed client for ui/CONTRACT.md with a three-step fallback, per backend:
 *   1. LIVE    — the backend's /api (real store code running now)
 *   2. REPLAY  — recorded fixtures: /fixtures/<platform>/<name>.json (then legacy /fixtures/<name>.json for windows)
 *   3. SIM     — the built-in offline simulation in src/mocks (clearly labelled, never shown as LIVE)
 */
import { BACKENDS, STATUS_POLL_MS, TIMEOUT_QUICK_MS, TIMEOUT_SCENARIO_MS } from "./config";
import { createStore, lsGet, lsSet } from "./lib/store";
import * as mocks from "./mocks";
import { NATIVE } from "./mocks/sim";
import type {
  BackendId, C1Extra, C2Extra, C3Extra, C4Extra, C5Extra, DataSource, Info, PlugExtra, PowerLoss, ScenarioResult,
  StoredResults, StreamDone, StreamEnd, StreamLine, StreamStart,
} from "./types";

export interface Fetched<T> {
  data: T;
  source: DataSource;
  backend: BackendId;
  error?: string;
}

// ---------------------------------------------------------------- backend selection + status
const LS_KEY = "crashlab.backend";
const initialBackend: BackendId = lsGet(LS_KEY) === "linux" ? "linux" : "windows";
export const backendStore = createStore<BackendId>(initialBackend);
backendStore.subscribe(() => lsSet(LS_KEY, backendStore.get()));

export type BackendStatus = "checking" | "live" | "replay" | "offline";
export interface BackendState {
  status: BackendStatus;
  info: Info | null;
  lastError?: string;
}
export const statusStore = createStore<Record<BackendId, BackendState>>({
  windows: { status: "checking", info: null },
  linux: { status: "checking", info: null },
});
function setStatus(b: BackendId, patch: Partial<BackendState>) {
  statusStore.set((s) => ({ ...s, [b]: { ...s[b], ...patch } }));
}

// ---------------------------------------------------------------- low level
async function fetchJSON<T>(url: string, init: RequestInit | undefined, timeoutMs: number): Promise<T> {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const res = await fetch(url, { ...init, signal: ctl.signal, cache: "no-store" });
    const text = await res.text();
    let body: unknown;
    try {
      body = JSON.parse(text);
    } catch {
      throw new Error(`${url}: not JSON (HTTP ${res.status})`);
    }
    if (!res.ok) {
      const msg = (body as { error?: string })?.error ?? `HTTP ${res.status}`;
      throw new Error(msg);
    }
    return body as T;
  } catch (e) {
    if ((e as Error).name === "AbortError") throw new Error(`timed out after ${timeoutMs / 1000}s`);
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

async function fixture<T>(b: BackendId, name: string): Promise<T | null> {
  const urls = [`${BACKENDS[b].fixtures}/${name}.json`];
  if (b === "windows") urls.push(`/fixtures/${name}.json`); // pre-addendum location
  for (const u of urls) {
    try {
      return await fetchJSON<T>(u, undefined, 4000);
    } catch {
      /* next */
    }
  }
  return null;
}

async function load<T>(
  b: BackendId,
  path: string,
  fixtureName: string,
  mock: () => T,
  opts: { method?: "GET" | "POST"; body?: unknown; timeout?: number; normalize?: (x: T) => T } = {},
): Promise<Fetched<T>> {
  const norm = opts.normalize ?? ((x: T) => x);
  const init: RequestInit | undefined =
    opts.method === "POST"
      ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(opts.body ?? {}) }
      : undefined;
  let error: string | undefined;
  try {
    const data = await fetchJSON<T>(BACKENDS[b].apiBase + path, init, opts.timeout ?? TIMEOUT_QUICK_MS);
    if (statusStore.get()[b].status !== "live") void pingBackend(b);
    return { data: norm(data), source: "live", backend: b };
  } catch (e) {
    error = (e as Error).message;
  }
  const fx = await fixture<T>(b, fixtureName);
  if (fx) return { data: norm(fx), source: "replay", backend: b, error };
  return { data: norm(mock()), source: "mock", backend: b, error };
}

// ---------------------------------------------------------------- normalisation
export function normalizeInfo(raw: Info, b: BackendId): Info {
  const p = raw.platform as unknown;
  if (p && typeof p === "object") {
    const pl = p as Info["platform"];
    return { ...raw, platform: { ...pl, native: pl.native ?? NATIVE[b], caveats: pl.caveats ?? [] } };
  }
  // pre-addendum server: platform was a plain string
  const s = String(p ?? b);
  const id = /win/i.test(s) ? "windows" : /linux/i.test(s) ? "linux" : b;
  return {
    ...raw,
    platform: { id, label: s, kernel: "", fs: "", python: raw.python ?? "", native: NATIVE[id as BackendId] ?? NATIVE[b], caveats: [] },
  };
}

function withNative<X>(b: BackendId) {
  return (r: ScenarioResult<X>): ScenarioResult<X> => {
    const native = statusStore.get()[b].info?.platform.native ?? NATIVE[b];
    const infoLabel = statusStore.get()[b].info?.platform.label;
    const platform = r.platform
      ? { ...r.platform, label: /�/.test(r.platform.label) ? infoLabel ?? r.platform.label.replace(/�/g, "·") : r.platform.label }
      : r.platform;
    return {
      ...r,
      platform,
      events: (r.events ?? []).map((e) => (e.kind === "syscall" && e.op && !e.native && native[e.op] ? { ...e, native: native[e.op] } : e)),
      checks: r.checks ?? [],
    };
  };
}

// ---------------------------------------------------------------- endpoints
export async function pingBackend(b: BackendId): Promise<void> {
  try {
    const raw = await fetchJSON<Info>(BACKENDS[b].apiBase + "/info", undefined, TIMEOUT_QUICK_MS);
    setStatus(b, { status: "live", info: normalizeInfo(raw, b), lastError: undefined });
    return;
  } catch (e) {
    const fx = await fixture<Info>(b, "info");
    if (fx) setStatus(b, { status: "replay", info: normalizeInfo(fx, b), lastError: (e as Error).message });
    else setStatus(b, { status: "offline", info: mocks.mockInfo(b), lastError: (e as Error).message });
  }
}

let polling = false;
export function startStatusPolling() {
  if (polling) return;
  polling = true;
  const tick = () => {
    (Object.keys(BACKENDS) as BackendId[]).forEach((b) => void pingBackend(b));
  };
  tick();
  setInterval(tick, STATUS_POLL_MS);
}

export const getInfo = (b: BackendId) =>
  load<Info>(b, "/info", "info", () => mocks.mockInfo(b), { normalize: (x) => normalizeInfo(x, b) });

export const getResults = (b: BackendId) => load<StoredResults>(b, "/results", "results", mocks.mockResults);

export const getPowerLoss = (b: BackendId) =>
  load<PowerLoss>(b, "/powerloss", "powerloss", mocks.mockPowerLoss, { timeout: TIMEOUT_SCENARIO_MS });

export const runPlug = (b: BackendId, crashAt: string | null, mode: "atomic" | "naive") =>
  load<ScenarioResult<PlugExtra>>(
    b,
    "/scenario/plug",
    mode === "naive" ? `plug-naive-${crashAt ?? "none"}` : `plug-${crashAt ?? "none"}`,
    () => mocks.mockPlug(b, crashAt, mode),
    { method: "POST", body: { crashAt, mode }, timeout: TIMEOUT_SCENARIO_MS, normalize: withNative(b) },
  );

export type ChallengeId = "c1" | "c2" | "c3" | "c4" | "c5";
export interface ChallengeExtra {
  c1: C1Extra;
  c2: C2Extra;
  c3: C3Extra;
  c4: C4Extra;
  c5: C5Extra;
}

export function runChallenge<K extends ChallengeId>(b: BackendId, id: K, body: Record<string, unknown> = {}) {
  const fx = id === "c5" ? `c5-${String(body.crashPoint ?? "recover_after_tmp_unlink")}` : id;
  const mock = (): ScenarioResult<ChallengeExtra[K]> => {
    switch (id) {
      case "c1": return mocks.mockC1(b) as unknown as ScenarioResult<ChallengeExtra[K]>;
      case "c2": return mocks.mockC2(b, Number(body.clients ?? 8)) as unknown as ScenarioResult<ChallengeExtra[K]>;
      case "c3": return mocks.mockC3(b) as unknown as ScenarioResult<ChallengeExtra[K]>;
      case "c4": return mocks.mockC4(b) as unknown as ScenarioResult<ChallengeExtra[K]>;
      default: return mocks.mockC5(b, String(body.crashPoint ?? "recover_after_tmp_unlink")) as unknown as ScenarioResult<ChallengeExtra[K]>;
    }
  };
  return load<ScenarioResult<ChallengeExtra[K]>>(b, `/scenario/${id}`, fx, mock, {
    method: "POST",
    body,
    timeout: TIMEOUT_SCENARIO_MS,
    normalize: withNative<ChallengeExtra[K]>(b),
  });
}

// ---------------------------------------------------------------- SSE test stream
export interface StreamHandlers {
  onStart(d: StreamStart): void;
  onLine(d: StreamLine): void;
  onEnd(d: StreamEnd): void;
  onDone(d: StreamDone): void;
  /** the stream could not be opened (backend not live) or dropped */
  onError(msg: string, receivedAny: boolean): void;
}

export function streamTests(b: BackendId, only: string[] | null, h: StreamHandlers): () => void {
  const q = only && only.length ? `?only=${encodeURIComponent(only.join(","))}` : "";
  let es: EventSource;
  try {
    es = new EventSource(`${BACKENDS[b].apiBase}/tests/stream${q}`);
  } catch (e) {
    h.onError((e as Error).message, false);
    return () => {};
  }
  let any = false;
  let finished = false;
  const parse = <T,>(ev: MessageEvent): T | null => {
    try {
      return JSON.parse(ev.data) as T;
    } catch {
      return null;
    }
  };
  es.addEventListener("start", (ev) => { any = true; const d = parse<StreamStart>(ev as MessageEvent); if (d) h.onStart(d); });
  es.addEventListener("line", (ev) => { any = true; const d = parse<StreamLine>(ev as MessageEvent); if (d) h.onLine(d); });
  es.addEventListener("end", (ev) => { any = true; const d = parse<StreamEnd>(ev as MessageEvent); if (d) h.onEnd(d); });
  es.addEventListener("done", (ev) => {
    finished = true;
    const d = parse<StreamDone>(ev as MessageEvent);
    if (d) h.onDone(d);
    es.close();
  });
  es.onerror = () => {
    if (finished) return;
    es.close(); // never let EventSource auto-reconnect: that would restart the whole suite
    h.onError(any ? "stream dropped" : "backend not reachable", any);
  };
  return () => {
    finished = true;
    es.close();
  };
}
