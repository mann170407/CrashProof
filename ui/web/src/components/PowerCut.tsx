import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { useEffect, useState } from "react";
import { createStore, useStore } from "../lib/store";

/** Global "power cut" flash. cutPower() from anywhere; one overlay renders it. */
const cutStore = createStore<{ n: number; kind: "crash" | "fault" }>({ n: 0, kind: "crash" });
let last = 0;
export function cutPower(kind: "crash" | "fault" = "crash") {
  const now = performance.now();
  if (now - last < 400) return; // compare mode: both sides crash at once -> one flash
  last = now;
  cutStore.set((s) => ({ n: s.n + 1, kind }));
}

export function PowerCutOverlay() {
  const { n, kind } = useStore(cutStore);
  const reduce = useReducedMotion();
  const [show, setShow] = useState(0);
  useEffect(() => {
    if (!n) return;
    setShow(n);
    const t = setTimeout(() => setShow(0), reduce ? 350 : kind === "crash" ? 1100 : 700);
    return () => clearTimeout(t);
  }, [n, kind, reduce]);

  return (
    <AnimatePresence>
      {show > 0 && (
        <motion.div
          key={show}
          className="pointer-events-none fixed inset-0 z-[100]"
          initial={{ opacity: 1 }}
          exit={{ opacity: 0, transition: { duration: 0.35 } }}
        >
          {reduce ? (
            <div className="absolute inset-0 bg-black/70" />
          ) : kind === "crash" ? (
            <>
              {/* the bulb pops: white flash, then the screen goes dark and flickers back */}
              <motion.div
                className="absolute inset-0 bg-white"
                initial={{ opacity: 0.95 }}
                animate={{ opacity: 0 }}
                transition={{ duration: 0.12, ease: "easeOut" }}
              />
              <motion.div
                className="absolute inset-0 bg-black"
                initial={{ opacity: 0 }}
                animate={{ opacity: [0, 0.96, 0.96, 0.6, 0.9, 0] }}
                transition={{ duration: 1.05, times: [0, 0.1, 0.55, 0.65, 0.75, 1], ease: "linear" }}
              />
              <motion.div
                className="absolute inset-0 flex items-center justify-center"
                initial={{ opacity: 0, scale: 1.4 }}
                animate={{ opacity: [0, 1, 1, 0], scale: [1.4, 1, 1, 0.98] }}
                transition={{ duration: 1.0, times: [0, 0.15, 0.7, 1] }}
              >
                <div className="mono rounded-xl border-4 border-[#ff5d73] px-10 py-3 text-[5rem] font-extrabold tracking-[0.2em] text-[#ff5d73] [text-shadow:0_0_40px_rgba(255,93,115,0.8)]">
                  POWER CUT
                </div>
              </motion.div>
            </>
          ) : (
            <motion.div
              className="absolute inset-0"
              style={{ background: "radial-gradient(circle at 50% 50%, rgba(255,181,71,0.0), rgba(255,93,115,0.35))" }}
              initial={{ opacity: 0 }}
              animate={{ opacity: [0, 1, 0.3, 0.8, 0] }}
              transition={{ duration: 0.7 }}
            />
          )}
        </motion.div>
      )}
    </AnimatePresence>
  );
}
