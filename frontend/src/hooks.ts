import { useEffect, useRef, useState } from "react";

/** Live elapsed: server metrics arrive per step, so between events we extrapolate
 *  from the last snapshot's wall-clock arrival. Replays finish instantly and the
 *  final metrics stay authoritative. */
export function useLiveElapsedMs(base: number | null, live: boolean): number | null {
  const [nowMs, setNowMs] = useState(() => performance.now());
  const baseAt = useRef(performance.now());
  useEffect(() => {
    baseAt.current = performance.now();
  }, [base]);
  useEffect(() => {
    if (!live) return;
    setNowMs(performance.now());
    const id = setInterval(() => setNowMs(performance.now()), 500);
    return () => clearInterval(id);
  }, [live]);
  if (base == null) return null;
  if (!live) return base;
  return base + Math.max(0, nowMs - baseAt.current);
}
