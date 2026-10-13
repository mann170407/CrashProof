import { AnimatePresence, LayoutGroup, motion } from "motion/react";
import { HardDrive, MemoryStick, FolderSync, ShieldCheck, ShieldX, ScrollText, ArrowDown } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import type { DiskFile, DiskSnapshot } from "../types";
import { letterColor } from "../lib/format";
import { cx } from "./ui";

const spring = { type: "spring" as const, stiffness: 420, damping: 34 };
const pointerSpring = { type: "spring" as const, stiffness: 140, damping: 15 };

export interface DiskViewProps {
  snap: DiskSnapshot | null;
  crashed?: boolean;
  recovering?: boolean;
  faulted?: boolean;
  activePath?: string;
  activeColor?: string;
  commitNow?: boolean;
  compact?: boolean;
  title?: string;
}

/** tracks which files just became durable (data or dir entry), to fire a solidify pulse */
function useDurablePulses(files: DiskFile[]) {
  const prev = useRef<Map<string, { d: boolean; e: boolean }>>(new Map());
  const [pulses, setPulses] = useState<Record<string, number>>({});
  useEffect(() => {
    const next = new Map<string, { d: boolean; e: boolean }>();
    const fired: string[] = [];
    for (const f of files) {
      const p = prev.current.get(f.name);
      if (p && ((!p.d && f.dataDurable) || (!p.e && f.entryDurable))) fired.push(f.name);
      next.set(f.name, { d: f.dataDurable, e: f.entryDurable });
    }
    prev.current = next;
    if (fired.length) setPulses((s) => { const o = { ...s }; fired.forEach((n) => (o[n] = (o[n] ?? 0) + 1)); return o; });
  }, [files]);
  return pulses;
}

export function DiskView({ snap, crashed, recovering, faulted, activePath, activeColor, commitNow, compact, title }: DiskViewProps) {
  const uid = useId();
  const files = snap?.files ?? [];
  const pulses = useDurablePulses(files);
  const versions = files.filter((f) => f.kind === "version" || f.kind === "corrupt" || f.kind === "other");
  const temps = files.filter((f) => f.kind === "tmp");
  const current = files.find((f) => f.kind === "current");
  const misc = files.filter((f) => f.kind === "journal" || f.kind === "lock");
  const curV = snap?.current?.ok ? snap.current.version : null;

  return (
    <div
      className={cx(
        "relative flex h-full min-h-0 flex-col overflow-hidden rounded-2xl border bg-ink-900/80 transition-[border-color,box-shadow] duration-500",
        recovering ? "border-recover/70 shadow-[0_0_50px_-10px_rgba(45,212,191,0.55)]" : crashed ? "border-fail/60" : "border-line",
      )}
    >
      {/* recovery sweep */}
      {recovering && (
        <div className="pointer-events-none absolute inset-0 overflow-hidden">
          <div className="absolute inset-y-0 w-1/2 animate-sweep bg-gradient-to-r from-transparent via-recover/15 to-transparent" />
        </div>
      )}

      {/* header */}
      <div className={cx("flex items-center gap-3 border-b border-line", compact ? "px-3 py-2" : "px-5 py-3")}>
        <HardDrive className="h-5 w-5 text-fg-3" />
        <span className="mono text-[0.85rem] text-fg-2">{title ?? `disk · ${snap?.doc ?? "report"}/`}</span>
        <div className="ml-auto flex min-w-0 items-center gap-2">
          <ScrollText className="h-4 w-4 shrink-0 text-fg-3" />
          <span className="mono text-[0.7rem] text-fg-3">journal</span>
          <div className="flex min-w-0 items-center gap-1.5 overflow-hidden">
            <AnimatePresence initial={false}>
              {(snap?.journal ?? []).slice(-4).map((j, i) => (
                <motion.span
                  key={j.tx + j.t + i}
                  layout
                  initial={{ opacity: 0, scale: 0.6, y: -6 }}
                  animate={{ opacity: 1, scale: 1, y: 0 }}
                  exit={{ opacity: 0, scale: 0.6 }}
                  transition={spring}
                  className={cx(
                    "mono shrink-0 rounded-md border px-1.5 py-0.5 text-[0.68rem] font-bold",
                    j.t === "BEGIN" && "border-brand/60 bg-brand/15 text-commit",
                    j.t === "COMMIT" && "border-pass/60 bg-pass/15 text-pass",
                    j.t === "ABORT" && "border-fail/60 bg-fail/15 text-fail",
                  )}
                  title={`${j.t} tx ${j.tx}${j.old != null ? ` v${j.old}→v${j.new}` : ""}${j.why ? ` (${j.why})` : ""}`}
                >
                  {j.t}
                  {j.t === "BEGIN" && j.new != null ? ` v${j.old}→v${j.new}` : ""}
                </motion.span>
              ))}
            </AnimatePresence>
            {(snap?.journal?.length ?? 0) === 0 && <span className="mono text-[0.68rem] text-fg-3">empty</span>}
          </div>
          {snap && snap.journalStatus !== "ok" && (
            <span className="mono shrink-0 rounded bg-warn/15 px-1.5 text-[0.65rem] font-bold text-warn">{snap.journalStatus}</span>
          )}
        </div>
      </div>

      {/* files */}
      <div className={cx("scroll-quiet relative flex min-h-0 flex-1 flex-col justify-center-safe overflow-y-auto overflow-x-hidden", compact ? "gap-2 px-3 py-2" : "gap-4 p-4")}>
        <LayoutGroup id={uid}>
          <div className="flex flex-wrap items-end justify-center gap-3">
            <AnimatePresence mode="popLayout" initial={false}>
              {versions.map((f) => (
                <VersionCard
                  key={f.name}
                  f={f}
                  isCurrent={curV != null && f.version === curV}
                  pointerId={`${uid}-ptr`}
                  pulse={pulses[f.name] ?? 0}
                  active={activePath === f.name}
                  activeColor={activeColor}
                  commitNow={!!commitNow}
                  compact={compact}
                />
              ))}
            </AnimatePresence>
            {versions.length === 0 && <div className="text-fg-3">no version files</div>}
          </div>
          <div className={cx("flex flex-wrap items-center justify-center gap-2.5", compact ? "min-h-[2.4rem]" : "min-h-[3.2rem]")}>
            <AnimatePresence mode="popLayout" initial={false}>
              {current && (
                <SmallCard key="CURRENT" f={current} pulse={pulses.CURRENT ?? 0} active={activePath === "CURRENT"} activeColor={activeColor} commitNow={!!commitNow} curOk={snap?.current?.ok} curVersion={snap?.current?.version} />
              )}
              {temps.map((f) => (
                <SmallCard key={f.name} f={f} pulse={pulses[f.name] ?? 0} active={activePath === f.name} activeColor={activeColor} />
              ))}
              {misc.map((f) => (
                <SmallCard key={f.name} f={f} pulse={pulses[f.name] ?? 0} active={activePath === f.name} activeColor={activeColor} />
              ))}
            </AnimatePresence>
          </div>
        </LayoutGroup>

        <AnimatePresence>
          {crashed && (
            <motion.div
              key="stamp"
              initial={{ opacity: 0, scale: 2.2, rotate: -18 }}
              animate={{ opacity: 1, scale: 1, rotate: -12 }}
              exit={{ opacity: 0, scale: 0.9 }}
              transition={{ type: "spring", stiffness: 300, damping: 18 }}
              className="pointer-events-none absolute inset-0 flex items-center justify-center"
            >
              <div className="mono rounded-xl border-[5px] border-fail bg-ink-950/60 px-8 py-1 text-[3.6rem] font-extrabold tracking-[0.25em] text-fail [text-shadow:0_0_30px_rgba(255,93,115,0.7)]">
                CRASH
              </div>
            </motion.div>
          )}
          {faulted && !crashed && (
            <motion.div
              key="eio"
              initial={{ opacity: 0, scale: 1.8, rotate: 10 }}
              animate={{ opacity: 1, scale: 1, rotate: 8 }}
              exit={{ opacity: 0 }}
              className="pointer-events-none absolute right-4 top-4"
            >
              <div className="mono rounded-lg border-4 border-warn px-4 text-[2rem] font-extrabold tracking-widest text-warn">EIO</div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* what a reader gets right now */}
      <Readable snap={snap} compact={compact} />
    </div>
  );
}

function durabilityTags(f: DiskFile) {
  return (
    <div className="flex flex-wrap justify-center gap-1">
      {!f.dataDurable && (
        <span className="inline-flex items-center gap-1 rounded bg-warn/15 px-1.5 text-[0.62rem] font-bold uppercase tracking-wide text-warn" title="Written but not fsynced: lives only in the page cache">
          <MemoryStick className="h-3 w-3" /> page cache
        </span>
      )}
      {!f.entryDurable && (
        <span className="inline-flex items-center gap-1 rounded bg-white/10 px-1.5 text-[0.62rem] font-bold uppercase tracking-wide text-fg-2" title="Directory entry (create/rename) not yet made durable by fsync(dir)">
          <FolderSync className="h-3 w-3" /> dir entry
        </span>
      )}
    </div>
  );
}

function PulseRing({ pulse, color = "var(--color-pass)" }: { pulse: number; color?: string }) {
  if (!pulse) return null;
  return (
    <motion.span
      key={pulse}
      className="pointer-events-none absolute -inset-1 rounded-2xl border-2"
      style={{ borderColor: color }}
      initial={{ opacity: 0.9, scale: 0.98 }}
      animate={{ opacity: 0, scale: 1.12 }}
      transition={{ duration: 0.8, ease: "easeOut" }}
    />
  );
}

function VersionCard({
  f, isCurrent, pointerId, pulse, active, activeColor, commitNow, compact,
}: {
  f: DiskFile; isCurrent: boolean; pointerId: string; pulse: number; active: boolean; activeColor?: string; commitNow: boolean; compact?: boolean;
}) {
  const volatile = !f.dataDurable || !f.entryDurable;
  const bad = f.valid === false || f.kind === "corrupt";
  const letter = bad ? "✗" : f.fill ?? (f.preview ? f.preview.slice(0, 1) : "∅");
  const torn = !bad && f.kind === "other" && f.fill == null && f.size > 0;
  const col = letterColor(bad ? "X" : torn ? "X" : f.fill);
  return (
    <motion.div
      layout
      initial={{ opacity: 0, scale: 0.7, y: 20 }}
      animate={{ opacity: f.dataDurable ? 1 : 0.62, scale: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.5, y: 30, filter: "blur(6px)", transition: { duration: 0.45 } }}
      transition={spring}
      className="relative flex flex-col items-center"
    >
      {/* CURRENT pointer: one element, shared layoutId -> it swings from v1 to v2 at the commit */}
      <div className={cx("flex items-end justify-center", compact ? "h-7" : "h-10")}>
        {isCurrent && (
          <motion.div
            layoutId={pointerId}
            transition={pointerSpring}
            className={cx(
              "mono flex items-center gap-1 rounded-md bg-commit px-2 py-0.5 text-[0.7rem] font-extrabold tracking-wider text-ink-950",
              commitNow && "glow-commit",
            )}
          >
            CURRENT <ArrowDown className="h-3.5 w-3.5" />
          </motion.div>
        )}
      </div>
      <motion.div
        className={cx(
          "relative flex flex-col items-center justify-between overflow-hidden rounded-2xl border-2 bg-ink-850",
          compact ? "h-[6.6rem] w-[5.8rem] p-1" : "h-[10.5rem] w-[8.6rem] p-2.5",
          volatile && "volatile",
          (bad || torn) && "hatch-x",
        )}
        style={{
          borderColor: active ? activeColor ?? "var(--color-fg)" : isCurrent ? "var(--color-commit)" : volatile ? "var(--color-line-strong)" : "var(--color-ink-600)",
          background: `linear-gradient(180deg, ${col.bg}, transparent 70%)`,
          boxShadow: isCurrent && commitNow ? "0 0 40px 2px rgba(139,123,255,0.55)" : undefined,
        }}
      >
        <PulseRing pulse={pulse} />
        <div className="mono w-full truncate text-center text-[0.7rem] text-fg-2" title={f.name}>
          {f.name}
        </div>
        <div className={cx("font-extrabold leading-none tracking-tight", compact ? "text-[2.5rem]" : "text-[4.4rem]")} style={{ color: col.text }}>
          {letter}
        </div>
        <div className="flex w-full flex-col items-center gap-1">
          {f.kind === "version" && (
            <span
              className={cx(
                "inline-flex items-center gap-1 rounded px-1.5 text-[0.65rem] font-bold",
                f.valid ? "bg-pass/15 text-pass" : f.valid === false ? "bg-fail/20 text-fail" : "bg-white/10 text-fg-3",
              )}
              title="header + SHA-256 verification"
            >
              {f.valid ? <ShieldCheck className="h-3 w-3" /> : <ShieldX className="h-3 w-3" />}
              SHA {f.valid ? "✓" : f.valid === false ? "✗" : "?"}
            </span>
          )}
          {torn && <span className="rounded bg-fail/20 px-1.5 text-[0.65rem] font-bold text-fail">TORN · {f.size} B</span>}
          {durabilityTags(f)}
        </div>
      </motion.div>
    </motion.div>
  );
}

/** pointer line: "ADOC-CURRENT <format> <version> <sha> <crc>" (or a bare "v000002 ...") */
export function pointerTarget(preview: string): number | null {
  const m = /ADOC-CURRENT\s+\d+\s+(\d+)/.exec(preview) ?? /^v0*(\d+)/.exec(preview);
  return m ? parseInt(m[1], 10) : null;
}

function SmallCard({
  f, pulse, active, activeColor, commitNow, curOk, curVersion,
}: { f: DiskFile; pulse: number; active: boolean; activeColor?: string; commitNow?: boolean; curOk?: boolean; curVersion?: number }) {
  const volatile = !f.dataDurable || !f.entryDurable;
  const isCur = f.kind === "current";
  const col = letterColor(f.fill);
  const isPtrTmp = f.kind === "tmp" && /CURRENT/.test(f.name);
  const target = isCur ? curVersion ?? pointerTarget(f.preview) : isPtrTmp ? pointerTarget(f.preview) : null;
  return (
    <motion.div
      layout
      initial={{ opacity: 0, scale: 0.7 }}
      animate={{ opacity: f.dataDurable ? 1 : 0.6, scale: 1 }}
      exit={{ opacity: 0, scale: 0.6, transition: { duration: 0.35 } }}
      transition={spring}
      className={cx(
        "relative flex items-center gap-2.5 rounded-xl border-2 bg-ink-850 px-3 py-1.5",
        volatile && "volatile",
        isCur && commitNow && "glow-commit",
      )}
      style={{ borderColor: active ? activeColor ?? "var(--color-fg)" : isCur ? "rgb(185 168 255 / 0.55)" : "var(--color-line-strong)" }}
      title={f.name}
    >
      <PulseRing pulse={pulse} />
      {isCur ? (
        <>
          <span className="mono text-[0.8rem] font-bold text-commit">CURRENT</span>
          <span className="mono text-[0.9rem] font-bold text-fg">→ v{target ?? "?"}</span>
          <span className={cx("rounded px-1 text-[0.62rem] font-bold", f.valid === false || curOk === false ? "bg-fail/20 text-fail" : "bg-pass/15 text-pass")}>
            CRC {f.valid === false || curOk === false ? "✗" : "✓"}
          </span>
        </>
      ) : (
        <>
          <span className="rounded bg-white/10 px-1.5 text-[0.6rem] font-bold uppercase tracking-wider text-fg-3">{f.kind}</span>
          {f.fill && (
            <span className="text-[1.3rem] font-extrabold leading-none" style={{ color: col.text }}>
              {f.fill}
            </span>
          )}
          {isPtrTmp && target != null && <span className="mono text-[0.85rem] font-bold text-commit">→ v{target}</span>}
          <span className="mono max-w-[13rem] truncate text-[0.72rem] text-fg-2">{f.name}</span>
          <span className="mono text-[0.65rem] text-fg-3">{f.size}B</span>
        </>
      )}
      {durabilityTags(f)}
    </motion.div>
  );
}

function Readable({ snap, compact }: { snap: DiskSnapshot | null; compact?: boolean }) {
  const r = snap?.readable ?? null;
  const fill = snap?.readableFill ?? null;
  const torn = r != null && r !== "" && !fill;
  const col = letterColor(fill ?? (torn ? "X" : null));
  return (
    <div className={cx("flex items-center gap-4 border-t border-line bg-ink-950/40", compact ? "px-3 py-2" : "px-5 py-3")}>
      <span className="mono text-[0.85rem] text-fg-2">get({snap?.doc ?? "report"}) →</span>
      <AnimatePresence mode="wait" initial={false}>
        <motion.div
          key={`${fill}|${r}`}
          initial={{ opacity: 0, y: 10, scale: 0.9 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: -10, scale: 0.9 }}
          transition={{ duration: 0.25 }}
          className="flex items-center gap-3"
        >
          {fill ? (
            <span className="flex h-11 w-11 items-center justify-center rounded-lg text-[1.7rem] font-extrabold" style={{ background: col.bg, color: col.text, boxShadow: `inset 0 0 0 2px ${col.mark}` }}>
              {fill}
            </span>
          ) : r == null || r === "" ? (
            <span className="text-[1.1rem] font-semibold text-fail">∅ nothing readable</span>
          ) : (
            <span className="hatch-x rounded-lg px-2 py-1 text-[1rem] font-bold text-vx-text">✗ torn / garbage</span>
          )}
          {r && <span className="mono max-w-[16rem] truncate text-[0.8rem] text-fg-3">"{r}"</span>}
        </motion.div>
      </AnimatePresence>
    </div>
  );
}
