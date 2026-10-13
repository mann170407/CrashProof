import { AnimatePresence, motion } from "motion/react";
import { HardDrive, Check, ShieldCheck, ArrowRightLeft, Power, X, Zap } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { cx } from "../components/ui";
import { useStore, useSceneState } from "../lib/store";
import { COMMIT_STEP, DRAFT_KEY, SAVE_STEPS, armAt, armStore, diskStore, dismissReport, pad, powerOff, powerStore, save, type DiskState, type Recovery } from "../lib/editorDisk";
import type { SceneProps } from "./types";

// Look: Midday-style (neutral black, 1px hairlines, square corners, serif headings, monochrome).

const SEED = "Team Crashproof — demo notes\n\nType anything here. Every pause in typing triggers an autosave through the AtomicDocs write path.\n";
const DEBOUNCE = 450; // ms of quiet before an autosave starts

export default function LiveEditor({ active }: SceneProps) {
  const disk = useStore(diskStore);
  const { report } = useStore(powerStore);
  // the draft lives in RAM (scene state): it survives switching scenes, not a power cut
  const [draft, setDraft] = useSceneState<string | null>(DRAFT_KEY, null);
  const text = draft ?? disk.committed?.text ?? SEED;
  const ta = useRef<HTMLTextAreaElement>(null);
  const cursor = useRef<[number, number]>(disk.committed?.cursor ?? [text.length, text.length]);
  const deb = useRef(0);
  const [dirty, setDirty] = useState(false);

  // put the caret back where it was (also after a restart)
  useEffect(() => {
    const el = ta.current;
    if (!el || !active) return;
    el.focus({ preventScroll: true });
    el.setSelectionRange(cursor.current[0], cursor.current[1]);
  }, [active]);

  useEffect(() => () => clearTimeout(deb.current), []);

  const schedule = (t: string) => {
    setDirty(true);
    clearTimeout(deb.current);
    deb.current = window.setTimeout(() => {
      setDirty(false);
      save({ text: t, cursor: cursor.current });
    }, DEBOUNCE);
  };
  const track = () => {
    const el = ta.current;
    if (el) cursor.current = [el.selectionStart, el.selectionEnd];
  };

  const caretLine = text.slice(0, cursor.current[0]).split("\n").length;
  const caretCol = cursor.current[0] - text.lastIndexOf("\n", cursor.current[0] - 1);

  return (
    <>
      <div className="pointer-events-none absolute inset-0 bg-md-bg" />
      <div className="relative flex h-full min-h-0 flex-col gap-7 font-hedvig text-md-fg">
        <header className="flex items-end justify-between gap-6 pt-3">
          <div>
            <motion.h1
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
              className="font-serif text-[3.3rem] leading-[1.05] tracking-[-0.01em]"
            >
              Type. Pull the plug. <span className="text-md-3">Get it all back.</span>
            </motion.h1>
            <motion.p
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ duration: 0.6, delay: 0.1 }}
              className="mt-3 max-w-[60rem] text-[1.05rem] text-md-2"
            >
              An editor that autosaves through the AtomicDocs write path as you type. Power Off is simulated: it closes this app, not the computer.
            </motion.p>
          </div>
          <div className="flex shrink-0 flex-col items-end gap-2">
            <button
              type="button"
              onClick={powerOff}
              className="flex h-12 items-center gap-2.5 bg-md-fg px-6 text-[1rem] text-md-bg transition-colors hover:bg-white"
              title="Simulated: closes this app's UI, not your computer"
            >
              <Power className="h-4 w-4" strokeWidth={2} /> Power Off
            </button>
            <span className="text-[0.75rem] text-md-3">Simulated · your computer stays on</span>
          </div>
        </header>

        <div className="grid min-h-0 flex-1 grid-cols-1 border border-md-line xl:grid-cols-[1.6fr_1fr]">
          {/* ---------- editor ---------- */}
          <div className="flex min-h-0 flex-col border-md-line xl:border-r">
            <div className="flex h-12 shrink-0 items-center gap-3 border-b border-md-line px-6 text-[0.9rem]">
              <span className="text-md-fg">notes.txt</span>
              <span className="text-md-3">· Documents</span>
              <span className="ml-auto">
                <SaveStatus disk={disk} dirty={dirty} />
              </span>
            </div>
            <AnimatePresence>{report && <RecoveryBanner report={report} />}</AnimatePresence>
            <textarea
              ref={ta}
              value={text}
              spellCheck={false}
              onChange={(e) => {
                if (report) dismissReport();
                setDraft(e.target.value);
                track();
                schedule(e.target.value);
              }}
              onSelect={track}
              onKeyUp={track}
              onClick={track}
              className="no-ring min-h-0 flex-1 resize-none bg-transparent px-8 py-7 font-serif text-[1.45rem] leading-[1.65] text-md-fg caret-md-fg outline-none selection:bg-white/20"
            />
            <div className="flex h-10 shrink-0 items-center gap-6 border-t border-md-line px-6 text-[0.8rem] text-md-3 tabular">
              <span>
                Ln {caretLine}, Col {caretCol}
              </span>
              <span>{text.length} characters</span>
              <span className="ml-auto">Autosave after {DEBOUNCE} ms of quiet</span>
            </div>
          </div>

          {/* ---------- disk ---------- */}
          <div className="flex min-h-0 flex-col overflow-hidden">
            <Pipeline disk={disk} />
            <Files disk={disk} />
            <LastSafe disk={disk} />
          </div>
        </div>
      </div>
    </>
  );
}

function SectionHead({ icon, title, right }: { icon: React.ReactNode; title: string; right?: React.ReactNode }) {
  return (
    <div className="flex h-12 shrink-0 items-center gap-2.5 border-b border-md-line px-5 text-[0.9rem]">
      <span className="text-md-3">{icon}</span>
      <span>{title}</span>
      {right && <span className="ml-auto text-[0.8rem] text-md-3">{right}</span>}
    </div>
  );
}

function SaveStatus({ disk, dirty }: { disk: DiskState; dirty: boolean }) {
  const f = disk.inflight;
  if (f)
    return (
      <span className="flex items-center gap-2 text-[0.85rem] text-md-2">
        <motion.span className="h-1.5 w-1.5 bg-md-fg" animate={{ opacity: [1, 0.2, 1] }} transition={{ duration: 0.8, repeat: Infinity }} />
        Saving v{f.v} · step {f.step + 1} of {SAVE_STEPS.length}
      </span>
    );
  if (dirty)
    return (
      <span className="flex items-center gap-2 text-[0.85rem] text-md-2">
        <span className="h-1.5 w-1.5 border border-md-2" /> Unsaved changes
      </span>
    );
  if (disk.committed)
    return (
      <span className="flex items-center gap-2 text-[0.85rem] text-md-2">
        <Check className="h-3.5 w-3.5 text-pass" /> Saved · v{disk.committed.v}
      </span>
    );
  return <span className="text-[0.85rem] text-md-3">Not saved yet</span>;
}

function Pipeline({ disk }: { disk: DiskState }) {
  const f = disk.inflight;
  const armed = useStore(armStore);
  return (
    <div className="shrink-0 border-b border-md-line">
      <SectionHead icon={<ArrowRightLeft className="h-4 w-4" />} title="Save path" right={f ? `v${f.v} in flight` : "Idle"} />
      <div>
        {SAVE_STEPS.map((s, i) => {
          const state = !f ? "idle" : i < f.step ? "done" : i === f.step ? "run" : "todo";
          const commit = i === COMMIT_STEP;
          return (
            <div key={s.id} className="relative flex h-10 items-center gap-3 border-b border-md-line px-5 last:border-b-0">
              {state === "run" && (
                <motion.div
                  key={`${f?.v}-${i}`}
                  className="absolute inset-y-0 left-0 bg-white/[0.06]"
                  initial={{ width: "0%" }}
                  animate={{ width: "100%" }}
                  transition={{ duration: s.ms / 1000, ease: "linear" }}
                />
              )}
              {state === "run" && <span className="absolute inset-y-0 left-0 w-[2px] bg-md-fg" />}
              <span className="relative w-5 text-[0.8rem] text-md-3 tabular">{String(i + 1).padStart(2, "0")}</span>
              <span className={cx("mono relative text-[0.85rem]", state === "run" || state === "done" ? "text-md-fg" : "text-md-3")}>
                {s.label(f?.v ?? (disk.committed?.v ?? 0) + 1)}
              </span>
              {commit && <span className="relative border border-md-line-2 px-1.5 py-px text-[0.68rem] uppercase tracking-[0.12em] text-md-2">commit point</span>}
              {armed === i && (
                <span className="relative flex items-center gap-1 bg-fail/15 px-1.5 py-px text-[0.7rem] text-fail">
                  <Zap className="h-3 w-3" /> power cuts here
                </span>
              )}
              <span className="relative ml-auto">
                {state === "done" ? <Check className="h-4 w-4 text-md-fg" /> : state === "run" ? <span className="text-[0.75rem] text-md-2">running</span> : null}
              </span>
            </div>
          );
        })}
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-md-line px-5 py-3">
        <span className="text-[0.8rem] text-md-3">Cut power on the next save at</span>
        <div className="flex border border-md-line" role="radiogroup" aria-label="Cut power at step">
          {[null, ...SAVE_STEPS.map((_, i) => i)].map((v) => (
            <button
              key={String(v)}
              type="button"
              role="radio"
              aria-checked={armed === v}
              onClick={() => armAt(v)}
              title={v == null ? "Only when you press Power Off" : `Halfway through step ${v + 1}: ${SAVE_STEPS[v].label(0).replace(/v0+/, "vN")}`}
              className={cx(
                "h-8 border-r border-md-line px-3 text-[0.8rem] transition-colors last:border-r-0",
                armed === v ? "bg-md-fg text-md-bg" : "text-md-2 hover:bg-white/[0.06]",
              )}
            >
              {v == null ? "Manual" : String(v + 1).padStart(2, "0")}
            </button>
          ))}
        </div>
        {armed != null && (
          <span className="text-[0.8rem] text-md-2">
            → after restart: {armed > COMMIT_STEP ? "the new text (commit already happened)" : "the previous save (before the commit point)"}
          </span>
        )}
      </div>
    </div>
  );
}

function Files({ disk }: { disk: DiskState }) {
  const f = disk.inflight;
  const cur = disk.committed?.v;
  const tmp = f && f.step <= 2 && !disk.versions.includes(f.v);
  return (
    <div className="shrink-0 border-b border-md-line">
      <SectionHead icon={<HardDrive className="h-4 w-4" />} title="On disk" right={`${disk.versions.length + (tmp ? 1 : 0)} ${disk.versions.length + (tmp ? 1 : 0) === 1 ? "file" : "files"}`} />
      <div className="min-h-[5rem]">
        <AnimatePresence initial={false}>
          {disk.versions.map((v) => (
            <motion.div
              key={v}
              layout
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0, height: 0 }}
              className="mono flex h-10 items-center gap-3 border-b border-md-line px-5 text-[0.85rem]"
            >
              <span>v{pad(v)}.dat</span>
              {v === cur && (
                <motion.span layoutId="editor-current" className="ml-auto bg-md-fg px-2 py-0.5 font-hedvig text-[0.72rem] text-md-bg">
                  CURRENT
                </motion.span>
              )}
            </motion.div>
          ))}
          {tmp && f && (
            <motion.div
              key={`tmp-${f.v}`}
              layout
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="mono flex h-10 items-center gap-3 border-b border-dashed border-md-line-2 px-5 text-[0.85rem] text-md-3"
            >
              <span>.v{pad(f.v)}.dat.tmp</span>
              <span className="ml-auto font-hedvig text-[0.75rem]">{f.step === 0 ? "Page cache · volatile" : "Fsynced"}</span>
            </motion.div>
          )}
        </AnimatePresence>
        {!disk.versions.length && !tmp && <div className="px-5 py-3 text-[0.85rem] text-md-3">Empty. Nothing saved yet.</div>}
      </div>
    </div>
  );
}

function LastSafe({ disk }: { disk: DiskState }) {
  const c = disk.committed;
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <SectionHead icon={<ShieldCheck className="h-4 w-4" />} title="Survives a power cut" right={c ? `v${c.v} · ${new Date(c.at).toLocaleTimeString()}` : undefined} />
      {c ? (
        <div className="flex min-h-0 flex-1 flex-col px-5 py-3">
          <div className="mb-2 text-[0.8rem] text-md-3 tabular">
            {c.text.length} characters · cursor at {c.cursor[0]}
          </div>
          {/* anchored to the bottom: the words just before the saved cursor stay visible however small the panel */}
          <div className="flex min-h-0 flex-1 flex-col justify-end overflow-hidden [mask-image:linear-gradient(to_top,black_70%,transparent)]">
            <div className="whitespace-pre-wrap break-words font-serif text-[1rem] leading-relaxed text-md-2">
              {c.text.slice(Math.max(0, c.cursor[0] - 400), c.cursor[0])}
              <span className="mx-px inline-block h-[1.05em] w-[2px] translate-y-[3px] bg-md-fg" />
              {c.text.slice(c.cursor[0], c.cursor[0] + 40)}
            </div>
          </div>
        </div>
      ) : (
        <div className="px-5 py-4 text-[0.9rem] text-md-3">Nothing yet. Start typing and the first autosave commits v1.</div>
      )}
    </div>
  );
}

function RecoveryBanner({ report }: { report: Recovery }) {
  const r = report.restored;
  const what =
    report.action === "rolled-forward"
      ? `The power went out after the commit point of v${report.crashedV}, so recovery finished that save.`
      : report.action === "rolled-back"
        ? `The power went out during “${SAVE_STEPS[report.crashedStep ?? 0].label(report.crashedV ?? 0)}” of v${report.crashedV}: before the commit point, so recovery discarded it.`
        : "No save was in flight when the power went out.";
  return (
    <motion.div
      initial={{ height: 0, opacity: 0 }}
      animate={{ height: "auto", opacity: 1 }}
      exit={{ height: 0, opacity: 0 }}
      className="shrink-0 overflow-hidden border-b border-md-line bg-white/[0.03]"
    >
      <div className="flex items-start gap-4 px-6 py-3.5">
        <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-pass" />
        <div className="min-w-0 flex-1">
          <div className="text-[1rem] text-md-fg">
            {r ? (
              <>
                Restored v{r.v} · cursor back at {r.cursor[0]} · {r.text.length} characters
              </>
            ) : (
              "Nothing had been saved yet: the document is empty."
            )}
          </div>
          <div className="mt-0.5 text-[0.85rem] text-md-2">
            {what}
            {report.lostChars > 0 && ` The last ${report.lostChars} characters you typed were only in memory and are gone.`}
          </div>
        </div>
        <button type="button" onClick={dismissReport} className="p-1 text-md-3 hover:text-md-fg" title="Dismiss">
          <X className="h-4 w-4" />
        </button>
      </div>
    </motion.div>
  );
}
