import { motion } from "motion/react";
import type { ReactNode } from "react";
import type { BackendId, DataSource } from "../types";
import type { BackendStatus } from "../api";

export function cx(...c: (string | false | null | undefined)[]) {
  return c.filter(Boolean).join(" ");
}

export function Button({
  children, onClick, variant = "ghost", size = "md", disabled, className, title, icon,
}: {
  children?: ReactNode;
  onClick?: () => void;
  variant?: "primary" | "ghost" | "danger" | "subtle";
  size?: "sm" | "md" | "lg" | "xl";
  disabled?: boolean;
  className?: string;
  title?: string;
  icon?: ReactNode;
}) {
  const sizes = {
    sm: "h-9 px-3 text-[0.8rem] gap-1.5 rounded-lg",
    md: "h-11 px-4 text-[0.95rem] gap-2 rounded-xl",
    lg: "h-14 px-6 text-[1.1rem] gap-2.5 rounded-2xl",
    xl: "h-20 px-10 text-[1.6rem] gap-3 rounded-3xl",
  }[size];
  const variants = {
    primary:
      "bg-fg text-ink-950 font-medium hover:bg-white",
    ghost: "bg-white/[0.04] text-fg border border-line hover:bg-white/[0.08] hover:border-line-strong",
    subtle: "text-fg-2 hover:text-fg hover:bg-white/[0.06]",
    danger:
      "bg-[#e11d48] text-white font-semibold hover:bg-[#f43f5e]",
  }[variant];
  return (
    <motion.button
      type="button"
      title={title}
      disabled={disabled}
      onClick={onClick}
      whileTap={disabled ? undefined : { scale: 0.96 }}
      className={cx(
        "inline-flex select-none items-center justify-center whitespace-nowrap transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-40",
        sizes,
        variants,
        className,
      )}
    >
      {icon}
      {children}
    </motion.button>
  );
}

export function Segmented<T extends string | number>({
  options, value, onChange, size = "md", layoutId,
}: {
  options: { value: T; label: ReactNode; title?: string }[];
  value: T;
  onChange: (v: T) => void;
  size?: "sm" | "md";
  layoutId: string;
}) {
  return (
    <div className="inline-flex rounded-xl border border-line bg-ink-900/80 p-1" role="radiogroup">
      {options.map((o) => {
        const on = o.value === value;
        return (
          <button
            key={String(o.value)}
            type="button"
            role="radio"
            aria-checked={on}
            title={o.title}
            onClick={() => onChange(o.value)}
            className={cx(
              "relative rounded-lg font-medium transition-colors",
              size === "sm" ? "px-2.5 py-1 text-[0.75rem]" : "px-3.5 py-1.5 text-[0.9rem]",
              on ? "text-ink-950" : "text-fg-2 hover:text-fg",
            )}
          >
            {on && (
              <motion.span
                layoutId={layoutId}
                className="absolute inset-0 rounded-lg bg-fg"
                transition={{ type: "spring", stiffness: 500, damping: 38 }}
              />
            )}
            <span className="relative z-10 inline-flex items-center gap-1.5">{o.label}</span>
          </button>
        );
      })}
    </div>
  );
}

export function Chip({ children, color, className }: { children: ReactNode; color?: string; className?: string }) {
  return (
    <span
      className={cx("inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[0.75rem] font-semibold tracking-wide", className)}
      style={{ borderColor: color ? `color-mix(in srgb, ${color} 55%, transparent)` : undefined, color: color ?? undefined, background: color ? `color-mix(in srgb, ${color} 12%, transparent)` : undefined }}
    >
      {children}
    </span>
  );
}

const STATUS_STYLE: Record<BackendStatus | DataSource, { c: string; t: string }> = {
  live: { c: "#3ddc97", t: "LIVE" },
  replay: { c: "#ffb547", t: "REPLAY" },
  offline: { c: "#ff5d73", t: "OFFLINE" },
  mock: { c: "#ff5d73", t: "SIM" },
  checking: { c: "#7a8398", t: "…" },
};

export function StatusDot({ status, label = true }: { status: BackendStatus | DataSource; label?: boolean }) {
  const s = STATUS_STYLE[status];
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="relative inline-flex h-2.5 w-2.5">
        <span className="relative m-auto inline-flex h-1.5 w-1.5 rounded-full" style={{ background: s.c }} />
      </span>
      {label && (
        <span className="text-[0.68rem] uppercase tracking-[0.14em]" style={{ color: s.c }}>
          {s.t}
        </span>
      )}
    </span>
  );
}

/** "LIVE" vs "REPLAY" vs "SIM" for one payload */
export function SourceBadge({ source, backend, error }: { source: DataSource; backend?: BackendId; error?: string }) {
  const title =
    source === "live"
      ? "Live: computed just now by the real store code on this backend"
      : source === "replay"
        ? `Replay: recorded fixture from a real run (backend not reachable${error ? `: ${error}` : ""})`
        : `Offline simulation: backend and fixtures unavailable${error ? ` (${error})` : ""}`;
  return (
    <span title={title} className="inline-flex items-center gap-2 rounded-full border border-line bg-ink-900/80 px-3 py-1">
      {backend && <OsMark id={backend} className="h-3.5 w-3.5" />}
      <StatusDot status={source} />
    </span>
  );
}

/** simple OS marks (generic shapes, no trademarks reproduced) */
export function OsMark({ id, className = "h-4 w-4" }: { id: BackendId | string; className?: string }) {
  if (id === "windows")
    return (
      <svg viewBox="0 0 16 16" className={className} aria-label="Windows">
        <path fill="currentColor" d="M1 2.3 6.6 1.5v5.9H1zM7.4 1.4 15 .3v7.1H7.4zM1 8.2h5.6v5.9L1 13.3zM7.4 8.2H15v7.1l-7.6-1.1z" />
      </svg>
    );
  return (
    <svg viewBox="0 0 16 16" className={className} aria-label="Linux">
      <path fill="currentColor" fillRule="evenodd" d="M8 1.3c2.3 0 3.6 1.9 3.6 4 0 1.3-.4 2.2-.9 3 .9 1.2 1.5 2.7 1.5 4 0 1-.4 2-1.1 2.6H4.9c-.7-.6-1.1-1.6-1.1-2.6 0-1.3.6-2.8 1.5-4-.5-.8-.9-1.7-.9-3 0-2.1 1.3-4 3.6-4Zm0 6.2c-1.4 0-2.6 2.2-2.6 4.4 0 1.4.9 2.4 2.6 2.4s2.6-1 2.6-2.4c0-2.2-1.2-4.4-2.6-4.4Z" />
      <path fill="currentColor" d="M3.4 14.2c.3-1 1.3-1.5 2.4-1l.6 2.3c-1 .6-2.6.2-3-1.3Zm9.2 0c-.3-1-1.3-1.5-2.4-1l-.6 2.3c1 .6 2.6.2 3-1.3Z" />
    </svg>
  );
}

export function SceneTitle({ eyebrow, title, sub, right }: { eyebrow?: ReactNode; title: ReactNode; sub?: ReactNode; right?: ReactNode }) {
  return (
    <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between md:gap-6">
      <div className="min-w-0">
        {eyebrow && (
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
            className="mb-2 text-[0.8rem] uppercase tracking-[0.2em] text-fg-3"
          >
            {eyebrow}
          </motion.div>
        )}
        <motion.h1
          initial={{ opacity: 0, y: 14 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.05, ease: [0.16, 1, 0.3, 1] }}
          className="font-serif text-[3rem] leading-[1.05] font-normal tracking-[-0.01em] text-fg"
        >
          {title}
        </motion.h1>
        {sub && (
          <motion.p
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.12, ease: [0.16, 1, 0.3, 1] }}
            className="mt-2 max-w-[62rem] text-[1.1rem] leading-snug text-fg-2"
          >
            {sub}
          </motion.p>
        )}
      </div>
      {right && <div className="flex shrink-0 flex-wrap items-center gap-3">{right}</div>}
    </div>
  );
}

export function Panel({ children, className, glow }: { children: ReactNode; className?: string; glow?: string }) {
  return (
    <div
      className={cx("relative rounded-2xl border border-line bg-ink-900/70 backdrop-blur-sm", className)}
      style={glow ? { boxShadow: `0 0 0 1px color-mix(in srgb, ${glow} 40%, transparent), 0 0 40px -8px color-mix(in srgb, ${glow} 45%, transparent)` } : undefined}
    >
      {children}
    </div>
  );
}

export function Spinner({ className = "h-5 w-5" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={cx("animate-spin", className)} aria-hidden>
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeOpacity="0.2" strokeWidth="3" />
      <path d="M21 12a9 9 0 0 0-9-9" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}
