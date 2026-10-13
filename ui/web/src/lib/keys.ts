import { useEffect, useRef } from "react";

export function isTypingTarget(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  if (!el) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable;
}

/**
 * Scene-local hotkeys (only while `enabled`). Keys are KeyboardEvent.key values, lower-cased
 * for letters (" " for Space). Navigation keys (arrows, PageUp/Down, digits, F) belong to the shell.
 */
export function useHotkeys(enabled: boolean, map: Record<string, (e: KeyboardEvent) => void>) {
  const ref = useRef(map);
  ref.current = map;
  useEffect(() => {
    if (!enabled) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey || isTypingTarget(e.target)) return;
      const k = e.key.length === 1 ? e.key.toLowerCase() : e.key;
      const f = ref.current[k];
      if (f) {
        e.preventDefault();
        f(e);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [enabled]);
}
