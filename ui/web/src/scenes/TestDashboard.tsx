import { AnimatePresence, motion } from "motion/react";
import { PlayCircle, Square, Timer, CheckCircle2, XCircle, CircleDashed } from "lucide-react";
import { useEffect, useRef } from "react";
import { getResults, streamTests } from "../api";
import { BACKEND_ORDER, BACKENDS } from "../config";
import { Button, OsMark, SceneTitle, Segmented, StatusDot, Spinner, cx } from "../components/ui";
import { useBackend, useBackendStatus } from "../lib/backend";
import { useHotkeys } from "../lib/keys";
import { useSceneState } from "../lib/store";
import { useLoad } from "../lib/useLoad";
import { headline } from "../lib/results";
import type { BackendId, DataSource, StoredResults } from "../types";
import type { SceneProps } from "./types";

// same list and order as atomicdocs/run_all.py (SUITES + CHALLENGES)
const SUITES: { name: string; title: string }[] = [
  { name: "crash_explorer", title: "simulated power loss at every syscall" },
  { name: "fault_test", title: "injected I/O faults, corruption, concurrency" },
  { name: "crash_test", title: "real process kill at every crash point" },
  { name: "c1", title: "KO-04-C1 commit-point crash matrix" },
  { name: "c2", title: "KO-04-C2 lost-update rejection" },
  { name: "c3", title: "KO-04-C3 retention under read pressure" },
  { name: "c4", title: "KO-04-C4 failure after commit before flush" },
  { name: "c5", title: "KO-04-C5 interrupted recovery" },
];

type Status = "pending" | "running" | "pass" | "fail";
interface Card {
  name: string;
  title: string;
  status: Status;
  secs?: number;
  lines: string[];
  last?: string;
  stored?: string;
}
interface RunState {
  cards: Record<string, Card>;
  running: boolean;
  source: DataSource | null;
  done: { passed: number; total: number } | null;
  t0: number;
  error?: string;
}

const CHALLENGE_NAMES = ["c1", "c2", "c3", "c4", "c5"];
type Scope = "all" | "challenges";
const listFor = (scope: Scope) => (scope === "all" ? SUITES : SUITES.filter((s) => CHALLENGE_NAMES.includes(s.name)));

const fresh = (): RunState => ({
  cards: Object.fromEntries(SUITES.map((s) => [s.name, { ...s, status: "pending" as Status, lines: [] }])),
  running: false,
  source: null,
  done: null,
  t0: 0,
});

function storedSummary(r: StoredResults | null, name: string): { pass: boolean; text: string } | null {
  if (!r) return null;
  const h = headline(r);
  if (name === "crash_explorer" && r.explorer) return { pass: h.corrupt === 0, text: `${h.states} power-loss states · 0 corrupt` };
  if (name === "fault_test" && r.faults) return { pass: h.faultsPassed === h.faultsTotal, text: `${h.faultsPassed}/${h.faultsTotal} fault scenarios` };
  if (name === "crash_test" && r.crashMatrix) return { pass: true, text: `${h.kills} process kills · 0 corrupt` };
  const m = /^c(\d)$/.exec(name);
  if (m) {
    const c = r.challenges?.[`KO-04-C${m[1]}`];
    if (c) return { pass: !!c.pass, text: `${c.passed ?? "?"}/${c.total ?? "?"} checks` };
  }
  return null;
}

export default function TestDashboard({ active }: SceneProps) {
  const backend = useBackend();
  const [mode, setMode] = useSceneState<BackendId | "both">("tests:mode", backend);
  const sides: BackendId[] = mode === "both" ? BACKEND_ORDER : [mode];
  const [scope, setScope] = useSceneState<Scope>("tests:scope", "all");
  const list = listFor(scope);
  const [runs, setRuns] = useSceneState<Record<BackendId, RunState>>("tests:runs", { linux: fresh(), windows: fresh() });
  const stopRef = useRef<Record<string, () => void>>({});
  const resL = useLoad("results", "linux", getResults);
  const resW = useLoad("results", "windows", getResults);
  const stored = { linux: resL.data, windows: resW.data };

  // Stream events arrive in bursts (C5 prints 142 checks); applying each one as its own store update
  // trips React's nested-update limit (#185). Queue them and apply once per animation frame.
  const queue = useRef<{ b: BackendId; f: (s: RunState) => RunState }[]>([]);
  const frame = useRef(0);
  const flush = () => {
    frame.current = 0;
    const q = queue.current;
    queue.current = [];
    if (q.length) setRuns((all) => q.reduce((acc, { b, f }) => ({ ...acc, [b]: f(acc[b]) }), all));
  };
  const patch = (b: BackendId, f: (s: RunState) => RunState) => {
    queue.current.push({ b, f });
    if (!frame.current) frame.current = requestAnimationFrame(flush);
  };
  const patchCard = (b: BackendId, name: string, f: (c: Card) => Card) =>
    patch(b, (s) => ({ ...s, cards: { ...s.cards, [name]: f(s.cards[name] ?? { name, title: name, status: "pending", lines: [] }) } }));

  const replay = (b: BackendId) => {
    // backend not live: replay the stored results of the last full run, clearly labelled
    patch(b, (s) => ({ ...s, source: "replay", running: true }));
    const r = b === "linux" ? resL.data : resW.data;
    let k = 0;
    const next = () => {
      if (k >= list.length) {
        patch(b, (s) => {
          const vals = list.map((x) => s.cards[x.name]);
          return { ...s, running: false, done: { passed: vals.filter((c) => c.status === "pass").length, total: vals.length } };
        });
        return;
      }
      const sname = list[k++].name;
      patchCard(b, sname, (c) => ({ ...c, status: "running", lines: ["(replay) stored result of the last full run"] }));
      const t = setTimeout(() => {
        const sm = storedSummary(r, sname);
        patchCard(b, sname, (c) => ({ ...c, status: sm ? (sm.pass ? "pass" : "fail") : "fail", last: sm?.text ?? "no stored result", secs: undefined }));
        next();
      }, 700);
      stopRef.current[b] = () => clearTimeout(t);
    };
    next();
  };

  const start = (b: BackendId) => {
    stopRef.current[b]?.();
    queue.current = queue.current.filter((x) => x.b !== b); // drop leftovers of the previous run
    setRuns((all) => ({ ...all, [b]: { ...fresh(), running: true, t0: Date.now(), source: "live" } }));
    stopRef.current[b] = streamTests(b, scope === "all" ? null : CHALLENGE_NAMES, {
      onStart: (d) => patchCard(b, d.name, (c) => ({ ...c, title: d.title || c.title, status: "running", lines: [] })),
      onLine: (d) => patchCard(b, d.name, (c) => ({ ...c, lines: [...c.lines.slice(-30), d.text] })),
      onEnd: (d) => patchCard(b, d.name, (c) => ({ ...c, status: d.pass ? "pass" : "fail", secs: d.secs, last: d.last })),
      onDone: (d) => patch(b, (s) => ({ ...s, running: false, done: d })),
      onError: (msg, any) => {
        if (!any) replay(b);
        else patch(b, (s) => ({ ...s, running: false, error: msg }));
      },
    });
  };
  const runAll = () => sides.forEach(start);
  const stopAll = () => {
    sides.forEach((b) => stopRef.current[b]?.());
    sides.forEach((b) => patch(b, (s) => ({ ...s, running: false })));
  };
  useEffect(() => () => Object.values(stopRef.current).forEach((f) => f()), []);
  useHotkeys(active, { r: () => runAll() });

  const anyRunning = sides.some((b) => runs[b].running);

  return (
    <div className="flex h-full min-h-0 flex-col gap-4">
      <SceneTitle
        eyebrow="10 · Everything, live"
        title="Run every suite. Right now."
        sub="Simulated power loss, injected I/O faults, real process kills and the five KO-04 challenges — each a separate process, streamed as it runs."
        right={
          <>
            <Segmented
              layoutId="tests-scope"
              value={scope}
              onChange={(v) => !anyRunning && setScope(v)}
              options={[
                { value: "all", label: "All 8 suites", title: "crash_explorer, fault_test, crash_test + C1..C5 (several minutes)" },
                { value: "challenges", label: "C1–C5 only", title: "the five live challenges (about a minute)" },
              ]}
            />
            <Segmented
              layoutId="tests-mode"
              value={mode}
              onChange={setMode}
              options={[
                { value: "linux", label: <><OsMark id="linux" /> Linux</> },
                { value: "windows", label: <><OsMark id="windows" /> Windows</> },
                { value: "both", label: "Both" },
              ]}
            />
            {anyRunning ? (
              <Button variant="ghost" size="lg" onClick={stopAll} icon={<Square className="h-5 w-5" />}>
                Stop
              </Button>
            ) : (
              <Button variant="primary" size="lg" onClick={runAll} icon={<PlayCircle className="h-6 w-6" />}>
                Run everything
              </Button>
            )}
          </>
        }
      />
      <div className={cx("grid min-h-0 flex-1 gap-5", sides.length > 1 ? "grid-cols-1 xl:grid-cols-2" : "grid-cols-1")}>
        {sides.map((b) => (
          <Board key={b} b={b} st={runs[b]} stored={stored[b]} compact={sides.length > 1} list={list} />
        ))}
      </div>
    </div>
  );
}

function Board({ b, st, stored, compact, list }: { b: BackendId; st: RunState; stored: StoredResults | null; compact: boolean; list: typeof SUITES }) {
  const status = useBackendStatus();
  const vals = list.map((s) => st.cards[s.name]);
  const finished = vals.filter((c) => c.status === "pass" || c.status === "fail").length;
  const passed = vals.filter((c) => c.status === "pass").length;
  const elapsed = st.t0 ? Math.round((Date.now() - st.t0) / 1000) : 0;
  return (
    <div className="flex min-h-0 flex-col gap-3">
      <div className="flex items-center gap-3 rounded-2xl border border-line bg-ink-900/70 px-4 py-2.5">
        <OsMark id={b} className="h-6 w-6" />
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-[1.05rem] font-semibold">
            {BACKENDS[b].name} <StatusDot status={status[b].status} />
          </div>
          <div className="truncate text-[0.75rem] text-fg-3">{status[b].info?.platform.label}</div>
        </div>
        <div className="ml-4 h-2.5 flex-1 overflow-hidden rounded-full bg-ink-800">
          <motion.div className="h-full rounded-full bg-gradient-to-r from-brand to-pass" animate={{ width: `${(finished / list.length) * 100}%` }} transition={{ type: "spring", stiffness: 80, damping: 20 }} />
        </div>
        <div className="mono w-[5rem] text-right text-[1.1rem] font-bold tabular">
          {passed}/{list.length}
        </div>
        {st.source && st.source !== "live" && <span className="mono rounded bg-warn/15 px-2 text-[0.7rem] font-bold text-warn">REPLAY</span>}
        {st.running && st.source === "live" && <span className="mono text-[0.8rem] text-fg-3 tabular">{elapsed}s</span>}
      </div>
      {st.done && (
        <motion.div initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }} className={cx("rounded-xl px-4 py-2 text-[1.1rem] font-bold", st.done.passed === st.done.total ? "bg-pass/15 text-pass" : "bg-fail/15 text-fail")}>
          {st.done.passed === st.done.total ? "✓" : "✗"} {st.done.passed}/{st.done.total} passed
        </motion.div>
      )}
      {st.error && <div className="rounded-xl bg-fail/10 px-4 py-2 text-fail">{st.error}</div>}
      <div className={cx("grid min-h-0 flex-1 auto-rows-fr gap-3", compact ? "grid-cols-2" : list.length === 5 ? "grid-cols-2 lg:grid-cols-5" : "grid-cols-2 lg:grid-cols-4")}>
        {list.map((s, i) => (
          <SuiteCard key={s.name} c={st.cards[s.name]} i={i} stored={storedSummary(stored, s.name)} compact={compact} />
        ))}
      </div>
    </div>
  );
}

function SuiteCard({ c, i, stored, compact }: { c: Card; i: number; stored: { pass: boolean; text: string } | null; compact: boolean }) {
  const done = c.status === "pass" || c.status === "fail";
  return (
    <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.05 }} className="relative min-h-[7rem]" style={{ perspective: 1200 }}>
      <motion.div className="relative h-full w-full" style={{ transformStyle: "preserve-3d" }} animate={{ rotateY: done ? 180 : 0 }} transition={{ type: "spring", stiffness: 120, damping: 16 }}>
        {/* front: pending / running */}
        <div
          className={cx("absolute inset-0 flex flex-col overflow-hidden rounded-2xl border bg-ink-900/80 p-4", c.status === "running" ? "border-brand shadow-[0_0_40px_-10px_rgba(139,123,255,0.8)]" : "border-line")}
          style={{ backfaceVisibility: "hidden" }}
        >
          <div className="flex items-center gap-2">
            {c.status === "running" ? <Spinner className="h-5 w-5 text-brand" /> : <CircleDashed className="h-5 w-5 text-fg-3" />}
            <span className="mono text-[1.05rem] font-bold">{c.name}</span>
          </div>
          <div className="mt-1 text-[0.82rem] leading-snug text-fg-2">{c.title}</div>
          {c.status === "running" ? (
            <div className="mono mt-2 min-h-0 flex-1 overflow-hidden text-[0.68rem] leading-snug text-fg-3">
              <AnimatePresence initial={false}>
                {c.lines.slice(compact ? -3 : -5).map((l, k) => (
                  <motion.div key={c.lines.length - k + l} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className="truncate">
                    {l}
                  </motion.div>
                ))}
              </AnimatePresence>
            </div>
          ) : (
            stored && (
              <div className="mt-auto flex items-center gap-1.5 text-[0.75rem] text-fg-3">
                last run: <span className={stored.pass ? "text-pass" : "text-fail"}>{stored.pass ? "PASS" : "FAIL"}</span> · {stored.text}
              </div>
            )
          )}
        </div>
        {/* back: result */}
        <div
          className={cx("absolute inset-0 flex flex-col justify-between rounded-2xl border-2 p-4", c.status === "pass" ? "border-pass/60 bg-pass/10" : "border-fail/70 bg-fail/10")}
          style={{ backfaceVisibility: "hidden", transform: "rotateY(180deg)" }}
        >
          <div className="flex items-center gap-2">
            {c.status === "pass" ? <CheckCircle2 className="h-6 w-6 text-pass" /> : <XCircle className="h-6 w-6 text-fail" />}
            <span className="mono text-[1.05rem] font-bold">{c.name}</span>
            <span className={cx("ml-auto text-[1.3rem] font-extrabold", c.status === "pass" ? "text-pass" : "text-fail")}>{c.status === "pass" ? "PASS" : "FAIL"}</span>
          </div>
          <div className="mono line-clamp-2 text-[0.75rem] text-fg-2" title={c.last}>
            {c.last}
          </div>
          <div className="flex items-center gap-1 text-[0.8rem] text-fg-3 tabular">
            <Timer className="h-3.5 w-3.5" /> {c.secs != null ? `${c.secs.toFixed(1)} s` : "replay"}
          </div>
        </div>
      </motion.div>
    </motion.div>
  );
}
