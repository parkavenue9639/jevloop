import type { EscalationStats, Lane, LaneError, LanePhase, LaneState, Metrics } from "./types";
import type { StreamData } from "./stream";

/** Display metadata (i18n keys) for each lane, keyed by lane id. */
export const LANE_INFO: Record<Lane, { titleKey: string; subKey: string }> = {
  jev: { titleKey: "jevLane", subKey: "jevLaneSub" },
  baseline: { titleKey: "baseLane", subKey: "baseLaneSub" },
};

/** Derive a lane's coarse phase from its stream state.
 *  A lane error outranks completion; a finished stream without a final event
 *  (e.g. a crashed lane) still counts as "done". Untagged errors (no lane
 *  field, as older/stored runs may carry) degrade the default jev lane unless
 *  it already finished cleanly. */
export function lanePhase(
  lane: Lane,
  state: LaneState | undefined,
  errors: LaneError[],
  done: boolean,
): LanePhase {
  if (!state) return "idle";
  const errored = errors.some(
    (e) => e.lane === lane || (e.lane === null && lane === "jev" && !state.finished),
  );
  if (errored) return "error";
  if (state.finished || done) return "done";
  if (state.awaiting) return "awaiting";
  return "running";
}

/** Null-safe escalation stats — older stored runs may carry `escalations: null`. */
export function escalationStats(metrics: Metrics | null | undefined): EscalationStats {
  const esc = metrics?.escalations;
  if (!esc) return { count: 0, upheld: 0, overridden: 0 };
  return { count: esc.count ?? 0, upheld: esc.upheld ?? 0, overridden: esc.overridden ?? 0 };
}

/** The error message a lane should display, if any. Untagged errors (no lane
 *  field, as older/stored runs may carry) degrade to the jev lane unless it
 *  already finished cleanly — the same rule lanePhase applies. */
export function laneErrorMessage(
  lane: Lane,
  data: Pick<StreamData, "errors" | "lanes">,
): string | undefined {
  return data.errors.find(
    (e) => e.lane === lane || (e.lane === null && lane === "jev" && !data.lanes.jev?.finished),
  )?.message;
}
