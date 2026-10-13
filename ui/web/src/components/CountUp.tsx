import { useEffect, useState } from "react";
import { useReducedMotion } from "motion/react";

/** Counts from 0 to `value` with an ease-out-expo curve. */
export function CountUp({ value, duration = 1.8, delay = 0, format = (n: number) => Math.round(n).toLocaleString("en-US") }: { value: number; duration?: number; delay?: number; format?: (n: number) => string }) {
  const reduce = useReducedMotion();
  const [n, setN] = useState(reduce ? value : 0);
  useEffect(() => {
    if (reduce) {
      setN(value);
      return;
    }
    let raf = 0;
    const t0 = performance.now() + delay * 1000;
    const tick = (now: number) => {
      const k = Math.min(1, Math.max(0, (now - t0) / (duration * 1000)));
      const e = k === 1 ? 1 : 1 - Math.pow(2, -10 * k);
      setN(value * e);
      if (k < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [value, duration, delay, reduce]);
  return <span className="tabular">{format(n)}</span>;
}
