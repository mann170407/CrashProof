import type { BackendId } from "./types";

/**
 * The ONE place backend URLs live.
 *
 *  windows: same origin "/api" — the Windows server (ui/server.py --port 8765) serves this UI,
 *           and in `npm run dev` / `npm run preview` Vite proxies /api -> http://127.0.0.1:8765.
 *  linux:   http://127.0.0.1:8766/api (WSL2 backend, CORS *). In dev/preview we go through the
 *           Vite proxy /api-linux -> http://127.0.0.1:8766/api so the browser stays same-origin.
 *
 * Overrides (no rebuild needed): ?windows=<url>&linux=<url> in the page URL, e.g.
 *   http://127.0.0.1:8765/?linux=http://192.168.1.20:8766/api
 * Build-time overrides: VITE_API_WINDOWS / VITE_API_LINUX.
 */
export interface BackendConfig {
  id: BackendId;
  name: string;
  apiBase: string;
  fixtures: string; // per-platform recorded fixtures
}

const env = import.meta.env as Record<string, string | boolean | undefined>;
const isViteServer = Boolean(env.DEV) || (typeof location !== "undefined" && (location.port === "5173" || location.port === "4173"));

function override(key: string): string | null {
  try {
    const q = new URLSearchParams(location.search).get(key);
    return q ? q.replace(/\/$/, "") : null;
  } catch {
    return null;
  }
}

export const BACKENDS: Record<BackendId, BackendConfig> = {
  windows: {
    id: "windows",
    name: "Windows",
    apiBase: override("windows") ?? (env.VITE_API_WINDOWS as string | undefined) ?? "/api",
    fixtures: "/fixtures/windows",
  },
  linux: {
    id: "linux",
    name: "Linux",
    apiBase:
      override("linux") ??
      (env.VITE_API_LINUX as string | undefined) ??
      (isViteServer ? "/api-linux" : "http://127.0.0.1:8766/api"),
    fixtures: "/fixtures/linux",
  },
};

export const BACKEND_ORDER: BackendId[] = ["linux", "windows"]; // compare: Linux left, Windows right

/** Timeouts: scenarios spawn real processes (C2 races up to 16), so they get generous budgets. */
export const TIMEOUT_QUICK_MS = 4000;
export const TIMEOUT_SCENARIO_MS = 90000;
export const STATUS_POLL_MS = 8000;
