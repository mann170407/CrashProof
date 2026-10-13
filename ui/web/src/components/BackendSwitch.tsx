import { motion } from "motion/react";
import { BACKEND_ORDER, BACKENDS } from "../config";
import { setBackend, useBackend, useBackendStatus } from "../lib/backend";
import { OsMark, StatusDot, cx } from "./ui";

/** Global Windows | Linux switch with each backend's own LIVE / REPLAY / OFFLINE status. */
export function BackendSwitch() {
  const backend = useBackend();
  const status = useBackendStatus();
  const label = status[backend].info?.platform.label;
  return (
    <div className="flex items-center gap-3">
      {label && <span className="hidden max-w-[22rem] truncate text-[0.8rem] text-md-3 2xl:block" title={label}>{label}</span>}
      <div className="flex border border-md-line" role="radiogroup" aria-label="Backend">
        {BACKEND_ORDER.map((b) => {
          const on = b === backend;
          const st = status[b];
          return (
            <button
              key={b}
              type="button"
              role="radio"
              aria-checked={on}
              onClick={() => setBackend(b)}
              title={`${st.info?.platform.label ?? BACKENDS[b].name} — ${st.status.toUpperCase()}${st.lastError && st.status !== "live" ? ` (${st.lastError})` : ""}\nAPI: ${BACKENDS[b].apiBase}`}
              className={cx("relative flex items-center gap-2 px-3 py-1.5 text-[0.85rem] transition-colors", on ? "text-md-fg" : "text-md-3 hover:text-md-2")}
            >
              {on && (
                <motion.span
                  layoutId="backend-pill"
                  className="absolute inset-0 bg-white/[0.08]"
                  transition={{ type: "spring", stiffness: 500, damping: 38 }}
                />
              )}
              <span className="relative z-10 flex items-center gap-2">
                <OsMark id={b} className="h-4 w-4" />
                {BACKENDS[b].name}
                <StatusDot status={st.status} label={on} />
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
