import { motion } from "motion/react";
import { ArrowRight } from "lucide-react";
import { getResults } from "../api";
import { CountUp } from "../components/CountUp";
import { SectorField } from "../components/SectorField";
import { SourceBadge } from "../components/ui";
import { useBackend } from "../lib/backend";
import { headline } from "../lib/results";
import { useLoad } from "../lib/useLoad";
import type { SceneProps } from "./types";

const ease = [0.16, 1, 0.3, 1] as const;

export default function Hero({ go }: SceneProps) {
  const backend = useBackend();
  const { data, res } = useLoad("results", backend, getResults);
  const h = headline(data);
  const stats = [
    { v: h.states, label: "distinct power-loss states checked", color: "var(--color-fg)" },
    { v: h.corrupt, label: "corrupted documents", color: "var(--color-pass)", strong: true },
    { v: h.kills, label: "real process kills, every crash point", color: "var(--color-fg)" },
    { v: h.faultsTotal, label: `fault scenarios (${h.faultsPassed}/${h.faultsTotal} pass)`, color: "var(--color-fg)" },
    { v: h.checksTotal, label: `live-challenge checks (${h.challengesPassed}/${h.challengesTotal} challenges)`, color: "var(--color-fg)" },
  ];
  const words1 = ["Pull", "the", "plug."];
  const words2 = ["Lose", "nothing."];
  return (
    <div className="relative flex h-full min-h-[40rem] flex-col justify-center">
      <SectorField className="pointer-events-none absolute -inset-x-[2.4rem] -bottom-[3.2rem] -top-[0.6rem] h-[calc(100%+3.8rem)] w-[calc(100%+4.8rem)] opacity-90" />
      <div className="relative">
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, ease }}
          className="mb-6 inline-flex items-center gap-3 border border-line bg-ink-900/70 px-4 py-1.5 text-[0.8rem] uppercase tracking-[0.2em] text-fg-2"
        >
          <span className="h-1.5 w-1.5 bg-fg" />
          KERNEL OPS · KO-04 · OSH021
        </motion.div>
        <h1 className="font-serif text-[7.6rem] font-normal leading-[1] tracking-[-0.02em]">
          <span className="block">
            {words1.map((w, i) => (
              <motion.span key={w} className="mr-[0.22em] inline-block" initial={{ opacity: 0, y: 40, filter: "blur(12px)" }} animate={{ opacity: 1, y: 0, filter: "blur(0px)" }} transition={{ duration: 0.9, delay: 0.15 + i * 0.09, ease }}>
                {w}
              </motion.span>
            ))}
          </span>
          <span className="block">
            {words2.map((w, i) => (
              <motion.span
                key={w}
                className="mr-[0.22em] inline-block pb-[0.08em] text-md-3"
                initial={{ opacity: 0, y: 40, filter: "blur(12px)" }}
                animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
                transition={{ duration: 0.9, delay: 0.5 + i * 0.1, ease }}
              >
                {w}
              </motion.span>
            ))}
          </span>
        </h1>
        <motion.p
          initial={{ opacity: 0, y: 14 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, delay: 0.8, ease }}
          className="mt-7 max-w-[60rem] text-[1.45rem] leading-snug text-fg-2"
        >
          <span className="font-semibold text-fg">AtomicDocs</span> — a crash-consistent document store. Cut power at any instant during a save and
          every document is exactly its old version or its new one. Never a mixture. Never lost once acknowledged.
        </motion.p>

        <div className="mt-12 grid max-w-[92rem] grid-cols-2 gap-4 md:grid-cols-5">
          {stats.map((s, i) => (
            <motion.div
              key={s.label}
              initial={{ opacity: 0, y: 24 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.7, delay: 1.0 + i * 0.1, ease }}
              className={`border bg-ink-900/75 px-5 py-4 backdrop-blur ${s.strong ? "border-pass/40" : "border-line"}`}
            >
              <div className="font-serif text-[3.6rem] font-normal leading-none" style={{ color: s.color }}>
                <CountUp value={s.v} delay={1.1 + i * 0.1} />
              </div>
              <div className="mt-2 text-[0.95rem] leading-snug text-fg-2">{s.label}</div>
            </motion.div>
          ))}
        </div>

        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 1.7, duration: 0.8 }}
          className="mt-10 flex flex-wrap items-center gap-x-8 gap-y-3"
        >
          <div className="text-[1.15rem]">
            <span className="text-fg-3">Team Crashproof — </span>
            <span className="font-semibold">Mann Sharma</span>
            <span className="text-fg-3"> · </span>
            <span className="font-semibold">Adarsh Pillai</span>
            <span className="text-fg-3"> · </span>
            <span className="font-semibold">Purumalla Shreeyans</span>
          </div>
          {res && <SourceBadge source={res.source} backend={res.backend} error={res.error} />}
          <button type="button" onClick={() => go(1)} className="ml-auto inline-flex items-center gap-2 text-[1rem] text-fg-2 hover:text-fg">
            Press <kbd>→</kbd> to begin <ArrowRight className="h-4 w-4" />
          </button>
        </motion.div>
      </div>
    </div>
  );
}
