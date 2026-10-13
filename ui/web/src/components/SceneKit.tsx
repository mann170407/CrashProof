import { AnimatePresence, motion } from "motion/react";
import { Columns2, Play, Activity, Sparkles } from "lucide-react";
import { useCallback, type ReactNode } from "react";
import type { Fetched } from "../api";
import { BACKEND_ORDER, BACKENDS } from "../config";
import { useBackend, useCompare } from "../lib/backend";
import { useHotkeys } from "../lib/keys";
import { useSceneState } from "../lib/store";
import type { BackendId, ScenarioResult } from "../types";
import { TimelinePlayer, type PlayerRun } from "./TimelinePlayer";
import { Button, OsMark, SceneTitle, Segmented, SourceBadge, Spinner } from "./ui";

export interface RunnerState<X> {
  runs: PlayerRun<X>[];
  loading: BackendId[];
  runId: number;
  error?: string;
}

/** runs a scenario on the selected backend, or on both in Compare mode; results persist per scene */
export function useRunner<X>(key: string, run: (b: BackendId) => Promise<Fetched<ScenarioResult<X>>>) {
  const backend = useBackend();
  const [compare] = useCompare();
  const [st, setSt] = useSceneState<RunnerState<X>>(key, { runs: [], loading: [], runId: 0 });
  const go = useCallback(async (override?: (b: BackendId) => Promise<Fetched<ScenarioResult<X>>>) => {
    const bs = compare ? BACKEND_ORDER : [backend];
    setSt((s) => ({ ...s, loading: bs, error: undefined }));
    try {
      const res = await Promise.all(bs.map((b) => (override ?? run)(b)));
      setSt((s) => ({
        runs: res.map((r) => ({ backend: r.backend, result: r.data, source: r.source, error: r.error })),
        loading: [],
        runId: s.runId + 1,
      }));
    } catch (e) {
      setSt((s) => ({ ...s, loading: [], error: (e as Error).message }));
    }
  }, [compare, backend, run, setSt]);
  // show only what matches the current mode/backend (stale runs from the other mode stay cached until rerun)
  const visible = compare ? st.runs : st.runs.filter((r) => r.backend === backend).slice(0, 1);
  return { state: st, runs: visible.length ? visible : st.runs.slice(0, compare ? 2 : 1), go, loading: st.loading.length > 0 };
}

export function CompareToggle() {
  const [compare, setCompare] = useCompare();
  const backend = useBackend();
  return (
    <Segmented
      layoutId="compare-toggle"
      value={compare ? "both" : "one"}
      onChange={(v) => setCompare(v === "both")}
      options={[
        { value: "one", label: <><OsMark id={backend} className="h-4 w-4" /> {BACKENDS[backend].name}</>, title: "Run on the selected backend" },
        { value: "both", label: <><Columns2 className="h-4 w-4" /> Compare</>, title: "Run on Linux AND Windows, side by side (C)" },
      ]}
    />
  );
}

export function LoadingVeil({ loading, what }: { loading: BackendId[]; what: string }) {
  return (
    <AnimatePresence>
      {loading.length > 0 && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          className="absolute inset-0 z-30 flex items-center justify-center rounded-2xl bg-ink-950/70 backdrop-blur-sm"
        >
          <div className="flex flex-col items-center gap-4">
            <Spinner className="h-12 w-12 text-commit" />
            <div className="text-[1.3rem] font-semibold">{what}</div>
            <div className="flex gap-3">
              {loading.map((b) => (
                <span key={b} className="inline-flex items-center gap-2 rounded-full border border-line px-3 py-1 text-[0.9rem] text-fg-2">
                  <OsMark id={b} /> {BACKENDS[b].name}
                </span>
              ))}
            </div>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

export interface ChallengeSceneProps<X> {
  active: boolean;
  code: string;
  level?: string;
  title: string;
  text: string;
  runKey: string;
  run: (b: BackendId) => Promise<Fetched<ScenarioResult<X>>>;
  controls?: ReactNode;
  insightLabel: string;
  insight: (runs: PlayerRun<X>[]) => ReactNode;
  overlay?: (run: PlayerRun<X>, index: number) => ReactNode;
  emptyHint: ReactNode;
}

export function ChallengeScene<X>(p: ChallengeSceneProps<X>) {
  const { runs, go, loading, state } = useRunner<X>(p.runKey, p.run);
  const [view, setView] = useSceneState<"timeline" | "insight">(p.runKey + ":view", "timeline");
  useHotkeys(p.active, {
    r: () => void go(),
    i: () => setView(view === "timeline" ? "insight" : "timeline"),
  });
  const has = runs.length > 0;

  return (
    <div className="flex h-full min-h-0 flex-col gap-4">
      <SceneTitle
        eyebrow={
          <span className="inline-flex items-center gap-3">
            {p.code}
            {p.level && <span className="rounded border border-brand/50 px-1.5 text-[0.7rem] tracking-widest">{p.level}</span>}
            <span className="text-fg-3">· live challenge</span>
          </span>
        }
        title={p.title}
        sub={<span className="italic">“{p.text}”</span>}
        right={
          <>
            <CompareToggle />
            <Button variant="primary" size="lg" onClick={() => void go()} disabled={loading} icon={loading ? <Spinner /> : <Play className="h-5 w-5" />} title="Run live (R)">
              {has ? "Run again" : "Run"}
            </Button>
          </>
        }
      />

      {(has || p.controls) && (
        <div className="flex min-h-[2.8rem] items-center gap-3">
          {has && (
            <Segmented
              layoutId={`${p.runKey}-view`}
              value={view}
              onChange={setView}
              options={[
                { value: "timeline", label: <><Activity className="h-4 w-4" /> Live timeline</> },
                { value: "insight", label: <><Sparkles className="h-4 w-4" /> {p.insightLabel}</> },
              ]}
            />
          )}
          {p.controls && <div className="flex items-center gap-2">{p.controls}</div>}
          <div className="ml-auto flex items-center gap-2">
            {runs.map((r) => (
              <SourceBadge key={r.backend} source={r.source} backend={r.backend} error={r.error} />
            ))}
          </div>
        </div>
      )}

      <div className="relative min-h-0 flex-1">
        {state.error && <div className="mb-2 rounded-xl border border-fail/50 bg-fail/10 px-4 py-2 text-fail">{state.error}</div>}
        {!has ? (
          <EmptyState onRun={() => void go()} loading={loading} hint={p.emptyHint} />
        ) : (
          <AnimatePresence mode="wait">
            <motion.div
              key={view}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
              transition={{ duration: 0.3 }}
              className="h-full min-h-0"
            >
              {view === "timeline" ? (
                <TimelinePlayer runs={runs} resetKey={state.runId} active={p.active} overlay={p.overlay} />
              ) : (
                <div className="h-full min-h-0 overflow-y-auto scroll-quiet">{p.insight(runs)}</div>
              )}
            </motion.div>
          </AnimatePresence>
        )}
        <LoadingVeil loading={state.loading} what="Running the real store code…" />
      </div>
    </div>
  );
}

function EmptyState({ onRun, loading, hint }: { onRun: () => void; loading: boolean; hint: ReactNode }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-8 rounded-3xl border border-dashed border-line-strong bg-ink-900/40 p-10 text-center">
      <div className="max-w-[52rem] text-[1.25rem] leading-relaxed text-fg-2">{hint}</div>
      <motion.div animate={{ scale: [1, 1.04, 1] }} transition={{ duration: 2.4, repeat: Infinity, ease: "easeInOut" }}>
        <Button variant="primary" size="xl" onClick={onRun} disabled={loading} icon={loading ? <Spinner className="h-7 w-7" /> : <Play className="h-7 w-7" />}>
          Run it live
        </Button>
      </motion.div>
      <div className="text-[0.9rem] text-fg-3">
        <kbd>R</kbd> run · <kbd>C</kbd> compare kernels · <kbd>Space</kbd> play/pause · <kbd>,</kbd> <kbd>.</kbd> step · <kbd>I</kbd> insight
      </div>
    </div>
  );
}

export function Stat({ label, value, color, sub }: { label: ReactNode; value: ReactNode; color?: string; sub?: ReactNode }) {
  return (
    <div className="rounded-2xl border border-line bg-ink-900/70 px-5 py-4">
      <div className="text-[0.8rem] font-semibold uppercase tracking-[0.14em] text-fg-3">{label}</div>
      <div className="mt-1 text-[2.6rem] font-bold leading-none tracking-tight tabular" style={{ color }}>
        {value}
      </div>
      {sub && <div className="mt-1.5 text-[0.85rem] text-fg-2">{sub}</div>}
    </div>
  );
}
