import { AnimatePresence, motion } from "motion/react";
import { ChevronLeft, ChevronRight, Pause, Play, RotateCcw, X, CheckCircle2, XCircle, Timer, Equal, TriangleAlert } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { BackendId, Check, DataSource, LabEvent, ScenarioResult } from "../types";
import { SPEEDS, usePlayback, type Playback } from "../lib/usePlayback";
import { useHotkeys } from "../lib/keys";
import { crashStateAt, fmtMs, laneColor, laneLabel, outcomeKey, snapshotAt } from "../lib/format";
import { Swimlanes } from "./Swimlanes";
import { DiskView } from "./DiskView";
import { cutPower } from "./PowerCut";
import { OsMark, SourceBadge, cx } from "./ui";

export interface PlayerRun<X = unknown> {
  backend: BackendId;
  result: ScenarioResult<X>;
  source: DataSource;
  error?: string;
}

function dwell(e: LabEvent | undefined): number {
  if (!e) return 800;
  if (e.kind === "crash") return 2300;
  if (e.kind === "fault") return 2000;
  if (e.commit) return 2000;
  if (e.kind === "phase") return 1300;
  if (e.kind === "result") return 1500;
  if (e.kind === "check") return 650;
  if (e.kind === "note") return 1100;
  return 850;
}

export function TimelinePlayer<X>({
  runs, resetKey, active, overlay, autoplay = true, initialSpeed = 1, compareLabel,
}: {
  runs: PlayerRun<X>[];
  resetKey: unknown;
  active: boolean;
  /** extra visual drawn over a side's disk area (scene-specific) */
  overlay?: (run: PlayerRun<X>, index: number) => ReactNode;
  autoplay?: boolean;
  initialSpeed?: number;
  compareLabel?: string;
}) {
  const lengths = runs.map((r) => r.result.events.length);
  const length = Math.max(1, ...lengths);
  const pb = usePlayback(length, {
    resetKey,
    autoplay,
    initialSpeed,
    dwellMs: (i) => Math.max(...runs.map((r) => dwell(r.result.events[Math.min(i, r.result.events.length - 1)]))),
  });
  const [bannerOpen, setBannerOpen] = useState(true);
  useEffect(() => setBannerOpen(true), [resetKey]);

  useHotkeys(active, {
    " ": () => pb.toggle(),
    ",": () => pb.step(-1),
    ".": () => pb.step(1),
    b: () => setBannerOpen((o) => !o),
  });

  // power-cut flash when playback reaches a crash / fault going forward
  const lastIdx = useRef(-1);
  useEffect(() => {
    if (!active) return;
    if (pb.index !== lastIdx.current && pb.dir > 0) {
      for (const r of runs) {
        const e = r.result.events[pb.index];
        if (e?.kind === "crash") cutPower("crash");
        else if (e?.kind === "fault") cutPower("fault");
      }
    }
    lastIdx.current = pb.index;
  }, [pb.index, pb.dir, runs, active]);

  const compare = runs.length > 1;

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <div className={cx("relative grid min-h-0 flex-1 gap-4", compare ? "grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]" : "grid-cols-[minmax(0,1fr)]")}>
        {runs.map((r) => (
          <Side key={r.backend} run={r} index={Math.min(pb.index, r.result.events.length - 1)} compare={compare} overlay={overlay} />
        ))}
        <AnimatePresence>
          {pb.atEnd && bannerOpen && (
            <ResultBanner runs={runs} onClose={() => setBannerOpen(false)} compareLabel={compareLabel} />
          )}
        </AnimatePresence>
      </div>
      <Controls pb={pb} runs={runs} />
    </div>
  );
}

function Side<X>({ run, index, compare, overlay }: { run: PlayerRun<X>; index: number; compare: boolean; overlay?: (run: PlayerRun<X>, index: number) => ReactNode }) {
  const events = run.result.events;
  const e = events[index];
  const snap = useMemo(() => snapshotAt(events, index), [events, index]);
  const cs = useMemo(() => crashStateAt(events, index), [events, index]);
  const disk = (
    <div className="relative min-h-0">
      <DiskView
        snap={snap}
        crashed={cs.crashed}
        recovering={cs.recovering}
        faulted={e?.kind === "fault"}
        activePath={e?.path}
        activeColor={e ? laneColor(e.lane) : undefined}
        commitNow={!!e?.commit}
        compact={compare}
      />
      {overlay?.(run, index)}
    </div>
  );

  if (compare)
    return (
      <div className="@container flex min-h-0 min-w-0 flex-col gap-2">
        <SideHeader run={run} />
        <div className="grid min-h-0 flex-1 grid-cols-1 grid-rows-[minmax(10rem,1fr)_auto] gap-2 @[44rem]:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)] @[44rem]:grid-rows-1">
          <div className="min-h-0">
            <Swimlanes events={events} index={index} compact />
          </div>
          <div className="flex min-h-0 flex-col gap-2">
            <Caption e={e} compact />
            <div className="min-h-0 flex-1 [&>div]:h-full">{disk}</div>
          </div>
        </div>
      </div>
    );
  return (
    <div className="grid min-h-0 grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1.08fr)_minmax(0,1fr)]">
      <div className="flex min-h-0 flex-col gap-2">
        <div className="min-h-[12rem] flex-1">
          <Swimlanes events={events} index={index} />
        </div>
      </div>
      <div className="flex min-h-0 flex-col gap-3">
        <Caption e={e} />
        <div className="min-h-0 flex-1 [&>div]:h-full">{disk}</div>
      </div>
    </div>
  );
}

function SideHeader<X>({ run }: { run: PlayerRun<X> }) {
  const p = run.result.platform;
  return (
    <div className="flex items-center gap-3 rounded-xl border border-line bg-ink-900/80 px-3 py-1.5">
      <OsMark id={run.backend} className="h-5 w-5" />
      <span className="truncate text-[0.95rem] font-semibold">{p?.label ?? run.backend}</span>
      <span className="ml-auto inline-flex items-center gap-1.5 text-[0.8rem] text-fg-2 tabular" title="wall-clock runtime of this scenario on this backend">
        <Timer className="h-4 w-4" /> {fmtMs(run.result.elapsedMs)}
      </span>
      <SourceBadge source={run.source} error={run.error} />
    </div>
  );
}

function Caption({ e, compact }: { e: LabEvent | undefined; compact?: boolean }) {
  if (!e) return null;
  const color = e.kind === "crash" ? "var(--color-fail)" : e.kind === "fault" ? "var(--color-warn)" : e.commit ? "var(--color-commit)" : laneColor(e.lane);
  return (
      <motion.div
        key={e.seq}
        initial={{ opacity: 0.2, x: 10 }}
        animate={{ opacity: 1, x: 0 }}
        transition={{ duration: 0.2 }}
        className={cx("rounded-xl border border-line bg-ink-900/80", compact ? "px-3 py-1.5" : "px-4 py-3")}
        style={{ boxShadow: `inset 4px 0 0 ${color}` }}
      >
        <div className="flex items-baseline gap-2">
          <span className="mono text-[0.7rem] font-bold uppercase tracking-widest" style={{ color }}>
            {e.kind === "syscall" ? laneLabel(e.lane) : e.kind}
          </span>
          <span className={cx("min-w-0 truncate font-semibold", e.kind === "syscall" && "mono", compact ? "text-[0.85rem]" : "text-[1.05rem]")}>{e.label}</span>
        </div>
        {!compact && (e.detail || e.native) && (
          <div className="mt-1 text-[0.9rem] leading-snug text-fg-2">
            {e.detail}
            {e.native && (
              <span className="mono ml-1 text-[0.78rem] text-fg-3">
                {e.detail ? " · " : ""}
                {e.native}
              </span>
            )}
          </div>
        )}
      </motion.div>
  );
}

function Controls<X>({ pb, runs }: { pb: Playback; runs: PlayerRun<X>[] }) {
  const events = runs[0]?.result.events ?? [];
  // markers on the scrub track: crash, commit, phase
  const marks = events
    .map((e, i) => ({ e, i }))
    .filter(({ e }) => e.kind === "crash" || e.commit || e.kind === "fault" || e.kind === "phase");
  return (
    <div className="flex items-center gap-3 rounded-2xl border border-line bg-ink-900/80 px-3 py-2">
      <IconBtn onClick={() => pb.restart()} title="Restart">
        <RotateCcw className="h-5 w-5" />
      </IconBtn>
      <IconBtn onClick={() => pb.step(-1)} title="Step back (,)">
        <ChevronLeft className="h-6 w-6" />
      </IconBtn>
      <motion.button
        type="button"
        whileTap={{ scale: 0.92 }}
        onClick={() => pb.toggle()}
        title="Play / pause (Space)"
        className="flex h-12 w-12 items-center justify-center rounded-full bg-fg text-ink-950 shadow-[0_0_30px_-6px_rgba(139,123,255,0.8)]"
      >
        {pb.playing ? <Pause className="h-6 w-6" /> : <Play className="ml-0.5 h-6 w-6" />}
      </motion.button>
      <IconBtn onClick={() => pb.step(1)} title="Step forward (.)">
        <ChevronRight className="h-6 w-6" />
      </IconBtn>
      <div className="relative mx-2 flex-1">
        <input
          type="range"
          className="scrub w-full"
          min={0}
          max={Math.max(0, pb.length - 1)}
          value={pb.index}
          onChange={(ev) => {
            pb.pause();
            pb.seek(Number(ev.target.value));
          }}
          aria-label="Scrub timeline"
        />
        <div className="pointer-events-none absolute inset-x-0 -top-2 h-2">
          {marks.map(({ e, i }) => (
            <span
              key={i}
              className="absolute top-0 h-2 w-[3px] -translate-x-1/2 rounded-full"
              style={{
                left: `${(i / Math.max(1, pb.length - 1)) * 100}%`,
                background: e.kind === "crash" ? "var(--color-fail)" : e.commit ? "var(--color-commit)" : e.kind === "fault" ? "var(--color-warn)" : "var(--color-fg-3)",
              }}
            />
          ))}
        </div>
      </div>
      <span className="mono w-[6.5rem] text-right text-[0.85rem] text-fg-2 tabular">
        {pb.index + 1} / {pb.length}
      </span>
      <div className="flex rounded-lg border border-line p-0.5">
        {SPEEDS.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => pb.setSpeed(s)}
            className={cx("mono rounded-md px-2 py-0.5 text-[0.78rem] font-semibold", pb.speed === s ? "bg-fg text-ink-950" : "text-fg-2 hover:text-fg")}
          >
            {s}×
          </button>
        ))}
      </div>
    </div>
  );
}

function IconBtn({ children, onClick, title }: { children: ReactNode; onClick: () => void; title: string }) {
  return (
    <motion.button
      type="button"
      whileTap={{ scale: 0.9 }}
      onClick={onClick}
      title={title}
      className="flex h-10 w-10 items-center justify-center rounded-full text-fg-2 hover:bg-white/10 hover:text-fg"
    >
      {children}
    </motion.button>
  );
}

// ---------------------------------------------------------------- result banner
export function CheckList({ checks, max = 7, delay = 0.25 }: { checks: Check[]; max?: number; delay?: number }) {
  const shown = checks.slice(0, max);
  return (
    <ul className="flex flex-col gap-1.5">
      {shown.map((c, i) => (
        <motion.li
          key={i}
          initial={{ opacity: 0, x: -10 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ delay: delay + i * 0.09, duration: 0.3 }}
          className="flex items-start gap-2.5 text-[0.9rem] leading-snug"
          title={c.detail}
        >
          <AnimatedTick pass={c.pass} delay={delay + i * 0.09 + 0.1} />
          <span className={c.pass ? "text-fg" : "text-fail"}>{c.name}</span>
        </motion.li>
      ))}
      {checks.length > max && <li className="pl-8 text-[0.85rem] text-fg-3">+ {checks.length - max} more checks · {checks.filter((c) => c.pass).length}/{checks.length} pass</li>}
    </ul>
  );
}

export function AnimatedTick({ pass, delay = 0, size = "1.3rem" }: { pass: boolean; delay?: number; size?: string }) {
  return (
    <svg viewBox="0 0 24 24" style={{ width: size, height: size }} className="mt-0.5 shrink-0" aria-label={pass ? "pass" : "fail"}>
      <circle cx="12" cy="12" r="11" fill={pass ? "rgb(61 220 151 / 0.15)" : "rgb(255 93 115 / 0.18)"} stroke={pass ? "#3ddc97" : "#ff5d73"} strokeWidth="1.5" />
      {pass ? (
        <motion.path d="M7 12.5l3.2 3.2L17 9" fill="none" stroke="#3ddc97" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ delay, duration: 0.35 }} />
      ) : (
        <motion.path d="M8 8l8 8M16 8l-8 8" fill="none" stroke="#ff5d73" strokeWidth="2.6" strokeLinecap="round" initial={{ pathLength: 0 }} animate={{ pathLength: 1 }} transition={{ delay, duration: 0.35 }} />
      )}
    </svg>
  );
}

function ResultBanner<X>({ runs, onClose, compareLabel }: { runs: PlayerRun<X>[]; onClose: () => void; compareLabel?: string }) {
  const compare = runs.length > 1;
  const allPass = runs.every((r) => r.result.pass);
  const same = compare && new Set(runs.map((r) => outcomeKey(r.result as ScenarioResult<unknown>))).size === 1;
  return (
    <motion.div
      initial={{ opacity: 0, y: 40 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: 30 }}
      transition={{ type: "spring", stiffness: 260, damping: 26 }}
      className={cx(
        "absolute bottom-3 left-3 z-20 max-h-[80%] overflow-y-auto rounded-2xl border-2 bg-ink-900/95 p-5 shadow-2xl backdrop-blur-md scroll-quiet",
        compare ? "right-3" : "w-[min(46rem,calc(52%-1rem))]",
        allPass ? "border-pass/60" : "border-fail/70",
      )}
    >
      <button type="button" onClick={onClose} className="absolute right-3 top-3 rounded-full p-1 text-fg-3 hover:bg-white/10 hover:text-fg" title="Hide (B)">
        <X className="h-5 w-5" />
      </button>
      {compare && (
        <motion.div
          initial={{ opacity: 0, scale: 0.9 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ delay: 0.15 }}
          className={cx("mb-3 flex items-center gap-3 text-[1.4rem] font-bold", same && allPass ? "text-pass" : "text-warn")}
        >
          {same ? <Equal className="h-7 w-7" /> : <TriangleAlert className="h-7 w-7" />}
          {same && allPass ? "Same result on both kernels" : same ? "Same outcome on both kernels (see failed checks)" : "Results differ between kernels"}
          <span className="mono text-[0.9rem] font-normal text-fg-2">{compareLabel ?? runs.map((r) => outcomeKey(r.result as ScenarioResult<unknown>)).join("  ·  ")}</span>
        </motion.div>
      )}
      <div className={cx("grid gap-6", compare ? "grid-cols-2" : "grid-cols-1")}>
        {runs.map((r) => (
          <div key={r.backend}>
            <div className="mb-2 flex items-center gap-3">
              {r.result.pass ? <CheckCircle2 className="h-8 w-8 text-pass" /> : <XCircle className="h-8 w-8 text-fail" />}
              <span className={cx("text-[1.6rem] font-extrabold tracking-wide", r.result.pass ? "text-pass" : "text-fail")}>{r.result.pass ? "PASS" : "FAIL"}</span>
              {compare && <OsMark id={r.backend} className="h-5 w-5" />}
              <span className="text-[1rem] text-fg">{r.result.summary}</span>
            </div>
            <CheckList checks={r.result.checks} max={compare ? 4 : 6} />
          </div>
        ))}
      </div>
    </motion.div>
  );
}
