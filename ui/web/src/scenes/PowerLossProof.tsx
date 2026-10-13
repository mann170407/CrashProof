import { motion } from "motion/react";
import { RefreshCw, ShieldCheck, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { getPowerLoss } from "../api";
import { DotGrid, Legend } from "../components/DotGrid";
import { CountUp } from "../components/CountUp";
import { Button, SceneTitle, Segmented, SourceBadge, Spinner, cx } from "../components/ui";
import { useBackend } from "../lib/backend";
import { useHotkeys } from "../lib/keys";
import { useLoad } from "../lib/useLoad";
import type { PowerLossSide } from "../types";
import type { SceneProps } from "./types";

export default function PowerLossProof({ active }: SceneProps) {
  const backend = useBackend();
  const { data, res, loading, reload } = useLoad("powerloss", backend, getPowerLoss);
  const [which, setWhich] = useState<"atomic" | "naive">("atomic");
  useHotkeys(active, { r: () => void reload(), t: () => setWhich((w) => (w === "atomic" ? "naive" : "atomic")) });

  return (
    <div className="flex h-full min-h-0 flex-col gap-4">
      <SceneTitle
        eyebrow="04 · Power-loss proof"
        title="Every possible power cut. Checked."
        sub="SimFS (ALICE-style) enumerates every disk state a power cut could leave at every system call (unsynced writes lost, 512 B sectors torn, renames not yet durable), then runs the real recovery and reads the document."
        right={
          <>
            {res && <SourceBadge source={res.source} backend={res.backend} error={res.error} />}
            <Button size="md" variant="ghost" onClick={() => void reload()} disabled={loading} icon={loading ? <Spinner /> : <RefreshCw className="h-4 w-4" />}>
              Re-run
            </Button>
          </>
        }
      />
      {!data ? (
        <div className="flex flex-1 items-center justify-center gap-3 text-[1.2rem] text-fg-2">
          <Spinner className="h-7 w-7" /> enumerating crash states…
        </div>
      ) : (
        <div className="grid min-h-0 flex-1 grid-cols-1 gap-5 xl:grid-cols-[1.25fr_1fr]">
          <div className="grid min-h-0 grid-rows-[auto_1fr] gap-4">
            <GridPanel title="Naive overwrite" bad side={data.naive} sample={data.sample.naive} />
            <GridPanel title="AtomicDocs" side={data.atomic} sample={data.sample.atomic} big />
          </div>
          <div className="flex min-h-0 flex-col gap-3 rounded-2xl border border-line bg-ink-900/70 p-5">
            <div className="flex items-center gap-3">
              <div className="text-[1.2rem] font-semibold">States per system call</div>
              <div className="ml-auto">
                <Segmented
                  layoutId="pl-which"
                  size="sm"
                  value={which}
                  onChange={setWhich}
                  options={[
                    { value: "atomic", label: "AtomicDocs" },
                    { value: "naive", label: "Naive" },
                  ]}
                />
              </div>
            </div>
            <Legend />
            <SyscallBars key={which} side={data[which]} />
          </div>
        </div>
      )}
    </div>
  );
}

function GridPanel({ title, side, sample, bad, big }: { title: string; side: PowerLossSide; sample: ("A" | "B" | "X")[]; bad?: boolean; big?: boolean }) {
  const t = side.totals;
  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
      className={cx("flex min-h-0 gap-6 rounded-2xl border bg-ink-900/70 p-5", bad ? "border-fail/40" : "border-pass/40")}
    >
      <div className="flex w-[17rem] shrink-0 flex-col justify-center">
        <div className="flex items-center gap-2 text-[1.35rem] font-semibold">
          {bad ? <TriangleAlert className="h-6 w-6 text-vx-text" /> : <ShieldCheck className="h-6 w-6 text-pass" />}
          {title}
        </div>
        <div className="mt-3 text-[3.2rem] font-bold leading-none tracking-tight">
          <CountUp value={t.states} />
          <span className="ml-2 text-[1rem] font-medium text-fg-3">distinct disk states</span>
        </div>
        <div className={cx("mt-2 text-[1.7rem] font-bold", t.corrupt ? "text-fail" : "text-pass")}>
          {t.corrupt ? "✗ " : "✓ "}
          <CountUp value={t.corrupt} /> corrupt
        </div>
        <div className="mt-1 text-[0.9rem] text-fg-2">
          A <b className="text-va-text">{t.A}</b> · B <b className="text-vb-text">{t.B}</b> · {sample.length} crash states checked
        </div>
      </div>
      <div className={cx("min-h-0 flex-1", big ? "min-h-[12rem]" : "h-[7rem]")}>
        <DotGrid sample={sample} dot={big ? 13 : 22} gap={big ? 4 : 7} maxCols={big ? 48 : 13} />
      </div>
    </motion.div>
  );
}

function SyscallBars({ side }: { side: PowerLossSide }) {
  const max = Math.max(1, ...side.perSyscall.map((p) => p.states));
  const label = (k: number) => side.syscalls.find((s) => s.k === k);
  return (
    <div className="scroll-quiet flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto pr-1">
      {side.perSyscall.map((p, i) => {
        const s = label(p.k);
        const commit = !!s?.commit;
        return (
          <motion.div
            key={p.k}
            initial={{ opacity: 0, x: -10 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: 0.1 + i * 0.05 }}
            className={cx("group grid grid-cols-[15rem_1fr_3rem] items-center gap-3 rounded-lg px-2 py-1", commit && "glow-commit bg-brand/15")}
            title={`after syscall #${p.k}: ${s?.label ?? ""}\n${p.states} states — A ${p.A}, B ${p.B}, corrupt ${p.corrupt}`}
          >
            <div className="min-w-0">
              <div className={cx("mono truncate text-[0.78rem]", commit ? "font-bold text-white" : "text-fg-2")}>{s?.label ?? `#${p.k}`}</div>
              {commit && <div className="mono text-[0.6rem] font-extrabold tracking-widest text-commit">COMMIT POINT</div>}
            </div>
            <div className="flex h-4 gap-[2px] overflow-hidden rounded-[4px]">
              {(["A", "B", "corrupt"] as const).map((k) =>
                p[k] > 0 ? (
                  <motion.div
                    key={k}
                    className={cx("h-full rounded-[3px]", k === "corrupt" && "hatch-x")}
                    style={{ background: k === "A" ? "#3b93f5" : k === "B" ? "#cc7e14" : undefined, outline: k === "corrupt" ? "1px solid #f0436a" : undefined }}
                    initial={{ width: 0 }}
                    animate={{ width: `${(p[k] / max) * 100}%` }}
                    transition={{ delay: 0.2 + i * 0.05, type: "spring", stiffness: 90, damping: 18 }}
                  />
                ) : null,
              )}
            </div>
            <div className="mono text-right text-[0.8rem] text-fg-2 tabular">{p.states}</div>
          </motion.div>
        );
      })}
      <div className="mt-2 text-[0.8rem] text-fg-3">
        Before the CURRENT rename every state recovers to A; from the rename on, to B. Nothing else exists.
      </div>
    </div>
  );
}
