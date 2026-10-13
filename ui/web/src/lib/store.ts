import { useCallback, useSyncExternalStore } from "react";

/** Minimal global store (no dependency): get/set/subscribe + a React hook. */
export function createStore<T>(initial: T) {
  let state = initial;
  const subs = new Set<() => void>();
  return {
    get: () => state,
    set(next: T | ((prev: T) => T)) {
      const v = typeof next === "function" ? (next as (p: T) => T)(state) : next;
      if (Object.is(v, state)) return;
      state = v;
      subs.forEach((f) => f());
    },
    subscribe(f: () => void) {
      subs.add(f);
      return () => {
        subs.delete(f);
      };
    },
  };
}
export type Store<T> = ReturnType<typeof createStore<T>>;

export function useStore<T>(store: Store<T>): T {
  return useSyncExternalStore(store.subscribe, store.get, store.get);
}

// ---- per-key persistent scene state (survives navigating between scenes) ----
const sceneCache = createStore<Record<string, unknown>>({});

export function useSceneState<T>(key: string, initial: T): [T, (v: T | ((p: T) => T)) => void] {
  const all = useStore(sceneCache);
  const value = (key in all ? all[key] : initial) as T;
  const set = useCallback(
    (v: T | ((p: T) => T)) =>
      sceneCache.set((prev) => {
        const cur = (key in prev ? prev[key] : initial) as T;
        const next = typeof v === "function" ? (v as (p: T) => T)(cur) : v;
        return { ...prev, [key]: next };
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [key],
  );
  return [value, set];
}

/** Read / drop one scene-state key outside React (e.g. a simulated power cut wiping "RAM"). */
export function getSceneState<T>(key: string): T | undefined {
  return sceneCache.get()[key] as T | undefined;
}
export function clearSceneState(key: string) {
  sceneCache.set((prev) => {
    if (!(key in prev)) return prev;
    const next = { ...prev };
    delete next[key];
    return next;
  });
}

// ---- safe localStorage ----
export function lsGet(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}
export function lsSet(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* private mode / blocked storage: ignore */
  }
}
