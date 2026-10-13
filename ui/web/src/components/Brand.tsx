import { cx } from "./ui";

/** Crash Proof mark: a shield (proof) around a lightning bolt (the power cut). Monochrome, uses currentColor. */
export function CrashProofMark({ className = "h-6 w-6" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" aria-hidden>
      <path d="M12 2.5 4 5.5v6.2c0 4.6 3.4 8.4 8 9.8 4.6-1.4 8-5.2 8-9.8V5.5L12 2.5Z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
      <path d="M13.1 6.3 8.6 13h3.5l-1.2 4.7 4.5-6.8h-3.5l1.2-4.6Z" fill="currentColor" />
    </svg>
  );
}

/** Mark + "Crash Proof" wordmark. */
export function CrashProofLogo({ size = "md", className }: { size?: "md" | "lg"; className?: string }) {
  const lg = size === "lg";
  return (
    <span className={cx("inline-flex items-center text-md-fg", lg ? "gap-3.5" : "gap-2.5", className)}>
      <CrashProofMark className={lg ? "h-11 w-11" : "h-6 w-6"} />
      <span className={cx("font-serif tracking-[-0.01em]", lg ? "text-[2.2rem]" : "text-[1.15rem]")}>Crash Proof</span>
    </span>
  );
}
