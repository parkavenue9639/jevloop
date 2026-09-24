import type { LaneState, Step } from "./types";

export type LoopNode = "state" | "decision" | "binding" | "authoring" | "kernel" | "evidence";
export type LoopStatus = "ready" | "recorded" | "live" | "waiting" | "paused" | "error" | "ended" | "disconnected";

/** Visualization only: never infer execution or success from a decision. */
export function loopView(state: LaneState | undefined, options: {
  live: boolean;
  connected: boolean;
  done: boolean;
  error: boolean;
  baseline?: boolean;
  step?: Step;
}) {
  let status: LoopStatus = "ready";
  let active: LoopNode | null = null;
  if (options.error) status = "error";
  else if (state?.finished || options.done) status = "ended";
  else if (state?.awaiting) status = "paused";
  else if (options.live && !options.connected) status = "disconnected";
  else if (options.live) {
    status = state?.activity ? "live" : "waiting";
    const stage = state?.activity?.stage;
    active = stage === "starting" ? "state"
      : stage === "deciding" ? "decision"
      : stage === "authoring" ? "authoring"
      : stage === "preparing" || stage === "executing" ? "kernel" : null;
  } else if (state) status = "recorded";

  const step = options.step ?? (state?.decisionFrame ? undefined : state?.steps.at(-1));
  const calls = step?.model_calls;
  // Legacy records without model calls cannot establish an LLM-free route.
  const assisted = options.baseline || !!step?.escalation || !!step?.outcome?.helper
    || calls?.some((call) => call.kind !== "jev_decision");
  const direct = !options.baseline && !!calls?.length && !assisted;
  const decisionObserved = !!calls?.length || step?.decision.confidence != null || step?.decision.latency_ms != null;
  const route: LoopNode[] = step && decisionObserved ? ["state", "decision"] : [];
  if (assisted && !options.baseline) route.push("authoring");
  if (direct) route.push("binding");
  if (step?.outcome || step?.denied || step?.aborted) route.push("kernel");
  if (step) route.push("evidence");
  return { status, active, route, step, direct, assisted, blocked: !!(step?.denied || step?.aborted) };
}

export function formatTime(ms: number | null | undefined): string {
  return ms == null ? "—" : ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
}

export function formatCost(cost: number | null | undefined): string {
  return cost == null ? "—" : `$${cost.toFixed(5)}`;
}
