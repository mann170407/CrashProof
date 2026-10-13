import type { StoredResults } from "../types";

/** Headline numbers derived from /api/results (tolerant of missing pieces). */
export function headline(r: StoredResults | null) {
  const ex = (r?.explorer ?? {}) as Record<string, unknown>;
  let corrupt = 0;
  let oursStates = 0;
  for (const [k, v] of Object.entries(ex)) {
    if (k.startsWith("ours_") && v && typeof v === "object") {
      corrupt += Number((v as Record<string, unknown>).CORRUPT ?? 0);
      oursStates += Number((v as Record<string, unknown>).states ?? 0);
    }
  }
  const states = Number(ex.total_states ?? oursStates ?? 0);
  const cm = r?.crashMatrix as unknown;
  const rows: { trials?: number; corrupt?: number }[] = Array.isArray(cm)
    ? (cm as { trials?: number }[])
    : Array.isArray((cm as { crash_matrix?: unknown })?.crash_matrix)
      ? ((cm as { crash_matrix: { trials?: number }[] }).crash_matrix)
      : [];
  const kills = rows.reduce((s, x) => s + Number(x.trials ?? 0), 0);
  const killCorrupt = rows.reduce((s, x) => s + Number(x.corrupt ?? 0), 0);
  const faults = Object.values(r?.faults ?? {}).filter((v) => v && typeof v === "object" && !Array.isArray(v) && "pass" in (v as object)) as { pass: boolean }[];
  const ch = Object.values(r?.challenges ?? {}).filter(Boolean) as { passed?: number; total?: number; pass?: boolean }[];
  return {
    states,
    corrupt: corrupt + killCorrupt,
    kills,
    faultsPassed: faults.filter((f) => f.pass).length,
    faultsTotal: faults.length,
    checksPassed: ch.reduce((s, c) => s + Number(c.passed ?? 0), 0),
    checksTotal: ch.reduce((s, c) => s + Number(c.total ?? 0), 0),
    challengesPassed: ch.filter((c) => c.pass).length,
    challengesTotal: ch.length,
    naive: (ex.naive_fsync ?? null) as { states?: number; CORRUPT?: number } | null,
  };
}
