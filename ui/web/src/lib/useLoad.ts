import { useCallback, useEffect } from "react";
import type { Fetched } from "../api";
import type { BackendId } from "../types";
import { useSceneState } from "./store";

/** Load once per (key, backend); cached across scene changes. reload() forces a refetch. */
export function useLoad<T>(key: string, backend: BackendId, fn: (b: BackendId) => Promise<Fetched<T>>, enabled = true) {
  const k = `${key}@${backend}`;
  const [st, setSt] = useSceneState<{ res: Fetched<T> | null; loading: boolean; n: number }>(k, { res: null, loading: false, n: 0 });
  const reload = useCallback(async () => {
    setSt((s) => ({ ...s, loading: true }));
    const res = await fn(backend);
    setSt((s) => ({ res, loading: false, n: s.n + 1 }));
  }, [backend, fn, setSt]);
  useEffect(() => {
    if (enabled && !st.res && !st.loading) void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [k, enabled]);
  return { data: st.res?.data ?? null, res: st.res, loading: st.loading, reload };
}
