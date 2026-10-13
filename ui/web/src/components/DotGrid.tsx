import type { ReactNode } from "react";
import { useMemo } from "react";

type O = "A" | "B" | "X";
const FILL: Record<O, string> = { A: "#3b93f5", B: "#cc7e14", X: "#f0436a" };

/**
 * Every dot = one legal post-crash disk state, recovered and read with the real code.
 * A = old version, B = new version, X = corrupt (drawn as a cross, not just a colour).
 */
export function DotGrid({ sample, maxCols = 40, dot = 14, gap = 5, delayPerDot = 3, label }: { sample: O[]; maxCols?: number; dot?: number; gap?: number; delayPerDot?: number; label?: ReactNode }) {
  const cols = Math.min(maxCols, Math.max(4, Math.ceil(Math.sqrt(sample.length * 2.4))));
  const rows = Math.max(1, Math.ceil(sample.length / cols));
  const step = dot + gap;
  const w = cols * step - gap;
  const h = rows * step - gap;
  const items = useMemo(() => sample.map((o, i) => ({ o, x: (i % cols) * step, y: Math.floor(i / cols) * step, i })), [sample, cols, step]);
  const total = sample.length;
  const budget = 1600; // whole grid animates in within ~1.6 s regardless of size
  const d = Math.min(delayPerDot, budget / Math.max(1, total));
  return (
    <svg viewBox={`-2 -2 ${w + 4} ${h + 4}`} className="block h-full max-h-full w-full" preserveAspectRatio="xMidYMid meet" role="img" aria-label={typeof label === "string" ? label : "power-loss states"}>
      {items.map(({ o, x, y, i }) =>
        o === "X" ? (
          <g key={i} className="dot" style={{ animationDelay: `${i * d}ms` }}>
            <rect x={x} y={y} width={dot} height={dot} rx={3} fill="rgba(240,67,106,0.18)" stroke={FILL.X} strokeWidth={1.5} />
            <path d={`M${x + 3.5} ${y + 3.5}L${x + dot - 3.5} ${y + dot - 3.5}M${x + dot - 3.5} ${y + 3.5}L${x + 3.5} ${y + dot - 3.5}`} stroke={FILL.X} strokeWidth={2.2} strokeLinecap="round" />
          </g>
        ) : (
          <circle key={i} className="dot" style={{ animationDelay: `${i * d}ms` }} cx={x + dot / 2} cy={y + dot / 2} r={dot / 2} fill={FILL[o]} />
        ),
      )}
    </svg>
  );
}

export function Legend() {
  return (
    <div className="flex items-center gap-5 text-[0.9rem] text-fg-2">
      <span className="flex items-center gap-2"><span className="h-3.5 w-3.5 rounded-full" style={{ background: FILL.A }} /> A — old version</span>
      <span className="flex items-center gap-2"><span className="h-3.5 w-3.5 rounded-full" style={{ background: FILL.B }} /> B — new version</span>
      <span className="flex items-center gap-2">
        <svg viewBox="0 0 14 14" className="h-3.5 w-3.5"><rect x="0.5" y="0.5" width="13" height="13" rx="3" fill="rgba(240,67,106,0.18)" stroke={FILL.X} /><path d="M3.5 3.5l7 7M10.5 3.5l-7 7" stroke={FILL.X} strokeWidth="2" strokeLinecap="round" /></svg>
        corrupt / torn
      </span>
    </div>
  );
}
