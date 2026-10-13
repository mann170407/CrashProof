import { motion } from "motion/react";
import { Atom, HardDrive, Lock, LifeBuoy, Layers } from "lucide-react";
import { SceneTitle } from "../components/ui";

const CONCEPTS = [
  { icon: Atom, t: "Atomicity", c: "#b9a8ff", d: ["rename(2) / MoveFileExW is the single commit point", "old version or new version, never a mixture"] },
  { icon: HardDrive, t: "File systems", c: "#3b93f5", d: ["page cache vs stable storage: fsync the file, then the directory", "directory entries are data too"] },
  { icon: Lock, t: "Synchronization", c: "#f472b6", d: ["flock / LockFileEx: writers share, recovery excludes", "compare-and-swap (expect=v): no lost updates"] },
  { icon: LifeBuoy, t: "Recovery", c: "#2dd4bf", d: ["trust only bytes that verify (SHA-256, CRC)", "idempotent: interrupted recovery reruns to the same disk"] },
  { icon: Layers, t: "Copy-on-write + journaling", c: "#cc7e14", d: ["immutable version files, GC keeps 3", "write-ahead intent journal for cleanup and reporting"] },
];

const LIMITS = [
  "Single-document transactions (no multi-document atomicity).",
  "Local file systems only, not NFS / SMB shares.",
  "Assumes the drive honours flush commands; power loss is simulated (SimFS, ALICE-style), not a real cord pull.",
  "Durability costs latency: a save is several fsyncs (about 26 ms on Windows NTFS vs 1.6 ms for an unsafe overwrite).",
];

export default function Closing() {
  const R = 15.5; // rem radius of the concept ring
  return (
    <div className="flex h-full min-h-0 flex-col gap-4">
      <SceneTitle eyebrow="11 · What it takes" title="Five OS ideas, one guarantee." />
      <div className="grid min-h-0 flex-1 grid-cols-1 gap-6 xl:grid-cols-[1.35fr_1fr]">
        <div className="relative flex min-h-[30rem] items-center justify-center">
          {CONCEPTS.map((c, i) => {
            const a = (i / CONCEPTS.length) * Math.PI * 2 - Math.PI / 2;
            const dx = Math.cos(a) * R;
            const dy = Math.sin(a) * R * 0.78;
            const len = Math.hypot(dx, dy);
            const deg = (Math.atan2(dy, dx) * 180) / Math.PI;
            return (
              <motion.div
                key={c.t}
                aria-hidden
                className="absolute left-1/2 top-1/2 h-[2px] origin-left"
                style={{ width: `${len.toFixed(2)}rem`, rotate: `${deg.toFixed(1)}deg`, background: `repeating-linear-gradient(90deg, ${c.c} 0 6px, transparent 6px 12px)`, opacity: 0.5 }}
                initial={{ scaleX: 0 }}
                animate={{ scaleX: 1 }}
                transition={{ delay: 0.3 + i * 0.12, duration: 0.8, ease: [0.16, 1, 0.3, 1] }}
              />
            );
          })}
          <motion.div
            initial={{ scale: 0.6, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={{ type: "spring", stiffness: 160, damping: 16 }}
            className="glow-commit relative z-10 flex h-[9rem] w-[9rem] flex-col items-center justify-center rounded-full border-2 border-commit bg-ink-900 text-center"
          >
            <div className="text-[1.3rem] font-bold leading-tight">AtomicDocs</div>
            <div className="text-[0.75rem] text-fg-2">
              old or new.
              <br />
              never torn.
            </div>
          </motion.div>
          {CONCEPTS.map((c, i) => {
            const a = (i / CONCEPTS.length) * Math.PI * 2 - Math.PI / 2;
            return (
              <motion.div
                key={c.t}
                initial={{ opacity: 0, scale: 0.8 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={{ delay: 0.5 + i * 0.12, type: "spring", stiffness: 180, damping: 18 }}
                className="absolute z-10 w-[17rem] rounded-2xl border bg-ink-900/90 p-3.5 backdrop-blur"
                style={{
                  left: `calc(50% + ${(Math.cos(a) * R).toFixed(2)}rem - 8.5rem)`,
                  top: `calc(50% + ${(Math.sin(a) * R * 0.78).toFixed(2)}rem - 3rem)`,
                  borderColor: `color-mix(in srgb, ${c.c} 50%, transparent)`,
                }}
              >
                <div className="flex items-center gap-2 text-[1.1rem] font-semibold" style={{ color: c.c }}>
                  <c.icon className="h-5 w-5" /> {c.t}
                </div>
                <ul className="mt-1 text-[0.88rem] leading-snug text-fg-2">
                  {c.d.map((x) => (
                    <li key={x}>· {x}</li>
                  ))}
                </ul>
              </motion.div>
            );
          })}
        </div>
        <div className="flex min-h-0 flex-col gap-5">
          <motion.div
            initial={{ opacity: 0, x: 20 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: 0.6, duration: 0.6 }}
            className="rounded-2xl border border-line bg-ink-900/70 p-6"
          >
            <div className="text-[1.3rem] font-semibold text-warn">Limitations</div>
            <ul className="mt-3 flex flex-col gap-2.5 text-[1rem] leading-snug text-fg-2">
              {LIMITS.map((l) => (
                <li key={l} className="flex gap-2">
                  <span className="text-fg-3">-</span> {l}
                </li>
              ))}
            </ul>
          </motion.div>
          <motion.div initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.9, duration: 0.7 }} className="mt-auto">
            <div className="font-serif text-[4.2rem] font-normal leading-none tracking-[-0.01em]">
              Thank you<span className="text-fg-3">.</span>
            </div>
            <div className="mt-3 text-[1.15rem] text-fg-2">
              Team Crashproof: <span className="text-fg">Mann Sharma · Adarsh Pillai · Purumalla Shreeyans</span>
            </div>
            <div className="mono mt-1 text-[0.85rem] text-fg-3">Kernel Ops · KO-04 · OSH021 · questions?</div>
          </motion.div>
        </div>
      </div>
    </div>
  );
}
