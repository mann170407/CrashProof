import { AnimatePresence, MotionConfig, motion } from "motion/react";
import { Maximize, LayoutGrid } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { SCENES } from "./scenes";
import { BackendSwitch } from "./components/BackendSwitch";
import { PowerCutOverlay } from "./components/PowerCut";
import { isTypingTarget } from "./lib/keys";
import { compareStore } from "./lib/backend";
import { startStatusPolling } from "./api";
import { cx } from "./components/ui";
import { CrashProofLogo } from "./components/Brand";
import { PowerScreen } from "./components/PowerScreen";
import { powerStore, restart } from "./lib/editorDisk";
import { useStore } from "./lib/store";

const KEY_TO_SCENE: Record<string, number> = { "1": 0, "2": 1, "3": 2, "4": 3, "5": 4, "6": 5, "7": 6, "8": 7, "9": 8, "0": 9, "-": 10, "=": 11 };

function sceneFromHash(): number {
  const m = /#\/?(\d+)/.exec(location.hash);
  const n = m ? parseInt(m[1], 10) - 1 : 0;
  return Math.max(0, Math.min(SCENES.length - 1, n));
}

export default function App() {
  const [idx, setIdx] = useState(sceneFromHash);
  const [dir, setDir] = useState(1);
  const [overview, setOverview] = useState(false);
  const power = useStore(powerStore);
  const on = power.phase === "on";

  useEffect(() => startStatusPolling(), []);

  const go = useCallback((n: number) => {
    setIdx((cur) => {
      const next = Math.max(0, Math.min(SCENES.length - 1, n));
      setDir(next >= cur ? 1 : -1);
      return next;
    });
    setOverview(false);
  }, []);

  useEffect(() => {
    const h = `#/${idx + 1}`;
    if (location.hash !== h) history.replaceState(null, "", h);
  }, [idx]);

  useEffect(() => {
    const onHash = () => go(sceneFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, [go]);

  useEffect(() => {
    if (!on) return; // the simulated machine is off: no navigation
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey || isTypingTarget(e.target)) return;
      const k = e.key;
      if (k === "ArrowRight" || k === "PageDown") {
        e.preventDefault();
        go(idx + 1);
      } else if (k === "ArrowLeft" || k === "PageUp") {
        e.preventDefault();
        go(idx - 1);
      } else if (k === "Home") go(0);
      else if (k === "End") go(SCENES.length - 1);
      else if (k in KEY_TO_SCENE) go(KEY_TO_SCENE[k]);
      else if (k === "f" || k === "F") {
        if (document.fullscreenElement) void document.exitFullscreen();
        else void document.documentElement.requestFullscreen?.().catch(() => {});
      } else if (k === "g" || k === "G" || (k === "Escape" && overview)) setOverview((o) => !o);
      else if ((k === "c" || k === "C")) compareStore.set(!compareStore.get());
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [idx, go, overview, on]);

  // Restart after the simulated power cut: recovery runs, then the UI comes back on the editor scene.
  const onRestart = useCallback(() => {
    restart();
    go(Math.max(0, SCENES.findIndex((s) => s.id === "editor")));
  }, [go]);

  const Scene = SCENES[idx].component;

  if (power.phase === "black" || power.phase === "logo" || power.phase === "booting")
    return (
      <MotionConfig reducedMotion="user">
        <PowerScreen power={power} onRestart={onRestart} />
      </MotionConfig>
    );

  return (
    <MotionConfig reducedMotion="user">
      <motion.div
        className="relative flex h-full w-full flex-col overflow-hidden bg-md-bg text-fg"
        // power off: the picture collapses to a line and goes out, like an old monitor
        initial={false}
        animate={power.phase === "closing" ? { scaleY: 0.004, scaleX: 0.7, opacity: 0.2, filter: "brightness(2.5)" } : { scaleY: 1, scaleX: 1, opacity: 1, filter: "brightness(1)" }}
        transition={power.phase === "closing" ? { duration: 0.5, ease: [0.7, 0, 0.84, 0] } : { duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
      >
        {/* progress rail */}
        <div className="absolute inset-x-0 top-0 z-40 h-[2px] bg-transparent">
          <motion.div
            className="h-full bg-md-fg/80"
            animate={{ width: `${((idx + 1) / SCENES.length) * 100}%` }}
            transition={{ type: "spring", stiffness: 120, damping: 24 }}
          />
        </div>

        {/* header */}
        <header className="relative z-30 flex h-[3.4rem] shrink-0 items-center gap-5 border-b border-md-line bg-md-bg px-6 font-hedvig">
          <button type="button" onClick={() => go(0)} className="flex items-center" title="Back to start (Home)">
            <CrashProofLogo />
          </button>
          <span className="h-5 w-px bg-md-line-2" />
          <span className="text-[0.8rem] text-md-3 tabular">
            {String(idx + 1).padStart(2, "0")} / {String(SCENES.length).padStart(2, "0")}
          </span>
          <span className="truncate text-[0.9rem] text-md-2">{SCENES[idx].title}</span>
          <div className="ml-auto flex items-center gap-2">
            <BackendSwitch />
            <span className="mx-1 h-5 w-px bg-md-line-2" />
            <button type="button" onClick={() => setOverview((o) => !o)} className="p-2 text-md-3 transition-colors hover:bg-white/[0.06] hover:text-md-fg" title="All scenes (G)">
              <LayoutGrid className="h-[1.1rem] w-[1.1rem]" strokeWidth={1.5} />
            </button>
            <button
              type="button"
              onClick={() => (document.fullscreenElement ? void document.exitFullscreen() : void document.documentElement.requestFullscreen?.().catch(() => {}))}
              className="p-2 text-md-3 transition-colors hover:bg-white/[0.06] hover:text-md-fg"
              title="Fullscreen (F)"
            >
              <Maximize className="h-[1.1rem] w-[1.1rem]" strokeWidth={1.5} />
            </button>
          </div>
        </header>

        {/* scene */}
        <main className="relative min-h-0 flex-1">
          <AnimatePresence initial={false} custom={dir} mode="popLayout">
            <motion.section
              key={idx}
              custom={dir}
              variants={{
                enter: (d: number) => ({ opacity: 0, x: d * 90 }),
                center: { opacity: 1, x: 0 },
                exit: (d: number) => ({ opacity: 0, x: d * -90 }),
              }}
              initial="enter"
              animate="center"
              exit="exit"
              transition={{ duration: 0.55, ease: [0.16, 1, 0.3, 1] }}
              className="scene-bg absolute inset-0 overflow-y-auto overflow-x-hidden px-[2.4rem] pb-[3.2rem] pt-[0.6rem] scroll-quiet md:overflow-hidden"
            >
              <Scene active={!overview} go={go} />
            </motion.section>
          </AnimatePresence>
        </main>

        {/* scene index */}
        <footer className="pointer-events-none absolute inset-x-0 bottom-0 z-30 flex h-[2.6rem] items-center px-6 font-hedvig">
          <span className="pointer-events-auto hidden text-[0.75rem] text-md-3 lg:block">Team Crashproof · OSH021 · KO-04</span>
          <nav className="pointer-events-auto absolute left-1/2 flex -translate-x-1/2 items-center gap-1.5" aria-label="Scenes">
            {SCENES.map((s, i) => (
              <button
                key={s.id}
                type="button"
                onClick={() => go(i)}
                title={`${i + 1}. ${s.title}`}
                className="group flex h-6 items-center"
                aria-current={i === idx}
              >
                <motion.span
                  layout
                  className={cx("block h-[3px] transition-colors", i === idx ? "bg-md-fg" : i < idx ? "bg-md-3" : "bg-md-line-2 group-hover:bg-md-3")}
                  animate={{ width: i === idx ? 28 : 12 }}
                />
              </button>
            ))}
          </nav>
          <span className="pointer-events-auto ml-auto hidden text-[0.72rem] text-md-3 xl:block">
            <kbd>←</kbd> <kbd>→</kbd> scenes · <kbd>1</kbd>–<kbd>0</kbd> <kbd>-</kbd> <kbd>=</kbd> jump · <kbd>G</kbd> overview · <kbd>F</kbd> fullscreen
          </span>
        </footer>

        <AnimatePresence>
          {overview && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="absolute inset-0 z-50 flex items-center justify-center bg-md-bg/90 p-10 font-hedvig backdrop-blur-md"
              onClick={() => setOverview(false)}
            >
              <div className="grid w-full max-w-[80rem] grid-cols-2 border-l border-t border-md-line md:grid-cols-4">
                {SCENES.map((s, i) => (
                  <motion.button
                    key={s.id}
                    type="button"
                    initial={{ opacity: 0, y: 20, scale: 0.96 }}
                    animate={{ opacity: 1, y: 0, scale: 1 }}
                    transition={{ delay: i * 0.03 }}
                    onClick={(e) => {
                      e.stopPropagation();
                      go(i);
                    }}
                    className={cx(
                      "flex flex-col items-start justify-start border-b border-r border-md-line bg-md-bg p-5 text-left transition-colors hover:bg-white/[0.04]",
                      i === idx && "bg-white/[0.06]",
                    )}
                  >
                    <div className="text-[0.8rem] text-md-3 tabular">
                      {i + 1 <= 9 ? i + 1 : i === 9 ? "0" : i === 10 ? "-" : i === 11 ? "=" : "→"} · {String(i + 1).padStart(2, "0")}
                    </div>
                    <div className="mt-2 font-serif text-[1.25rem] leading-tight text-md-fg">{s.title}</div>
                    <div className="mt-1 text-[0.85rem] text-md-3">{s.blurb}</div>
                  </motion.button>
                ))}
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        <PowerCutOverlay />
      </motion.div>
      {power.phase === "closing" && <div className="pointer-events-none fixed inset-0 -z-10 bg-black" />}
    </MotionConfig>
  );
}
