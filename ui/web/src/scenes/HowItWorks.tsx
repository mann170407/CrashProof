import { motion } from "motion/react";
import { ArrowRightLeft, FolderSync, PenLine, Save, ScrollText, Trash2, Zap, FilePlus2, CircleCheck, Plug } from "lucide-react";
import { getInfo, runPlug, statusStore } from "../api";
import { CompareToggle, LoadingVeil, useRunner } from "../components/SceneKit";
import { TimelinePlayer } from "../components/TimelinePlayer";
import { Button, OsMark, SceneTitle, SourceBadge, cx } from "../components/ui";
import { useBackend, useBackendStatus, useCompare } from "../lib/backend";
import { useHotkeys } from "../lib/keys";
import { useSceneState } from "../lib/store";
import { useLoad } from "../lib/useLoad";
import { prettyPoint } from "../lib/format";
import { BACKEND_ORDER } from "../config";
import { NATIVE } from "../mocks/sim";
import type { BackendId, PlugExtra, SysOp } from "../types";
import type { SceneProps } from "./types";

interface Step {
  n: number;
  title: string;
  op: SysOp;
  crash: string;
  phase: "prepare" | "commit" | "cleanup";
  why: string;
  commit?: boolean;
  icon: typeof Save;
}

const STEPS: Step[] = [
  { n: 1, title: "BEGIN v1→v2 → journal.wal", op: "write", crash: "after_begin", phase: "prepare", why: "write-ahead intent", icon: ScrollText },
  { n: 2, title: "write .v000002.dat.tmp", op: "write", crash: "torn_data", phase: "prepare", why: "copy-on-write: A untouched", icon: FilePlus2 },
  { n: 3, title: "fsync(temp)", op: "fsync", crash: "before_data_fsync", phase: "prepare", why: "bytes durable", icon: Save },
  { n: 4, title: "rename temp → v000002.dat", op: "rename", crash: "after_data_rename", phase: "prepare", why: "name the version", icon: ArrowRightLeft },
  { n: 5, title: "fsync(dir)", op: "fsync_dir", crash: "after_data_rename", phase: "prepare", why: "name durable", icon: FolderSync },
  { n: 6, title: "write .CURRENT.tmp → v2", op: "write", crash: "torn_pointer", phase: "commit", why: "new pointer, SHA inside", icon: PenLine },
  { n: 7, title: "fsync(CURRENT temp)", op: "fsync", crash: "before_pointer_rename", phase: "commit", why: "pointer durable", icon: Save },
  { n: 8, title: "rename .CURRENT.tmp → CURRENT", op: "rename", crash: "after_pointer_rename", phase: "commit", why: "THE COMMIT POINT", commit: true, icon: ArrowRightLeft },
  { n: 9, title: "fsync(dir)", op: "fsync_dir", crash: "after_pointer_rename", phase: "commit", why: "commit durable → ack", icon: FolderSync },
  { n: 10, title: "COMMIT → journal.wal", op: "write", crash: "after_commit", phase: "cleanup", why: "completion record", icon: CircleCheck },
  { n: 11, title: "GC: keep newest 3", op: "unlink", crash: "after_commit", phase: "cleanup", why: "housekeeping", icon: Trash2 },
];

export default function HowItWorks({ active }: SceneProps) {
  const backend = useBackend();
  const [compare] = useCompare();
  const status = useBackendStatus();
  const { data: info } = useLoad("info", backend, getInfo);
  const [chosen, setChosen] = useSceneState<number | null>("how:step", null);
  const runner = useRunner<PlugExtra>("how", (b) => runPlug(b, null, "atomic"));
  const crashPoints = info?.crashPoints ?? STEPS.map((s) => s.crash);

  const pull = (s: Step | null) => {
    setChosen(s ? s.n : 0);
    const crash = s ? s.crash : null;
    void runner.go((b: BackendId) => runPlug(b, crash, "atomic"));
  };
  useHotkeys(active, {
    r: () => pull(STEPS.find((s) => s.n === chosen) ?? null),
  });

  const nativeFor = (b: BackendId, op: string) => status[b].info?.platform.native?.[op] ?? statusStore.get()[b].info?.platform.native?.[op] ?? NATIVE[b][op];
  const has = runner.runs.length > 0;

  return (
    <div className="flex h-full min-h-0 flex-col gap-4">
      <SceneTitle
        eyebrow="02 · How it works"
        title="Eleven system calls. One of them commits."
        sub="Click any step to pull the plug there: the real store runs, dies at that point, and reboots through recovery."
        right={<CompareToggle />}
      />
      <div className="grid min-h-0 flex-1 grid-cols-1 gap-5 xl:grid-cols-[30rem_minmax(0,1fr)]">
        {/* ---- pipeline ---- */}
        <div className="scroll-quiet flex min-h-0 flex-col gap-[0.3rem] overflow-y-auto pr-2 pt-2">
          {STEPS.map((s, i) => {
            const sel = chosen === s.n;
            const ok = crashPoints.includes(s.crash);
            return (
              <div key={s.n}>
                {s.commit && (
                  <div className="my-1 flex items-center gap-2 text-[0.72rem] font-bold uppercase tracking-[0.18em]">
                    <span className="text-va-text">↑ crash here → A</span>
                    <span className="h-px flex-1 bg-gradient-to-r from-va/60 via-commit to-vb/60" />
                    <span className="text-vb-text">B ← crash here ↓</span>
                  </div>
                )}
                <motion.button
                  type="button"
                  disabled={!ok}
                  onClick={() => pull(s)}
                  initial={{ opacity: 0, x: -20 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ delay: 0.15 + i * 0.045, duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
                  whileHover={{ x: 4 }}
                  className={cx(
                    "group relative flex w-full items-center gap-3 rounded-xl border px-3 py-1 text-left transition-colors",
                    s.commit ? "glow-commit border-commit bg-brand/20" : sel ? "border-fail bg-fail/10" : "border-line bg-ink-900/70 hover:border-line-strong",
                  )}
                  title={`Pull the plug at ${s.crash}`}
                >
                  <span className={cx("mono w-6 text-right text-[0.8rem] font-bold", s.commit ? "text-commit" : "text-fg-3")}>{s.n}</span>
                  <s.icon className={cx("h-5 w-5 shrink-0", s.commit ? "text-commit" : s.phase === "cleanup" ? "text-pass" : "text-brand")} />
                  <span className="min-w-0 flex-1">
                    <span className={cx("mono block truncate text-[0.88rem] font-semibold", s.commit && "text-white")}>{s.title}</span>
                    <span className="block truncate text-[0.7rem] text-fg-3">
                      {compare ? (
                        BACKEND_ORDER.map((b) => (
                          <span key={b} className="mono mr-3 inline-flex items-center gap-1">
                            <OsMark id={b} className="h-3 w-3" /> {nativeFor(b, s.op)}
                          </span>
                        ))
                      ) : (
                        <span className="mono">{nativeFor(backend, s.op)}</span>
                      )}
                    </span>
                  </span>
                  <span className={cx("shrink-0 text-right text-[0.68rem]", s.commit ? "font-extrabold uppercase tracking-wider text-commit" : "text-fg-3")}>{s.why}</span>
                  <span
                    className={cx(
                      "absolute -right-2 -top-2 flex h-6 w-6 items-center justify-center rounded-full bg-fail text-white shadow-lg transition-opacity",
                      sel ? "opacity-100" : "opacity-0 group-hover:opacity-100",
                    )}
                  >
                    <Zap className="h-3.5 w-3.5" />
                  </span>
                </motion.button>
              </div>
            );
          })}
          <div className="mt-2 flex items-center gap-3">
            <Button size="sm" variant="ghost" onClick={() => pull(null)} icon={<Plug className="h-4 w-4" />}>
              No crash (full save)
            </Button>
            <span className="text-[0.75rem] text-fg-3">each step pulls the plug at the store crash point shown on hover</span>
          </div>
        </div>

        {/* ---- live replay ---- */}
        <div className="relative flex min-h-0 flex-col gap-2">
          {has ? (
            <>
              <div className="flex items-center gap-3 text-[1rem]">
                <Zap className="h-5 w-5 text-fail" />
                <span className="text-fg-2">Plug pulled at</span>
                <span className="mono font-bold text-fg">{prettyPoint(String(runner.runs[0].result.params?.crashAt ?? "") || null)}</span>
                <span className="text-fg-3">→ expected</span>
                <span className="font-bold" style={{ color: (chosen ?? 0) >= 8 || chosen === 0 ? "var(--color-vb-text)" : "var(--color-va-text)" }}>
                  {(chosen ?? 0) >= 8 || chosen === 0 ? "B (roll forward)" : "A (roll back)"}
                </span>
                <div className="ml-auto flex gap-2">
                  {runner.runs.map((r) => (
                    <SourceBadge key={r.backend} source={r.source} backend={r.backend} error={r.error} />
                  ))}
                </div>
              </div>
              <div className="min-h-0 flex-1">
                <TimelinePlayer runs={runner.runs} resetKey={runner.state.runId} active={active} initialSpeed={2} />
              </div>
            </>
          ) : (
            <Explainer />
          )}
          <LoadingVeil loading={runner.state.loading} what="Pulling the plug on the real store…" />
        </div>
      </div>
    </div>
  );
}

function Explainer() {
  const cards = [
    { c: "var(--color-brand)", t: "Prepare", d: "Journal the intent, write the whole new version to a temp file beside the old one, fsync it, rename it into place, fsync the directory. The old version is never touched." },
    { c: "var(--color-commit)", t: "Commit", d: "Write a new CURRENT pointer (with the version's SHA-256) to a temp file, fsync, then rename(2) it over CURRENT. rename is atomic: a reader or a reboot sees the old pointer or the new one, never half." },
    { c: "var(--color-pass)", t: "Durable, then tidy", d: "fsync the directory so the rename survives power loss — only then is the save acknowledged. Then the COMMIT record and GC (keep 3 versions)." },
    { c: "var(--color-recover)", t: "Recovery on open", d: "Trust only bytes that verify: CURRENT if it parses and its target's SHA-256 matches, else the newest valid version. Delete temp files and uncommitted versions. Idempotent." },
  ];
  return (
    <div className="grid h-full grid-cols-1 content-center gap-4 md:grid-cols-2">
      {cards.map((c, i) => (
        <motion.div
          key={c.t}
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3 + i * 0.1, duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
          className="rounded-2xl border border-line bg-ink-900/70 p-6"
          style={{ boxShadow: `inset 0 3px 0 ${c.c}` }}
        >
          <div className="text-[1.4rem] font-semibold" style={{ color: c.c }}>
            {c.t}
          </div>
          <div className="mt-2 text-[1.02rem] leading-relaxed text-fg-2">{c.d}</div>
        </motion.div>
      ))}
      <div className="col-span-full text-center text-[1.1rem] text-fg-2">
        ← click a step to cut the power there
      </div>
    </div>
  );
}
