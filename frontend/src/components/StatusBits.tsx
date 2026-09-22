import type { LanePhase } from "../types";
import { useT } from "../i18n";

const PHASE_META: Record<LanePhase, { key: string; cls: string }> = {
  idle: { key: "laneIdle", cls: "border-line text-ink2" },
  running: { key: "laneRunning", cls: "border-accent/50 text-accent" },
  awaiting: { key: "laneAwaiting", cls: "border-warn/70 text-ink" },
  done: { key: "laneDone", cls: "border-good/60 text-good" },
  error: { key: "laneError", cls: "border-critical/60 text-critical" },
};

/** Small pill stating a lane's coarse phase (running / awaiting / done / error). */
export function PhaseChip({ phase }: { phase: LanePhase }) {
  const t = useT();
  const meta = PHASE_META[phase];
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium ${meta.cls}`}>
      {phase === "running" && <Spinner />}
      {t(meta.key)}
    </span>
  );
}

/** Tailwind-only spinner (no dependency, no animation keyframes to add). */
export function Spinner({ className = "" }: { className?: string }) {
  return (
    <span
      aria-hidden
      className={`inline-block h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent align-[-1px] ${className}`}
    />
  );
}
