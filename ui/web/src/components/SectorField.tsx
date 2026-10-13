import { useEffect, useRef } from "react";

/**
 * Hero background: a field of disk sectors being written. Cells light up blue (A) / amber (B),
 * fade back; now and then one tears (rose). Canvas, ~1 draw per frame, paused when hidden or
 * when the viewer prefers reduced motion.
 */
export function SectorField({ className }: { className?: string }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const cv = ref.current!;
    const ctx = cv.getContext("2d");
    if (!ctx) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let w = 0, h = 0, cell = 0, cols = 0, rows = 0, raf = 0;
    type Lit = { i: number; t0: number; c: string; life: number };
    let lit: Lit[] = [];
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const resize = () => {
      w = cv.clientWidth; h = cv.clientHeight;
      cv.width = w * dpr; cv.height = h * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      cell = Math.max(18, Math.round(Math.min(w / 64, h / 36)));
      cols = Math.ceil(w / cell); rows = Math.ceil(h / cell);
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(cv);
    const colors = ["59,147,245", "59,147,245", "204,126,20", "204,126,20", "139,123,255", "240,67,106"];
    // a "write head" sweeping rows like a sequential write
    let head = 0;
    const draw = (now: number) => {
      ctx.clearRect(0, 0, w, h);
      ctx.strokeStyle = "rgba(255,255,255,0.035)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      for (let x = 0; x <= cols; x++) { ctx.moveTo(x * cell + 0.5, 0); ctx.lineTo(x * cell + 0.5, h); }
      for (let y = 0; y <= rows; y++) { ctx.moveTo(0, y * cell + 0.5); ctx.lineTo(w, y * cell + 0.5); }
      ctx.stroke();
      if (!reduce) {
        if (Math.random() < 0.55) {
          const i = Math.floor(Math.random() * cols * rows);
          lit.push({ i, t0: now, c: colors[Math.floor(Math.random() * colors.length)], life: 1800 + Math.random() * 2600 });
        }
        // sequential run
        head = (head + 0.35) % (cols * rows);
        const hi = Math.floor(head);
        if (Math.random() < 0.5) lit.push({ i: hi, t0: now, c: "185,168,255", life: 1400 });
      }
      lit = lit.filter((l) => now - l.t0 < l.life);
      for (const l of lit) {
        const k = (now - l.t0) / l.life;
        const a = k < 0.12 ? k / 0.12 : 1 - (k - 0.12) / 0.88;
        const x = (l.i % cols) * cell, y = Math.floor(l.i / cols) * cell;
        ctx.fillStyle = `rgba(${l.c},${0.28 * a})`;
        ctx.fillRect(x + 2, y + 2, cell - 3, cell - 3);
      }
      if (!reduce) raf = requestAnimationFrame(draw);
    };
    if (reduce) {
      for (let n = 0; n < 160; n++) lit.push({ i: Math.floor(Math.random() * cols * rows), t0: performance.now() - 300, c: colors[n % colors.length], life: 1e9 });
      draw(performance.now());
    } else raf = requestAnimationFrame(draw);
    const vis = () => {
      cancelAnimationFrame(raf);
      if (!document.hidden && !reduce) raf = requestAnimationFrame(draw);
    };
    document.addEventListener("visibilitychange", vis);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      document.removeEventListener("visibilitychange", vis);
    };
  }, []);
  return (
    <canvas
      ref={ref}
      aria-hidden
      className={className}
      style={{ maskImage: "radial-gradient(ellipse 75% 70% at 50% 45%, black 20%, transparent 80%)", WebkitMaskImage: "radial-gradient(ellipse 75% 70% at 50% 45%, black 20%, transparent 80%)" }}
    />
  );
}
