import { useCallback, useEffect, useRef, useState } from "react";

export const SPEEDS = [0.5, 1, 2, 4] as const;

export interface Playback {
  index: number;
  length: number;
  playing: boolean;
  speed: number;
  atEnd: boolean;
  play(): void;
  pause(): void;
  toggle(): void;
  step(delta: number): void;
  seek(i: number): void;
  restart(): void;
  setSpeed(s: number): void;
  /** direction of the last index change: +1 forward (play/step), -1 backward, 0 jump */
  dir: number;
}

/**
 * Drives an index through 0..length-1. `dwellMs(i)` = how long event i stays on screen at 1x
 * (crashes and phases linger so the room can read them).
 */
export function usePlayback(length: number, opts: { resetKey: unknown; autoplay?: boolean; dwellMs?: (i: number) => number; initialSpeed?: number }): Playback {
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(!!opts.autoplay);
  const [speed, setSpeed] = useState(opts.initialSpeed ?? 1);
  const [dir, setDir] = useState(0);
  const dwell = useRef(opts.dwellMs);
  dwell.current = opts.dwellMs;

  // new data -> rewind
  useEffect(() => {
    setIndex(0);
    setDir(0);
    setPlaying(!!opts.autoplay && length > 1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opts.resetKey]);

  useEffect(() => {
    if (!playing) return;
    if (index >= length - 1) {
      setPlaying(false);
      return;
    }
    const ms = (dwell.current?.(index) ?? 900) / speed;
    const t = setTimeout(() => {
      setDir(1);
      setIndex((i) => Math.min(length - 1, i + 1));
    }, ms);
    return () => clearTimeout(t);
  }, [playing, index, length, speed]);

  const step = useCallback(
    (d: number) => {
      setPlaying(false);
      setDir(Math.sign(d));
      setIndex((i) => Math.max(0, Math.min(length - 1, i + d)));
    },
    [length],
  );
  const seek = useCallback(
    (i: number) => {
      setDir(0);
      setIndex(Math.max(0, Math.min(length - 1, i)));
    },
    [length],
  );

  return {
    index: Math.min(index, Math.max(0, length - 1)),
    length,
    playing,
    speed,
    dir,
    atEnd: index >= length - 1,
    play: () => {
      if (index >= length - 1) setIndex(0);
      setPlaying(true);
    },
    pause: () => setPlaying(false),
    toggle: () => {
      if (playing) setPlaying(false);
      else {
        if (index >= length - 1) setIndex(0);
        setPlaying(true);
      }
    },
    step,
    seek,
    restart: () => {
      setDir(0);
      setIndex(0);
      setPlaying(true);
    },
    setSpeed,
  };
}
