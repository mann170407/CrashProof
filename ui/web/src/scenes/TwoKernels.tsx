import { motion } from "motion/react";
import { Gauge, Info as InfoIcon, Play } from "lucide-react";
import { getInfo, getResults, runPlug, type Fetched } from "../api";
import { BACKEND_ORDER, BACKENDS } from "../config";
import { Button, OsMark, SceneTitle, SourceBadge, Spinner, StatusDot, cx } from "../components/ui";
import { useBackendStatus } from "../lib/backend";
import { useHotkeys } from "../lib/keys";
import { useSceneState } from "../lib/store";
import { useLoad } from "../lib/useLoad";
import { fmtMs } from "../lib/format";
import { NATIVE } from "../mocks/sim";
import type { BackendId, PlugExtra, ScenarioResult } from "../types";
import type { SceneProps } from "./types";

const OPS: { op: string; what: string; key?: boolean }[] = [
  { op: "create", what: "create a new file, fail if it exists" },
  { op: "write", what: "write bytes (page cache)" },
  { op: "fsync", what: "force file data to stable storage", key: true },
  { op: "fsync_dir", what: "make a create/rename durable", key: true },
  { op: "rename", what: "atomically replace a name — the commit", key: true },
  { op: "unlink", what: "delete a name (open readers keep reading)" },
  { op: "lock", what: "writers share, recovery excludes" },
  { op: "read", what: "lock-free read that never blocks GC" },
];

export default function TwoKernels({ active }: SceneProps) {
  const status = useBackendStatus();
  const lin = useLoad("info", "linux", getInfo);
  const win = useLoad("info", "windows", getInfo);
  const infos = { linux: lin.data ?? status.linux.info, windows: win.data ?? status.windows.info };
  const resL = useLoad("results", "linux", getResults);
  const resW = useLoad("results", "windows", getResults);
  const bench = { linux: resL.data?.bench ?? null, windows: resW.data?.bench ?? null };
  const [timing, setTiming] = useSceneState<{ loading: boolean; runs: Fetched<ScenarioResult<PlugExtra>>[] }>("kernels:timing", { loading: false, runs: [] });

  const measure = async () => {
    setTiming({ loading: true, runs: [] });
    const runs = await Promise.all(BACKEND_ORDER.map((b) => runPlug(b, null, "atomic")));
    setTiming({ loading: false, runs });
  };
  useHotkeys(active, { r: () => void measure() });

  const maxMs = Math.max(1, ...timing.runs.map((r) => r.data.elapsedMs ?? 0));

  return (
    <div className="flex h-full min-h-0 flex-col gap-5">
      <SceneTitle
        eyebrow="03 · Two kernels"
        title="Two kernels, one protocol."
        sub="The same Python store runs on Linux (ext4, in WSL2) and on Windows (NTFS). Each POSIX step maps to a different native call — the guarantees and every test are identical."
      />
      <div className="grid min-h-0 flex-1 grid-cols-1 gap-5 xl:grid-cols-[1.6fr_1fr]">
        {/* mapping table */}
        <div className="flex min-h-0 flex-col overflow-hidden rounded-2xl border border-line bg-ink-900/70">
          <div className="grid grid-cols-[13rem_minmax(0,1fr)_minmax(0,1fr)] border-b border-line bg-ink-900">
            <div className="px-4 py-3 text-[0.8rem] font-semibold uppercase tracking-[0.14em] text-fg-3">step</div>
            {BACKEND_ORDER.map((b) => (
              <div key={b} className="flex items-center gap-2.5 border-l border-line px-4 py-3">
                <OsMark id={b} className="h-6 w-6" />
                <div className="min-w-0">
                  <div className="flex items-center gap-2 text-[1.05rem] font-semibold">
                    {BACKENDS[b].name} <StatusDot status={status[b].status} />
                  </div>
                  <div className="truncate text-[0.75rem] text-fg-3">{infos[b]?.platform.label ?? "…"}</div>
                </div>
              </div>
            ))}
          </div>
          <div className="scroll-quiet min-h-0 flex-1 overflow-y-auto">
            {OPS.map((o, i) => (
              <motion.div
                key={o.op}
                initial={{ opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.2 + i * 0.06, duration: 0.45, ease: [0.16, 1, 0.3, 1] }}
                className={cx("grid grid-cols-[13rem_minmax(0,1fr)_minmax(0,1fr)] border-b border-line/60", o.key && "bg-brand/[0.07]")}
              >
                <div className="px-4 py-2">
                  <div className={cx("mono text-[1rem] font-bold", o.key ? "text-commit" : "text-fg")}>{o.op}</div>
                  <div className="text-[0.75rem] leading-snug text-fg-3">{o.what}</div>
                </div>
                {BACKEND_ORDER.map((b) => (
                  <div key={b} className="mono flex items-center border-l border-line/60 px-4 py-2 text-[0.9rem] text-fg-2 [overflow-wrap:anywhere]">
                    {infos[b]?.platform.native?.[o.op] ?? NATIVE[b][o.op] ?? "—"}
                  </div>
                ))}
              </motion.div>
            ))}
          </div>
        </div>

        {/* timing + caveats */}
        <div className="flex min-h-0 flex-col gap-4">
          <div className="rounded-2xl border border-line bg-ink-900/70 p-5">
            <div className="flex items-center gap-3">
              <Gauge className="h-6 w-6 text-recover" />
              <div className="text-[1.25rem] font-semibold">One full save, both kernels</div>
              <Button size="md" variant="primary" className="ml-auto" onClick={() => void measure()} disabled={timing.loading} icon={timing.loading ? <Spinner /> : <Play className="h-4 w-4" />}>
                Measure
              </Button>
            </div>
            <div className="mt-4 flex flex-col gap-3">
              {BACKEND_ORDER.map((b) => {
                const r = timing.runs.find((x) => x.backend === b);
                const ms = r?.data.elapsedMs;
                return (
                  <div key={b} className="flex items-center gap-3">
                    <OsMark id={b} className="h-5 w-5" />
                    <div className="h-8 flex-1 overflow-hidden rounded-lg bg-ink-800">
                      <motion.div
                        className="h-full rounded-lg"
                        style={{ background: b === "linux" ? "linear-gradient(90deg,#2dd4bf,#5eead4)" : "linear-gradient(90deg,#4cc2ff,#8cc2ff)" }}
                        initial={{ width: 0 }}
                        animate={{ width: ms != null ? `${Math.max(2, (ms / maxMs) * 100)}%` : 0 }}
                        transition={{ type: "spring", stiffness: 80, damping: 18 }}
                      />
                    </div>
                    <span className="mono w-[6.5rem] text-right text-[1rem] font-bold tabular">{fmtMs(ms)}</span>
                    {r && <SourceBadge source={r.source} />}
                  </div>
                );
              })}
            </div>
            <div className="mt-3 text-[0.8rem] text-fg-3">scenario runtime incl. recovery & read (elapsedMs). Stored write benchmark (1000 B):</div>
            <div className="mt-2 grid grid-cols-2 gap-3">
              {BACKEND_ORDER.map((b) => (
                <BenchCard key={b} b={b} bench={bench[b]} />
              ))}
            </div>
          </div>

          <div className="scroll-quiet min-h-0 flex-1 overflow-y-auto rounded-2xl border border-line bg-ink-900/70 p-5">
            <div className="mb-3 flex items-center gap-2 text-[1.1rem] font-semibold">
              <InfoIcon className="h-5 w-5 text-warn" /> Honest caveats
            </div>
            {BACKEND_ORDER.map((b) => (
              <div key={b} className="mb-3">
                <div className="mb-1 flex items-center gap-2 text-[0.9rem] font-semibold text-fg-2">
                  <OsMark id={b} className="h-4 w-4" /> {BACKENDS[b].name}
                  <span className="mono text-[0.75rem] font-normal text-fg-3">
                    {infos[b]?.platform.kernel} {infos[b]?.platform.fs && `· ${infos[b]?.platform.fs}`} {infos[b]?.platform.python && `· py ${infos[b]?.platform.python}`}
                  </span>
                </div>
                <ul className="list-disc pl-6 text-[0.9rem] leading-snug text-fg-2">
                  {(infos[b]?.platform.caveats ?? []).map((c, i) => (
                    <li key={i}>{c}</li>
                  ))}
                  {(infos[b]?.platform.caveats ?? []).length === 0 && <li className="text-fg-3">none reported</li>}
                </ul>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function BenchCard({ b, bench }: { b: BackendId; bench: Record<string, number> | null }) {
  const a = bench?.atomicdocs_1000B_ms;
  const n = bench?.naive_fsync_1000B_ms;
  return (
    <div className="rounded-xl border border-line bg-ink-950/40 px-3 py-2">
      <div className="flex items-center gap-1.5 text-[0.75rem] text-fg-3">
        <OsMark id={b} className="h-3.5 w-3.5" /> {BACKENDS[b].name}
      </div>
      {a != null ? (
        <div className="mono text-[0.85rem] text-fg-2">
          AtomicDocs <span className="font-bold text-fg">{fmtMs(a)}</span>
          <br />
          naive+fsync <span className="font-bold text-fg">{fmtMs(n)}</span>
        </div>
      ) : (
        <div className="text-[0.8rem] text-fg-3">no stored bench</div>
      )}
    </div>
  );
}
