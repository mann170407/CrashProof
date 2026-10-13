import { backendStore, statusStore } from "../api";
import { createStore, lsGet, lsSet, useStore } from "./store";
import type { BackendId } from "../types";

export function useBackend(): BackendId {
  return useStore(backendStore);
}
export function setBackend(b: BackendId) {
  backendStore.set(b);
}
export function useBackendStatus() {
  return useStore(statusStore);
}

/** global "Compare both kernels" toggle (persisted) */
export const compareStore = createStore<boolean>(lsGet("crashlab.compare") === "1");
compareStore.subscribe(() => lsSet("crashlab.compare", compareStore.get() ? "1" : "0"));
export function useCompare(): [boolean, (v: boolean) => void] {
  const v = useStore(compareStore);
  return [v, (x: boolean) => compareStore.set(x)];
}
