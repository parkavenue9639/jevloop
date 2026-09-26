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
  const visualRead = (options.step ? false : !!state?.decisionFrame?.visualRead)
    || !!calls?.some((call) => call.kind === "visual_read");
  const visual = (options.step ? false : state?.decisionFrame?.llm?.kind === "visual_decision")
    || step?.decision.decision_source === "visual_llm"
    || !!calls?.some((call) => call.kind === "visual_decision");
  // The compact LLM circuit has one decision node for decision + parameters.
  if ((options.baseline || visual) && active === "authoring") active = "decision";
  // Legacy records without model calls cannot establish an LLM-free route.
  // visual_read runs inside the tool: it is neither decision assistance nor
  // an LLM-free execution, so it must not highlight either of those routes.
  const assisted = options.baseline || visual || !!step?.escalation || !!step?.outcome?.helper
    || calls?.some((call) => call.kind !== "jev_decision" && call.kind !== "visual_read");
  const direct = !options.baseline && !!calls?.length && !assisted && !visualRead;
  const decisionObserved = !!calls?.length || step?.decision.confidence != null || step?.decision.latency_ms != null;
  const route: LoopNode[] = step && decisionObserved ? ["state", "decision"] : [];
  if (assisted && !options.baseline && !visual) route.push("authoring");
  if (direct) route.push("binding");
  if (step?.outcome || step?.denied || step?.aborted) route.push("kernel");
  if (step) route.push("evidence");
  return { status, active, route, step, direct, assisted, visual, visualRead, blocked: !!(step?.denied || step?.aborted) };
}

export function formatTime(ms: number | null | undefined): string {
  return ms == null ? "—" : ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
}

export function formatCost(cost: number | null | undefined): string {
  return cost == null ? "—" : `$${cost.toFixed(5)}`;
}
