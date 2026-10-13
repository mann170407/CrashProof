import { AnimatePresence, motion } from "motion/react";
import {
  ArrowRightLeft, BookOpen, Check, FilePlus2, FolderPlus, FolderSync, Lock, LockOpen, PenLine, Save, Trash2, Zap, X,
  Flag, MessageSquareText, Skull, RotateCcw, User, BookOpenText, Cpu,
} from "lucide-react";
import { useEffect, useMemo, useRef } from "react";
import type { LabEvent, SysOp } from "../types";
import { fmtMs, isSpanning, laneColor, laneLabel, lanesOf } from "../lib/format";
import { cx } from "./ui";

const OP_ICON: Record<SysOp, typeof Save> = {
  create: FilePlus2, write: PenLine, fsync: Save, fsync_dir: FolderSync, rename: ArrowRightLeft, unlink: Trash2,
  mkdir: FolderPlus, lock: Lock, unlock: LockOpen, read: BookOpen,
};

function LaneIcon({ lane, className }: { lane: string; className?: string }) {
  const I = lane === "writer" ? PenLine : lane === "recovery" ? RotateCcw : lane === "reader" ? BookOpenText : lane.startsWith("client") ? User : Cpu;
  return <I className={className} style={{ color: laneColor(lane) }} />;
}

export function Swimlanes({ events, index, compact, maxLanes }: { events: LabEvent[]; index: number; compact?: boolean; maxLanes?: number }) {
  const lanes = useMemo(() => lanesOf(events).slice(0, maxLanes ?? 8), [events, maxLanes]);
  const visible = events.slice(0, index + 1);
  const scroller = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [index]);
  const cols = `repeat(${Math.max(1, lanes.length)}, minmax(0, 1fr))`;

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden rounded-2xl border border-line bg-ink-900/60">
      <div className="grid border-b border-line bg-ink-900/90" style={{ gridTemplateColumns: cols }}>
        {lanes.map((l) => (
          <div key={l} className={cx("flex items-center gap-2 font-semibold capitalize", compact ? "px-2 py-1.5 text-[0.8rem]" : "px-4 py-2.5 text-[0.95rem]")}>
            <LaneIcon lane={l} className="h-4 w-4" />
            <span style={{ color: laneColor(l) }}>{laneLabel(l)}</span>
          </div>
        ))}
      </div>
      <div ref={scroller} className="scroll-quiet relative min-h-0 flex-1 overflow-y-auto">
        {/* lane guide lines */}
        <div className="pointer-events-none absolute inset-0 grid" style={{ gridTemplateColumns: cols }}>
          {lanes.map((l) => (
            <div key={l} className="flex justify-center">
              <div className="h-full w-px" style={{ background: `color-mix(in srgb, ${laneColor(l)} 22%, transparent)` }} />
            </div>
          ))}
        </div>
        <div className={cx("relative flex flex-col", compact ? "gap-1 p-1.5" : "gap-1.5 p-2.5")}>
          <AnimatePresence initial={false}>
            {visible.map((e, i) => (
              <Row key={e.seq} e={e} lanes={lanes} cols={cols} active={i === index} compact={compact} />
            ))}
          </AnimatePresence>
        </div>
      </div>
    </div>
  );
}

function Row({ e, lanes, cols, active, compact }: { e: LabEvent; lanes: string[]; cols: string; active: boolean; compact?: boolean }) {
  const enter = { initial: { opacity: 0, y: 14, scale: 0.98 }, animate: { opacity: 1, y: 0, scale: 1 }, exit: { opacity: 0, height: 0, transition: { duration: 0.15 } } };
  if (isSpanning(e)) return <SpanRow e={e} active={active} compact={compact} enter={enter} />;
  const col = Math.max(0, lanes.indexOf(e.lane));
  return (
    <motion.div layout="position" {...enter} transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }} className="grid" style={{ gridTemplateColumns: cols }}>
      <div style={{ gridColumn: `${col + 1} / span 1` }} className="min-w-0 px-1">
        <EventChip e={e} active={active} compact={compact} />
      </div>
    </motion.div>
  );
}

function EventChip({ e, active, compact }: { e: LabEvent; active: boolean; compact?: boolean }) {
  const color = laneColor(e.lane);
  const Icon = e.kind === "fault" ? Zap : e.kind === "check" ? (e.ok === false ? X : Check) : e.kind === "note" ? MessageSquareText : e.op ? OP_ICON[e.op] ?? Cpu : Cpu;
  const commit = !!e.commit;
  const fault = e.kind === "fault";
  const check = e.kind === "check";
  return (
    <div
      className={cx(
        "relative overflow-hidden rounded-xl border transition-[background,box-shadow,border-color] duration-300",
        compact ? "px-2 py-1" : "px-3 py-1.5",
        commit && "glow-commit",
        active && !commit && "shadow-[0_0_0_2px_var(--tw-shadow-color)]",
      )}
      style={{
        borderColor: commit ? "var(--color-commit)" : fault ? "var(--color-warn)" : active ? color : "var(--color-line)",
        background: commit
          ? "linear-gradient(90deg, rgb(139 123 255 / 0.32), rgb(139 123 255 / 0.10))"
          : fault
            ? "rgb(255 181 71 / 0.14)"
            : active
              ? `color-mix(in srgb, ${color} 18%, var(--color-ink-850))`
              : "var(--color-ink-850)",
        ["--tw-shadow-color" as string]: `color-mix(in srgb, ${color} 55%, transparent)`,
      }}
      title={e.detail}
    >
      <div className="flex items-center gap-2">
        <Icon
          className={cx("shrink-0", compact ? "h-3.5 w-3.5" : "h-4 w-4")}
          style={{ color: fault ? "var(--color-warn)" : check ? (e.ok === false ? "var(--color-fail)" : "var(--color-pass)") : color }}
        />
        <span className={cx("min-w-0 flex-1 truncate", e.kind === "note" ? "italic text-fg-2" : "text-fg", e.kind === "syscall" ? "mono" : "", compact ? "text-[0.72rem]" : "text-[0.85rem]")}>
          {e.label}
        </span>
        {commit && (
          <span className="mono shrink-0 rounded bg-commit px-1.5 text-[0.6rem] font-extrabold tracking-wider text-ink-950">COMMIT POINT</span>
        )}
        {e.t != null && !compact && <span className="mono shrink-0 text-[0.62rem] text-fg-3 tabular">{fmtMs(e.t)}</span>}
      </div>
      {e.native && (
        <div className={cx("mono truncate pl-6 text-fg-3", compact ? "text-[0.6rem]" : "text-[0.68rem]")} title={e.native}>
          {e.native}
        </div>
      )}
    </div>
  );
}

function SpanRow({ e, active, compact, enter }: { e: LabEvent; active: boolean; compact?: boolean; enter: object }) {
  if (e.kind === "crash")
    return (
      <motion.div layout="position" {...enter} transition={{ type: "spring", stiffness: 500, damping: 20 }} className="px-1">
        <div className={cx("flex items-center gap-3 rounded-xl border-2 border-fail bg-fail/15", compact ? "px-2 py-1" : "px-4 py-2")}>
          <Skull className="h-5 w-5 text-fail" />
          <span className={cx("mono font-extrabold tracking-wider text-fail", compact ? "text-[0.8rem]" : "text-[1rem]")}>{e.label}</span>
          {!compact && e.detail && <span className="truncate text-[0.8rem] text-fg-2">{e.detail}</span>}
        </div>
      </motion.div>
    );
  if (e.kind === "result")
    return (
      <motion.div layout="position" {...enter} className="px-1">
        <div className={cx("flex items-center gap-3 rounded-xl border", e.ok === false ? "border-fail/60 bg-fail/10" : "border-pass/60 bg-pass/10", compact ? "px-2 py-1" : "px-4 py-2")}>
          {e.ok === false ? <X className="h-5 w-5 text-fail" /> : <Check className="h-5 w-5 text-pass" />}
          <span className={cx("font-semibold", compact ? "text-[0.8rem]" : "text-[0.95rem]")}>{e.label}</span>
        </div>
      </motion.div>
    );
  // phase / system notes / checks spanning all lanes
  const color = laneColor(e.lane);
  return (
    <motion.div layout="position" {...enter} className={cx("px-1", compact ? "pt-1" : "pt-2")}>
      <div className="flex items-center gap-3">
        {e.kind === "check" ? (
          e.ok === false ? <X className="h-4 w-4 text-fail" /> : <Check className="h-4 w-4 text-pass" />
        ) : (
          <Flag className="h-4 w-4" style={{ color }} />
        )}
        <span className={cx("font-semibold uppercase tracking-[0.12em]", active ? "text-fg" : "text-fg-2", compact ? "text-[0.7rem]" : "text-[0.8rem]")} style={{ color: e.kind === "phase" ? color : undefined }}>
          {e.label}
        </span>
        <div className="h-px flex-1" style={{ background: `linear-gradient(90deg, color-mix(in srgb, ${color} 60%, transparent), transparent)` }} />
      </div>
    </motion.div>
  );
}
