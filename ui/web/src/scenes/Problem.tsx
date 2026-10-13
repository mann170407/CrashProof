import { AnimatePresence, LayoutGroup, motion } from "motion/react";
import { Zap, Play, RotateCcw, ShieldCheck, TriangleAlert } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { getInfo, runPlug, type Fetched } from "../api";
import { useLoad } from "../lib/useLoad";
import { cutPower } from "../components/PowerCut";
import { Button, SceneTitle, SourceBadge, Spinner, cx } from "../components/ui";
import { useBackend } from "../lib/backend";
import { useHotkeys } from "../lib/keys";
import { letterColor, prettyPoint } from "../lib/format";
import type { PlugExtra, ScenarioResult } from "../types";
import type { SceneProps } from "./types";

const DURATION = 8000; // ms for one whole save, slow enough for a human to hit the button
const N = 16; // sectors drawn per file

type Phase = "idle" | "saving" | "cut" | "done";

/** atomic write path on the same clock; `crash` = the store crash point that matches this moment */
const ATOMIC: { from: number; to: number; label: string; crash: string }[] = [
  { from: 0.0, to: 0.07, label: "BEGIN → journal + fsync", crash: "after_begin" },
  { from: 0.07, to: 0.4, label: "write new copy (temp file)", crash: "torn_data" },
  { from: 0.4, to: 0.47, label: "fsync temp", crash: "before_data_fsync" },
  { from: 0.47, to: 0.56, label: "rename → v000002.dat, fsync dir", crash: "after_data_rename" },
  { from: 0.56, to: 0.64, label: "write CURRENT temp", crash: "torn_pointer" },
  { from: 0.64, to: 0.72, label: "fsync CURRENT temp", crash: "before_pointer_rename" },
  { from: 0.72, to: 0.85, label: "RENAME CURRENT — commit point", crash: "after_pointer_rename" },
  { from: 0.85, to: 1.0, label: "COMMIT record + GC", crash: "after_commit" },
];
const stageAt = (p: number) => ATOMIC.findIndex((s) => p >= s.from && p < s.to);
const clamp01 = (x: number) => Math.max(0, Math.min(1, x));

export default function Problem({ active }: SceneProps) {
  const backend = useBackend();
  const { data: info } = useLoad("info", backend, getInfo);
  const [phase, setPhase] = useState<Phase>("idle");
  const [p, setP] = useState(0);
  const [naive, setNaive] = useState<Fetched<ScenarioResult<PlugExtra>> | null>(null);
  const [atomic, setAtomic] = useState<Fetched<ScenarioResult<PlugExtra>> | null>(null);
  const [loading, setLoading] = useState(false);
  const raf = useRef(0);
  const t0 = useRef(0);
  const token = useRef(0); // ignore results of a run that was reset meanwhile

  useEffect(() => () => cancelAnimationFrame(raf.current), []);

  const start = () => {
    token.current++;
    setNaive(null);
    setAtomic(null);
    setP(0);
    setPhase("saving");
    t0.current = performance.now();
    const tick = (now: number) => {
      const k = clamp01((now - t0.current) / DURATION);
      setP(k);
      if (k >= 1) {
        setPhase("done");
        void fetchBoth(1);
        return;
      }
      raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);
  };

  const fetchBoth = async (at: number) => {
    const my = token.current;
    setLoading(true);
    // naive writer: O_TRUNC happens at t=0, sectors of B are written until 90% of the clock
    const naivePts = info?.naiveCrashPoints ?? ["torn_data"];
    const nothingWritten = Math.floor(clamp01(at / 0.9) * N) === 0;
    const naiveCrash = at >= 0.95 ? null : nothingWritten && naivePts.includes("after_trunc") ? "after_trunc" : "torn_data";
    const atomicCrash = at >= 1 ? null : ATOMIC[Math.max(0, stageAt(at))].crash;
    const [n, a] = await Promise.all([runPlug(backend, naiveCrash, "naive"), runPlug(backend, atomicCrash, "atomic")]);
    if (my !== token.current) return;
    setNaive(n);
    setAtomic(a);
    setLoading(false);
  };

  const cut = () => {
    if (phase !== "saving") return;
    cancelAnimationFrame(raf.current);
    setPhase("cut");
    cutPower("crash");
    void fetchBoth(p);
  };

  const reset = () => {
    token.current++;
    setLoading(false);
    cancelAnimationFrame(raf.current);
    setPhase("idle");
    setP(0);
    setNaive(null);
    setAtomic(null);
  };

  useHotkeys(active, {
    " ": () => (phase === "saving" ? cut() : phase === "idle" ? start() : reset()),
    s: () => (phase === "idle" ? start() : undefined),
    x: () => cut(),
    r: () => reset(),
  });

  const stage = phase === "idle" ? -1 : p >= 1 ? ATOMIC.length : stageAt(p);
  const frozen = phase === "cut";

  return (
    <div className="flex h-full min-h-0 flex-col gap-5">
      <SceneTitle
        eyebrow="01 · The problem"
        title={<>Saving a file is not one step.</>}
        sub="Overwrite a document in place and there is a window where neither the old nor the new version exists on disk. Start a save of B over A, then cut the power whenever you like."
      />

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-5 lg:grid-cols-2">
        {/* ---------- NAIVE ---------- */}
        <Side
          title="Naive save"
          code={'fd = open("report", O_WRONLY | O_TRUNC)\nwrite(fd, B, 64)'}
          tone="bad"
          result={naive}
          loading={loading && !naive}
          frozen={frozen}
        >
          <NaiveFile p={p} phase={phase} />
        </Side>

        {/* ---------- ATOMIC ---------- */}
        <Side
          title="AtomicDocs put()"
          code={"copy-on-write v2  →  fsync  →  rename(CURRENT)\n(the rename is the commit point)"}
          tone="good"
          result={atomic}
          loading={loading && !atomic}
          frozen={frozen}
        >
          <AtomicFiles p={p} phase={phase} stage={stage} />
        </Side>
      </div>

      {/* controls */}
      <div className="flex items-center justify-center gap-6">
        <AnimatePresence mode="wait">
          {phase === "idle" && (
            <motion.div key="start" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -10 }}>
              <Button variant="primary" size="xl" onClick={start} icon={<Play className="h-7 w-7" />}>
                Save B over A
              </Button>
            </motion.div>
          )}
          {phase === "saving" && (
            <motion.div key="cut" initial={{ opacity: 0, scale: 0.8 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0, scale: 1.2 }}>
              <motion.div animate={{ scale: [1, 1.05, 1] }} transition={{ duration: 0.9, repeat: Infinity }}>
                <Button variant="danger" size="xl" onClick={cut} icon={<Zap className="h-8 w-8" />} className="min-w-[22rem] text-[1.9rem] tracking-wide">
                  CUT POWER
                </Button>
              </motion.div>
            </motion.div>
          )}
          {(phase === "cut" || phase === "done") && (
            <motion.div key="reset" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="flex items-center gap-5">
              <div className="text-[1.15rem] text-fg-2">
                {phase === "cut" ? (
                  <>
                    Power cut at <span className="mono font-bold text-fg">{Math.round(p * 100)}%</span> of the save — store crash point{" "}
                    <span className="mono font-bold text-commit">{prettyPoint(ATOMIC[Math.max(0, stageAt(p))].crash)}</span>
                  </>
                ) : (
                  <>The save finished — this time. Try again and cut the power mid-save.</>
                )}
              </div>
              <Button variant="ghost" size="lg" onClick={reset} icon={<RotateCcw className="h-5 w-5" />}>
                Again
              </Button>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
      <div className="-mt-3 text-center text-[0.8rem] text-fg-3">
        <kbd>Space</kbd> save / cut / again · animation is paced for humans; the outcome comes from a real run of the store at that crash point
      </div>
    </div>
  );
}

function Side({
  title, code, tone, children, result, loading, frozen,
}: {
  title: string; code: string; tone: "good" | "bad"; children: React.ReactNode;
  result: Fetched<ScenarioResult<PlugExtra>> | null; loading: boolean; frozen: boolean;
}) {
  return (
    <motion.div
      animate={frozen ? { x: [0, -8, 7, -4, 2, 0] } : { x: 0 }}
      transition={{ duration: 0.45 }}
      className={cx("relative flex min-h-0 flex-col gap-4 rounded-3xl border bg-ink-900/70 p-6", tone === "bad" ? "border-line" : "border-line")}
    >
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3 text-[1.6rem] font-semibold tracking-tight">
            {tone === "bad" ? <TriangleAlert className="h-7 w-7 text-vx-text" /> : <ShieldCheck className="h-7 w-7 text-pass" />}
            {title}
          </div>
          <pre className="mono mt-2 whitespace-pre-wrap text-[0.85rem] leading-relaxed text-fg-3">{code}</pre>
        </div>
        {result && <SourceBadge source={result.source} backend={result.backend} error={result.error} />}
      </div>
      <div className="flex min-h-0 flex-1 flex-col justify-center">{children}</div>
      <Outcome result={result} loading={loading} />
    </motion.div>
  );
}

function Outcome({ result, loading }: { result: Fetched<ScenarioResult<PlugExtra>> | null; loading: boolean }) {
  if (loading)
    return (
      <div className="flex h-[5.5rem] items-center gap-3 rounded-2xl border border-line bg-ink-950/50 px-5 text-fg-2">
        <Spinner /> rebooting, running recovery, reading the document…
      </div>
    );
  if (!result) return <div className="h-[5.5rem] rounded-2xl border border-dashed border-line" />;
  const o = result.data.extra?.outcome ?? "corrupt";
  const good = o === "A" || o === "B";
  const last = [...result.data.events].reverse().find((e) => e.disk)?.disk;
  const col = letterColor(good ? o : "X");
  const rec = result.data.extra?.recovery as Record<string, unknown> | null | undefined;
  const how = rec ? ((rec.rolled_back as unknown[] | undefined)?.length ? "rolled back" : (rec.rolled_forward as unknown[] | undefined)?.length ? "rolled forward" : "") : "";
  return (
    <motion.div
      initial={{ opacity: 0, y: 16, scale: 0.97 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ type: "spring", stiffness: 300, damping: 24 }}
      className={cx("flex h-[5.5rem] items-center gap-5 rounded-2xl border-2 px-5", good ? "border-pass/60 bg-pass/10" : "border-fail/70 bg-fail/10")}
    >
      <div
        className={cx("flex h-14 w-14 shrink-0 items-center justify-center rounded-xl text-[2rem] font-extrabold", !good && "hatch-x")}
        style={{ background: good ? col.bg : undefined, color: col.text, boxShadow: `inset 0 0 0 2px ${col.mark}` }}
      >
        {good ? o : "✗"}
      </div>
      <div className="min-w-0">
        <div className={cx("text-[1.35rem] font-bold", good ? "text-pass" : "text-fail")}>
          {good ? `✓ Document is exactly ${o}` : o === "empty" ? "✗ Document is EMPTY — data lost" : "✗ Document is CORRUPT — neither A nor B"}
          {how && <span className="ml-2 text-[1rem] font-medium text-fg-2">({how})</span>}
        </div>
        <div className="mono truncate text-[0.8rem] text-fg-3">
          after reboot get() → {last?.readable == null ? "nothing" : `"${last.readable}"`} · {result.data.summary}
        </div>
      </div>
    </motion.div>
  );
}

function Sectors({
  cells, volatile, label, sub, highlight, pointer, commitGlow,
}: { cells: (string | null)[]; volatile?: boolean; label: React.ReactNode; sub?: React.ReactNode; highlight?: boolean; pointer?: boolean; commitGlow?: boolean }) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-3">
        <span className="mono text-[0.95rem] font-semibold">{label}</span>
        {sub}
        {pointer && (
          <motion.span
            layoutId="problem-current"
            transition={{ type: "spring", stiffness: 170, damping: 14 }}
            className={cx("mono ml-auto rounded-md bg-commit px-2.5 py-0.5 text-[0.85rem] font-extrabold text-ink-950", commitGlow && "glow-commit")}
          >
            ◀ CURRENT
          </motion.span>
        )}
      </div>
      <div
        className={cx("grid gap-1 rounded-xl border-2 p-1.5 transition-colors", volatile ? "volatile border-line-strong" : "border-ink-600", highlight && "border-commit")}
        style={{ gridTemplateColumns: `repeat(${N}, minmax(0,1fr))` }}
      >
        {cells.map((c, i) => {
          const col = letterColor(c);
          return (
            <motion.div
              key={i}
              animate={{ backgroundColor: c ? col.bg : "rgba(255,255,255,0.02)", borderColor: c ? col.mark : "rgba(255,255,255,0.08)" }}
              transition={{ duration: 0.15 }}
              className="mono flex aspect-square items-center justify-center rounded-md border text-[1.05rem] font-bold"
              style={{ color: c ? col.text : "transparent" }}
            >
              {c ?? "·"}
            </motion.div>
          );
        })}
      </div>
    </div>
  );
}

function NaiveFile({ p, phase }: { p: number; phase: Phase }) {
  const idle = phase === "idle";
  const written = idle ? 0 : Math.floor(clamp01(p / 0.9) * N);
  const cells = Array.from({ length: N }, (_, i) => (idle ? "A" : i < written ? "B" : null));
  const torn = phase === "cut" && written < N;
  return (
    <div className="flex flex-col gap-5">
      <Sectors
        cells={cells}
        volatile={!idle && p < 0.95}
        label="report"
        sub={
          !idle && (
            <span className={cx("rounded px-2 text-[0.75rem] font-bold", torn ? "bg-fail/20 text-fail" : written < N ? "bg-warn/15 text-warn" : "bg-pass/15 text-pass")}>
              {torn ? `TORN — ${written}/${N} sectors of B, A destroyed` : written < N ? "truncated → A is already gone" : "B written"}
            </span>
          )
        }
      />
      <div className="text-[1rem] leading-snug text-fg-2">
        <span className="font-semibold text-fg">O_TRUNC</span> destroys A at the very first instant. Until the last sector of B is on disk, the only copy of your
        document is a half-written file.
      </div>
    </div>
  );
}

function AtomicFiles({ p, phase, stage }: { p: number; phase: Phase; stage: number }) {
  const idle = phase === "idle";
  const frac = idle ? 0 : clamp01((p - 0.07) / 0.33);
  const wrote = Math.floor(frac * N);
  const tmpExists = !idle && p >= 0.07;
  const named = p >= 0.47 && !idle;
  const committed = p >= 0.72 && !idle;
  const v2cells = Array.from({ length: N }, (_, i) => (i < wrote ? "B" : null));
  return (
    <div className="flex flex-col gap-4">
      <LayoutGroup id="problem-atomic">
        <div className="flex flex-col gap-4">
          <Sectors
            cells={Array(N).fill("A")}
            label="v000001.dat"
            pointer={!committed}
            commitGlow={false}
            sub={<span className="rounded bg-pass/15 px-2 text-[0.75rem] font-bold text-pass">committed · never touched</span>}
          />
          <AnimatePresence>
            {tmpExists && (
              <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
                <Sectors
                  cells={v2cells}
                  volatile={p < 0.47}
                  pointer={committed}
                  commitGlow={stage === 6}
                  label={named ? "v000002.dat" : ".v000002.dat.tmp"}
                  sub={<span className="rounded bg-white/10 px-2 text-[0.75rem] font-bold text-fg-2">{named ? "durable copy" : "new copy · page cache"}</span>}
                />
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </LayoutGroup>
      <div className="flex flex-wrap gap-1.5">
        {ATOMIC.map((s, i) => (
          <span
            key={s.label}
            className={cx(
              "rounded-md border px-2 py-0.5 text-[0.75rem] font-medium transition-colors",
              i === stage ? (i === 6 ? "border-commit bg-commit text-ink-950" : "border-fg bg-fg text-ink-950") : i < stage ? "border-line text-fg-2" : "border-line text-fg-3/60",
            )}
          >
            {s.label}
          </span>
        ))}
      </div>
    </div>
  );
}
