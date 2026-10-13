import { AnimatePresence, motion } from "motion/react";
import { RotateCw } from "lucide-react";
import { useEffect } from "react";
import { CrashProofLogo } from "./Brand";
import type { PowerState } from "../lib/editorDisk";

/**
 * What the screen shows while the simulated machine is off: pure black for exactly 5 s,
 * then the Crash Proof logo with a Restart button, then a short boot log while recovery runs.
 */
export function PowerScreen({ power, onRestart }: { power: PowerState; onRestart: () => void }) {
  const { phase, report } = power;

  useEffect(() => {
    if (phase !== "logo") return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        onRestart();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [phase, onRestart]);

  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center bg-black font-hedvig text-md-fg" style={{ cursor: phase === "black" ? "none" : undefined }}>
      <AnimatePresence>
        {phase !== "black" && (
          <motion.div
            key="logo"
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.9, ease: [0.16, 1, 0.3, 1] }}
            className="flex flex-col items-center"
          >
            <CrashProofLogo size="lg" />
            <div className="mt-10 flex h-[8.5rem] flex-col items-center">
              <AnimatePresence mode="wait">
                {phase === "logo" ? (
                  <motion.div
                    key="restart"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1, transition: { delay: 0.5, duration: 0.6 } }}
                    exit={{ opacity: 0, transition: { duration: 0.2 } }}
                    className="flex flex-col items-center gap-5"
                  >
                    <button
                      type="button"
                      autoFocus
                      onClick={onRestart}
                      className="flex h-12 items-center gap-2.5 bg-md-fg px-7 text-[1rem] text-md-bg transition-colors hover:bg-white"
                    >
                      <RotateCw className="h-4 w-4" strokeWidth={2} /> Restart
                    </button>
                    <div className="text-[0.85rem] text-md-3">Simulated power loss · the app was closed, your computer stayed on</div>
                  </motion.div>
                ) : (
                  <motion.div key="boot" initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="mono flex flex-col items-start gap-1.5 text-[0.85rem] text-md-2">
                    {(report?.log ?? []).map((l, i) => (
                      <motion.div key={l} initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: 0.15 + i * 0.28 }}>
                        <span className="text-md-3">recovery ›</span> {l}
                      </motion.div>
                    ))}
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
