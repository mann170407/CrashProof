import { AnimatePresence, motion } from "motion/react";
import { Ban, CircleHelp, Crown, Fingerprint, Pause, Play, Skull, Undo2 } from "lucide-react";
import { useEffect, useState } from "react";
import { getInfo, runChallenge } from "../api";
import { BACKENDS } from "../config";
import { ChallengeScene } from "../components/SceneKit";
import type { PlayerRun } from "../components/TimelinePlayer";
import { AnimatedTick } from "../components/TimelinePlayer";
import { CountUp } from "../components/CountUp";
import { OsMark, Segmented, cx } from "../components/ui";
import { useBackend } from "../lib/backend";
import { useSceneState } from "../lib/store";
import { useLoad } from "../lib/useLoad";
import { fmtMs, letterColor, prettyPoint } from "../lib/format";
import type { C1Extra, C2Extra, C3Extra, C4Extra, C5Extra } from "../types";
import type { SceneProps } from "./types";

// ------------------------------------------------------------------ helpers
function PerBackend<X>({ runs, children }: { runs: PlayerRun<X>[]; children: (r: PlayerRun<X>) => React.ReactNode }) {
  return (
    <div className={cx("grid gap-5", runs.length > 1 ? "grid-cols-1 xl:grid-cols-2" : "grid-cols-1")}>
      {runs.map((r) => (
        <div key={r.backend} className="flex min-w-0 flex-col gap-3">
          {runs.length > 1 && (
            <div className="flex items-center gap-2 text-[1rem] font-semibold">
              <OsMark id={r.backend} className="h-5 w-5" /> {r.result.platform?.label ?? BACKENDS[r.backend].name}
              <span className="ml-auto text-[0.85rem] font-normal text-fg-3 tabular">{fmtMs(r.result.elapsedMs)}</span>
            </div>
          )}
          {children(r)}
        </div>
      ))}
    </div>
  );
}

function Letter({ l, size = "3.5rem" }: { l: string; size?: string }) {
  const bad = l !== "A" && l !== "B" && l !== "C";
  const c = letterColor(bad ? "X" : l);
  return (
    <span
      className={cx("inline-flex items-center justify-center rounded-xl font-extrabold", bad && "hatch-x")}
      style={{ width: size, height: size, fontSize: `calc(${size} * 0.6)`, background: c.bg, color: c.text, boxShadow: `inset 0 0 0 2px ${c.mark}` }}
    >
      {bad ? "✗" : l}
    </span>
  );
}

// ------------------------------------------------------------------ C1
export function C1({ active }: SceneProps) {
  return (
    <ChallengeScene<C1Extra>
      active={active}
      code="KO-04-C1"
      level="EASY"
      title="Commit-point crash matrix"
      text="Initialize A, save B with failure before CURRENT rename and after CURRENT rename, then reopen through recovery."
      runKey="c1"
      run={(b) => runChallenge(b, "c1")}
      insightLabel="Matrix"
      emptyHint={
        <>
          A real child process saves B and is killed with <span className="mono text-fg">os._exit</span> — once just <b className="text-va-text">before</b> the
          CURRENT rename, once just <b className="text-vb-text">after</b> it. Recovery must give exactly A, then exactly B.
        </>
      }
      insight={(runs) => (
        <PerBackend runs={runs}>
          {(r) => (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              {(r.result.extra?.runs ?? []).map((x, i) => (
                <motion.div
                  key={x.crashAt}
                  initial={{ opacity: 0, y: 20, rotateX: -30 }}
                  animate={{ opacity: 1, y: 0, rotateX: 0 }}
                  transition={{ delay: 0.15 + i * 0.2, type: "spring", stiffness: 200, damping: 20 }}
                  className={cx("rounded-3xl border-2 bg-ink-900/70 p-6", x.pass ? "border-pass/50" : "border-fail/60")}
                >
                  <div className="mono text-[0.85rem] uppercase tracking-widest text-fg-3">crash</div>
                  <div className="mono text-[1.5rem] font-bold">{prettyPoint(x.crashAt)}</div>
                  <div className="mt-5 flex items-center gap-6">
                    <div className="flex flex-col items-center gap-1">
                      <span className="text-[0.8rem] text-fg-3">expected</span>
                      <Letter l={x.expected} />
                    </div>
                    <span className="text-[2rem] text-fg-3">→</span>
                    <div className="flex flex-col items-center gap-1">
                      <span className="text-[0.8rem] text-fg-3">got after recovery</span>
                      <Letter l={x.got} size="4.5rem" />
                    </div>
                    <div className="ml-auto">
                      <AnimatedTick pass={x.pass} size="3.5rem" delay={0.5 + i * 0.2} />
                    </div>
                  </div>
                  <div className="mt-4 text-[0.95rem] text-fg-2">
                    {x.expected === "A" ? "Rolled back: uncommitted v2 deleted, CURRENT still names v1." : "Rolled forward: CURRENT already named v2 — the save is kept."}
                  </div>
                </motion.div>
              ))}
            </div>
          )}
        </PerBackend>
      )}
    />
  );
}

// ------------------------------------------------------------------ C2
export function C2({ active }: SceneProps) {
  const [clients, setClients] = useSceneState<number>("c2:clients", 8);
  return (
    <ChallengeScene<C2Extra>
      active={active}
      code="KO-04-C2"
      level="MEDIUM"
      title="Lost-update rejection"
      text="Use two clients reading version v1. Commit one update, then try the other with expect=v1."
      runKey="c2"
      run={(b) => runChallenge(b, "c2", { clients })}
      controls={
        <div className="flex items-center gap-2">
          <span className="text-[0.85rem] text-fg-3">race</span>
          <Segmented layoutId="c2-clients" size="sm" value={clients} onChange={setClients} options={[2, 4, 8, 12, 16].map((n) => ({ value: n, label: `${n}` }))} />
        </div>
      }
      insightLabel="The race"
      emptyHint={
        <>
          Two clients read v1. client1 commits B with <span className="mono text-fg">expect=1</span>; client2's <span className="mono text-fg">put(C, expect=1)</span>{" "}
          must be rejected — no lost update. Then {clients} real processes race for the same version: exactly one may win.
        </>
      }
      insight={(runs) => <PerBackend runs={runs}>{(r) => <Race extra={r.result.extra} />}</PerBackend>}
    />
  );
}

function Race({ extra }: { extra?: C2Extra }) {
  const race = extra?.race ?? [];
  const t0 = Math.min(...race.map((r) => r.startMs));
  const t1 = Math.max(...race.map((r) => r.endMs));
  const span = Math.max(1, t1 - t0);
  const [done, setDone] = useState(false);
  useEffect(() => {
    setDone(false);
    const t = setTimeout(() => setDone(true), 2600);
    return () => clearTimeout(t);
  }, [extra]);
  const c = extra?.contrastNoCas;
  return (
    <div className="grid grid-cols-1 gap-5 2xl:grid-cols-[1.6fr_1fr]">
      <div className="rounded-2xl border border-line bg-ink-900/70 p-5">
        <div className="mb-3 flex items-baseline gap-3">
          <div className="text-[1.2rem] font-semibold">{race.length} processes · put(expect=1) at the same instant</div>
          <div className="ml-auto text-[0.8rem] text-fg-3 tabular">{fmtMs(span)} wall clock</div>
        </div>
        <div className="flex flex-col gap-2">
          {race.map((r, i) => {
            const won = r.outcome === "committed";
            const left = ((r.startMs - t0) / span) * 100;
            const width = Math.max(1.5, ((r.endMs - r.startMs) / span) * 100);
            return (
              <div key={r.client} className="grid grid-cols-[5.5rem_1fr_9rem] items-center gap-3">
                <span className="mono text-[0.95rem] text-fg-2">client {r.client}</span>
                <div className="relative h-8 rounded-md bg-ink-800">
                  <motion.div
                    className="absolute inset-y-0 rounded-md"
                    style={{ left: `${left}%`, background: won ? "linear-gradient(90deg,#8b7bff,#b9a8ff)" : "rgba(255,255,255,0.18)" }}
                    initial={{ width: 0 }}
                    animate={{ width: `${width}%` }}
                    transition={{ duration: 1.6 * (width / 100) + 0.4, delay: 0.2 + (left / 100) * 1.2 + i * 0.02, ease: "easeOut" }}
                  />
                  {done && won && <motion.div className="absolute inset-0 rounded-md" initial={{ opacity: 0 }} animate={{ opacity: [0, 1, 0.6] }} style={{ boxShadow: "0 0 24px 4px rgba(139,123,255,0.6)" }} />}
                </div>
                <AnimatePresence>
                  {done && (
                    <motion.span
                      initial={{ opacity: 0, x: won ? 0 : 12 }}
                      animate={won ? { opacity: 1, scale: [0.6, 1.15, 1] } : { opacity: 1, x: [12, -6, 3, 0] }}
                      transition={{ duration: 0.5, delay: i * 0.03 }}
                      className={cx("inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-[0.8rem] font-bold", won ? "bg-commit text-ink-950" : "bg-fail/15 text-fail")}
                    >
                      {won ? <Crown className="h-4 w-4" /> : <Undo2 className="h-4 w-4" />}
                      {won ? `COMMITTED v${r.version ?? 2}` : "CONFLICT"}
                    </motion.span>
                  )}
                </AnimatePresence>
              </div>
            );
          })}
        </div>
      </div>
      {c && (
        <div className="flex flex-col gap-4 rounded-2xl border border-fail/40 bg-ink-900/70 p-5">
          <div className="text-[1.2rem] font-semibold">Same race, no compare-and-swap</div>
          <div className="text-[0.95rem] text-fg-2">{c.clients} processes each read a counter, add 1, write it back.</div>
          <div className="grid grid-cols-2 gap-3">
            <div className="rounded-xl bg-ink-800 p-4 text-center">
              <div className="text-[0.8rem] uppercase tracking-widest text-fg-3">expected</div>
              <div className="text-[3.4rem] font-bold leading-none text-fg"><CountUp value={c.expectedCount} delay={0.3} /></div>
            </div>
            <div className="hatch-x rounded-xl bg-ink-800 p-4 text-center">
              <div className="text-[0.8rem] uppercase tracking-widest text-fg-3">actual</div>
              <div className="text-[3.4rem] font-bold leading-none text-vx-text"><CountUp value={c.finalCount} delay={0.6} /></div>
            </div>
          </div>
          <div className="text-[1.15rem] font-semibold text-fail">
            ✗ {Math.max(0, c.expectedCount - c.finalCount)} lost updates
          </div>
          <div className="text-[0.9rem] text-fg-2">With <span className="mono text-fg">expect=v</span>, the version check and the commit happen under the per-document lock: losers get CONFLICT, re-read, retry.</div>
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ C3
export function C3({ active }: SceneProps) {
  return (
    <ChallengeScene<C3Extra>
      active={active}
      code="KO-04-C3"
      level="MEDIUM"
      title="Retention under read pressure"
      text="Keep retention at three versions and a paused reader of v1 while writers create v2..v6. Demonstrate your bounded read retry/retention behavior."
      runKey="c3"
      run={(b) => runChallenge(b, "c3")}
      insightLabel="Retention"
      emptyHint={
        <>
          A reader reads <span className="mono text-fg">CURRENT → v1</span> and freezes. Meanwhile five saves create v2…v6 and garbage collection keeps only the newest
          three. When the reader wakes, v1 is gone — it must retry, and the retry must be bounded.
        </>
      }
      insight={(runs) => <PerBackend runs={runs}>{(r) => <Retention extra={r.result.extra} />}</PerBackend>}
    />
  );
}

function Retention({ extra }: { extra?: C3Extra }) {
  const steps = extra?.retention ?? [];
  const [i, setI] = useState(0);
  const [playing, setPlaying] = useState(true);
  useEffect(() => {
    setI(0);
    setPlaying(true);
  }, [extra]);
  useEffect(() => {
    if (!playing) return;
    if (i >= steps.length - 1) {
      setPlaying(false);
      return;
    }
    const t = setTimeout(() => setI((x) => x + 1), 1300);
    return () => clearTimeout(t);
  }, [i, playing, steps.length]);
  const maxV = Math.max(6, ...steps.flatMap((s) => s.onDisk));
  const cur = steps[i];
  const ever = new Set<number>([1]);
  steps.slice(0, i + 1).forEach((s) => ever.add(s.afterSave));
  return (
    <div className="grid grid-cols-1 gap-5 2xl:grid-cols-[1.5fr_1fr]">
      <div className="rounded-2xl border border-line bg-ink-900/70 p-5">
        <div className="mb-4 flex items-center gap-3">
          <div className="text-[1.2rem] font-semibold">Versions on disk after each save</div>
          <span className="rounded-md border border-commit/50 px-2 text-[0.8rem] font-bold text-commit">KEEP_VERSIONS = 3</span>
          <button type="button" onClick={() => { if (i >= steps.length - 1) setI(0); setPlaying((p) => !p); }} className="ml-auto rounded-full p-2 text-fg-2 hover:bg-white/10" title="play / pause">
            {playing ? <Pause className="h-5 w-5" /> : <Play className="h-5 w-5" />}
          </button>
        </div>
        <div className="flex items-center gap-2">
          {Array.from({ length: maxV }, (_, k) => k + 1).map((v) => {
            const on = cur?.onDisk.includes(v);
            const gone = ever.has(v) && !on;
            return (
              <motion.div
                key={v}
                layout
                animate={{
                  opacity: on ? 1 : gone ? 0.35 : 0.12,
                  y: gone ? 18 : 0,
                  scale: on && cur?.afterSave === v ? [0.8, 1.12, 1] : 1,
                }}
                transition={{ type: "spring", stiffness: 260, damping: 20 }}
                className={cx(
                  "relative flex h-[10rem] flex-1 flex-col items-center justify-center rounded-2xl border-2",
                  on ? "border-commit/70 bg-brand/15" : gone ? "border-fail/50 border-dashed" : "border-line border-dashed",
                )}
              >
                <span className="mono text-[2.4rem] font-bold">v{v}</span>
                {cur?.afterSave === v && <span className="text-[0.7rem] font-bold uppercase tracking-wider text-commit">CURRENT</span>}
                {gone && <span className="text-[0.7rem] font-bold uppercase tracking-wider text-fail">GC'd</span>}
              </motion.div>
            );
          })}
        </div>
        <div className="mt-4 flex gap-1.5">
          {steps.map((s, k) => (
            <button key={k} type="button" onClick={() => { setPlaying(false); setI(k); }} className={cx("mono rounded-md px-2 py-0.5 text-[0.8rem]", k === i ? "bg-fg text-ink-950" : "text-fg-3 hover:text-fg")}>
              save v{s.afterSave}
            </button>
          ))}
          <span className="mono ml-auto text-[0.85rem] text-fg-2">on disk: {cur?.onDisk.map((v) => `v${v}`).join(" ")}</span>
        </div>
      </div>
      <div className="flex flex-col gap-3 rounded-2xl border border-line bg-ink-900/70 p-5">
        <div className="text-[1.2rem] font-semibold text-reader">The paused reader</div>
        {(extra?.readerAttempts ?? []).map((a, k) => (
          <motion.div
            key={k}
            initial={{ opacity: 0, x: 16 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: 0.3 + k * 0.35 }}
            className={cx("flex items-center gap-3 rounded-xl border px-4 py-2", a.outcome === "ok" ? "border-pass/50 bg-pass/10" : "border-fail/40 bg-fail/10")}
          >
            <span className="mono text-[0.85rem] text-fg-3">attempt {k + 1}</span>
            <span className="mono text-[1rem] font-bold">CURRENT → v{a.sawVersion}</span>
            <span className={cx("ml-auto text-[0.95rem] font-bold", a.outcome === "ok" ? "text-pass" : "text-fail")}>
              {a.outcome === "ok" ? "✓ read whole" : "✗ gone (ENOENT) → retry"}
            </span>
          </motion.div>
        ))}
        {extra?.starved && (
          <div className="mt-2 rounded-xl border border-warn/40 bg-warn/10 px-4 py-3">
            <div className="text-[0.95rem] font-semibold text-warn">Starved reader is bounded</div>
            <div className="mt-1 text-[0.9rem] text-fg-2">
              GC wins every race → gives up after <b className="text-fg">{extra.starved.attempts}</b> attempts:
            </div>
            <div className="mono mt-1 text-[0.78rem] text-fg-3">{extra.starved.error}</div>
          </div>
        )}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ C4
export function C4({ active }: SceneProps) {
  return (
    <ChallengeScene<C4Extra>
      active={active}
      code="KO-04-C4"
      level="HARD"
      title="Failure after commit before flush"
      text="Inject a directory-fsync error after CURRENT rename. Report an outcome-unknown error and recover without deleting the newly committed version."
      runKey="c4"
      run={(b) => runChallenge(b, "c4")}
      insightLabel="Outcome unknown"
      emptyHint={
        <>
          The CURRENT rename has happened — but the directory flush that makes it durable fails with <span className="mono text-warn">EIO</span>. Did the save
          commit? Nobody knows. The honest answer is <b className="text-warn">“outcome unknown”</b>: never delete v2, poison the handle, let recovery decide.
        </>
      }
      overlay={(run, index) => <C4Overlay run={run} index={index} />}
      insight={(runs) => <PerBackend runs={runs}>{(r) => <C4Insight extra={r.result.extra} />}</PerBackend>}
    />
  );
}

function C4Overlay({ run, index }: { run: PlayerRun<C4Extra>; index: number }) {
  const ev = run.result.events;
  const f = ev.findIndex((e) => e.kind === "fault");
  const rec = ev.findIndex((e, i) => i > f && e.lane === "recovery");
  const show = f >= 0 && index >= f && (rec < 0 || index < rec);
  return (
    <AnimatePresence>
      {show && (
        <motion.div
          initial={{ opacity: 0, scale: 1.6, rotate: -6 }}
          animate={{ opacity: 1, scale: 1, rotate: -4 }}
          exit={{ opacity: 0, scale: 0.9 }}
          transition={{ type: "spring", stiffness: 260, damping: 16 }}
          className="pointer-events-none absolute left-3 top-1 z-10 flex items-center gap-2"
        >
          <div className="flex items-center gap-2 rounded-xl border-[3px] border-warn bg-ink-950/85 px-4 py-1.5 text-[1.5rem] font-extrabold tracking-wider text-warn shadow-[0_0_40px_-6px_rgba(255,181,71,0.7)]">
            <CircleHelp className="h-7 w-7" /> OUTCOME UNKNOWN
          </div>
          {run.result.extra?.poisoned !== false && (
            <div className="flex w-fit items-center gap-2 rounded-lg border border-fail/60 bg-ink-950/85 px-3 py-1 text-[0.95rem] font-bold text-fail">
              <Skull className="h-4 w-4" /> handle poisoned
            </div>
          )}
        </motion.div>
      )}
    </AnimatePresence>
  );
}

function C4Insight({ extra }: { extra?: C4Extra }) {
  if (!extra) return null;
  const pl = extra.powerLoss;
  const max = Math.max(1, pl.states);
  return (
    <div className="grid grid-cols-1 gap-5 xl:grid-cols-3">
      <div className="flex flex-col gap-3 rounded-2xl border-2 border-warn/60 bg-ink-900/70 p-5">
        <div className="flex items-center gap-2 text-[1.5rem] font-extrabold text-warn">
          <CircleHelp className="h-7 w-7" /> OUTCOME UNKNOWN
        </div>
        <div className="mono rounded-lg bg-ink-950/60 p-3 text-[0.8rem] leading-relaxed text-fg-2">{extra.error}</div>
        <div className="flex items-center gap-2 text-[1rem]">
          <Ban className="h-5 w-5 text-fail" /> handle poisoned: <b className={extra.poisoned ? "text-fail" : "text-fg-3"}>{extra.poisoned ? "yes — next put raises Poisoned" : "no"}</b>
        </div>
        <div className="text-[0.9rem] text-fg-2">No blind fsync retry (fsyncgate): after a failed flush the page cache can no longer be trusted.</div>
      </div>
      <div className="flex flex-col gap-3 rounded-2xl border border-line bg-ink-900/70 p-5">
        <div className="text-[1.2rem] font-semibold">If the power failed right there</div>
        <div className="text-[0.95rem] text-fg-2">Simulated power loss at that instant — {pl.states} possible disk states:</div>
        {(["A", "B", "corrupt"] as const).map((k) => (
          <div key={k} className="grid grid-cols-[6rem_1fr_2.5rem] items-center gap-3">
            <span className="text-[0.95rem] font-semibold" style={{ color: k === "A" ? "var(--color-va-text)" : k === "B" ? "var(--color-vb-text)" : "var(--color-vx-text)" }}>
              {k === "corrupt" ? "✗ corrupt" : `${k} (${k === "A" ? "old" : "new"})`}
            </span>
            <div className="h-5 overflow-hidden rounded bg-ink-800">
              <motion.div
                className={cx("h-full rounded", k === "corrupt" && "hatch-x")}
                style={{ background: k === "A" ? "#3b93f5" : k === "B" ? "#cc7e14" : undefined }}
                initial={{ width: 0 }}
                animate={{ width: `${(pl[k] / max) * 100}%` }}
                transition={{ delay: 0.3, type: "spring", stiffness: 80, damping: 16 }}
              />
            </div>
            <span className="mono text-right text-[1rem] font-bold tabular">{pl[k]}</span>
          </div>
        ))}
        <div className="text-[0.9rem] text-fg-2">Both A and B are legal — that is exactly why the error says “unknown”, not “failed”.</div>
      </div>
      <div className="flex flex-col items-center justify-center gap-3 rounded-2xl border border-recover/50 bg-ink-900/70 p-5">
        <div className="text-[1.2rem] font-semibold text-recover">Reopen → recovery</div>
        <Letter l={extra.afterRecovery} size="6rem" />
        <div className="text-center text-[1rem] text-fg-2">
          {extra.afterRecovery === "B" ? "Rolled forward: CURRENT already named v2, and v2 was never deleted." : "Recovered to the old version."}
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ C5
export function C5({ active }: SceneProps) {
  const backend = useBackend();
  const { data: info } = useLoad("info", backend, getInfo);
  const points = info?.recoveryCrashPoints ?? ["recover_after_tmp_unlink", "recover_after_version_unlink", "recover_after_pointer_rewrite", "recover_before_checkpoint"];
  const [point, setPoint] = useSceneState<string>("c5:point", "recover_after_version_unlink");
  return (
    <ChallengeScene<C5Extra>
      active={active}
      code="KO-04-C5"
      level="HARD"
      title="Interrupted recovery"
      text="Interrupt recovery at a chosen state-changing operation and rerun recovery twice. Preserve a whole accepted version and consistent cleanup."
      runKey="c5"
      run={(b) => runChallenge(b, "c5", { crashPoint: point })}
      controls={
        <Segmented
          layoutId="c5-point"
          size="sm"
          value={point}
          onChange={setPoint}
          options={points.map((p) => ({ value: p, label: prettyPoint(p), title: p }))}
        />
      }
      insightLabel="Idempotence"
      emptyHint={
        <>
          Setup: v1 = A committed, an uncommitted v2 = B and leftover temp files. Recovery starts cleaning up — and is killed at{" "}
          <span className="mono text-fg">{prettyPoint(point)}</span>. Then recovery runs again, and again. Both reruns must leave the exact same disk.
        </>
      }
      insight={(runs) => <PerBackend runs={runs}>{(r) => <C5Insight extra={r.result.extra} />}</PerBackend>}
    />
  );
}

function C5Insight({ extra }: { extra?: C5Extra }) {
  if (!extra) return null;
  const [a, b] = extra.fingerprints;
  return (
    <div className="flex flex-col gap-4 rounded-2xl border border-line bg-ink-900/70 p-6">
      <div className="flex items-center gap-3 text-[1.3rem] font-semibold">
        <Fingerprint className="h-7 w-7 text-recover" /> On-disk fingerprint after each rerun
      </div>
      {[a, b].map((fp, k) => (
        <div key={k} className="grid grid-cols-[7rem_1fr] items-center gap-4">
          <span className="text-[1rem] font-semibold text-fg-2">rerun {k + 1}</span>
          <div className="mono flex flex-wrap rounded-xl bg-ink-950/60 px-5 py-3 text-[1.9rem] font-semibold leading-relaxed tracking-wider">
            {fp.split("").map((ch, i) => (
              <motion.span
                key={i}
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: 0.2 + k * 0.5 + i * 0.008 }}
                style={{ color: k === 1 ? (ch === a[i] ? "var(--color-pass)" : "var(--color-fail)") : "var(--color-fg)" }}
              >
                {ch}
              </motion.span>
            ))}
          </div>
        </div>
      ))}
      <motion.div
        initial={{ opacity: 0, scale: 0.9 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ delay: 1.4, type: "spring", stiffness: 200, damping: 16 }}
        className={cx("flex items-center gap-3 text-[1.6rem] font-bold", extra.identical ? "text-pass" : "text-fail")}
      >
        <AnimatedTick pass={extra.identical} size="2.2rem" delay={1.5} />
        {extra.identical ? "Identical — recovery is idempotent" : "Fingerprints differ"}
      </motion.div>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        {extra.reports.slice(0, 2).map((r, k) => (
          <div key={k} className="rounded-xl border border-line bg-ink-950/40 p-3">
            <div className="text-[0.85rem] font-semibold text-fg-2">rerun {k + 1} report</div>
            <div className="mono mt-1 text-[0.78rem] leading-relaxed text-fg-3">
              {Object.entries(r)
                .filter(([key]) => ["rolled_back", "rolled_forward", "tmp_removed", "journal", "fell_back", "degraded"].includes(key))
                .map(([key, v]) => (
                  <div key={key}>
                    {key}: <span className="text-fg-2">{JSON.stringify(v)}</span>
                  </div>
                ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
