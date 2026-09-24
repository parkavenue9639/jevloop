import type { DecisionProvider, LanePhase, LaneState, Metrics, ModelCall } from "./types";
import type { StreamData } from "./stream";
import { lanePhase } from "./lane.ts";
import { diagramModel } from "./diagramModel.ts";

export interface SessionComparison {
  turns: number;
  completedTurns: number;
  jev: LaneState;
  baseline: LaneState;
  phases: { jev: LanePhase; baseline: LanePhase };
  /** the decision model of the latest paired turn; a session may mix */
  model: DecisionProvider;
}

const sum = (values: number[]) => values.reduce((total, value) => total + value, 0);
const nullableMax = (values: Array<number | null>): number | null => {
  const present = values.filter((value): value is number => value != null);
  return present.length ? Math.max(...present) : null;
};

const median = (values: number[]): number | null => {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2
    ? sorted[middle]
    : Math.round((sorted[middle - 1] + sorted[middle]) / 2);
};

export function aggregateMetrics(
  items: Array<Metrics | null | undefined>,
  modelCalls: ModelCall[] = [],
): Metrics | null {
  const metrics = items.filter((item): item is Metrics => item != null);
  if (!metrics.length) return null;
  const larkCalls = sum(metrics.map((item) => item.lark.calls));
  const larkMs = sum(metrics.map((item) => item.lark.executed_ms));
  const escalationDetails = metrics.flatMap((item) => item.escalations?.detail ?? []);
  const jevLatencies = modelCalls
    .filter((call) => call.kind === "jev_decision" && call.latency_ms != null)
    .map((call) => call.latency_ms as number);
  const helperLatencies = modelCalls
    .filter((call) => call.kind !== "jev_decision" && call.latency_ms != null)
    .map((call) => call.latency_ms as number);
  const routing = metrics.map((item) => item.routing).filter(
    (item): item is NonNullable<Metrics["routing"]> => item != null,
  );
  const jevSteps = sum(routing.map((item) => item.jev_steps));
  const directJevSteps = sum(routing.map((item) => item.direct_jev_steps));
  return {
    elapsed_ms: sum(metrics.map((item) => item.elapsed_ms)),
    jev: {
      calls: sum(metrics.map((item) => item.jev.calls)),
      median_ms: median(jevLatencies)
        ?? median(metrics.flatMap((item) => item.jev.median_ms == null ? [] : [item.jev.median_ms])),
      p95_ms: nullableMax(metrics.map((item) => item.jev.p95_ms)),
      max_ms: nullableMax(metrics.map((item) => item.jev.max_ms)),
      input_tokens: sum(metrics.map((item) => item.jev.input_tokens)),
      output_tokens: sum(metrics.map((item) => item.jev.output_tokens)),
      est_cost_usd: Number(sum(metrics.map((item) => item.jev.est_cost_usd)).toFixed(6)),
    },
    helper: {
      calls: sum(metrics.map((item) => item.helper.calls)),
      median_ms: median(helperLatencies)
        ?? median(metrics.flatMap((item) => item.helper.median_ms == null ? [] : [item.helper.median_ms])),
      total_ms: sum(metrics.map((item) => item.helper.total_ms)),
      input_tokens: sum(metrics.map((item) => item.helper.input_tokens)),
      output_tokens: sum(metrics.map((item) => item.helper.output_tokens)),
      cache_hit_tokens: sum(metrics.map((item) => item.helper.cache_hit_tokens)),
      cache_miss_tokens: sum(metrics.map(
        (item) => item.helper.cache_miss_tokens
          ?? Math.max(0, item.helper.input_tokens - item.helper.cache_hit_tokens),
      )),
      est_cost_usd: Number(sum(metrics.map((item) => item.helper.est_cost_usd)).toFixed(6)),
    },
    routing: routing.length ? {
      decision_steps: sum(routing.map((item) => item.decision_steps)),
      jev_steps: jevSteps,
      direct_jev_steps: directJevSteps,
      llm_assisted_jev_steps: sum(routing.map((item) => item.llm_assisted_jev_steps)),
      llm_avoidance_rate: jevSteps ? directJevSteps / jevSteps : null,
      plain_steps: sum(routing.map((item) => item.plain_steps)),
    } : undefined,
    est_cost_usd: Number(sum(metrics.map((item) => item.est_cost_usd)).toFixed(6)),
    pricing: metrics.find((item) => item.pricing)?.pricing,
    lark: {
      calls: larkCalls,
      executed_ms: larkMs,
      avg_ms: larkCalls ? Math.round(larkMs / larkCalls) : null,
      dry_run_calls: sum(metrics.map((item) => item.lark.dry_run_calls)),
      failed: sum(metrics.map((item) => item.lark.failed)),
    },
    denials: metrics.flatMap((item) => item.denials),
    escalations: {
      count: sum(metrics.map((item) => item.escalations?.count ?? 0)),
      upheld: sum(metrics.map((item) => item.escalations?.upheld ?? 0)),
      overridden: sum(metrics.map((item) => item.escalations?.overridden ?? 0)),
      detail: escalationDetails,
    },
  };
}

function aggregateLane(states: LaneState[]): LaneState {
  const steps = states.flatMap((state) => state.steps);
  const modelCalls = steps.flatMap((step) => step.model_calls ?? []);
  return {
    steps,
    metrics: aggregateMetrics(states.map((state) => state.metrics), modelCalls),
    awaiting: states.some((state) => state.awaiting),
    answer: [...states].reverse().find((state) => state.answer)?.answer ?? null,
    finished: states.length > 0 && states.every((state) => state.finished),
    activity: [...states].reverse().find((state) => state.activity)?.activity ?? null,
    unknownAcknowledgements: sum(
      states.map((state) => state.unknownAcknowledgements),
    ),
  };
}

export function aggregateSessionComparison(turns: StreamData[]): SessionComparison | null {
  const paired = turns.filter((turn) => turn.lanes.jev && turn.lanes.baseline);
  if (!paired.length) return null;
  const jevStates = paired.map((turn) => turn.lanes.jev as LaneState);
  const baselineStates = paired.map((turn) => turn.lanes.baseline as LaneState);
  const jev = aggregateLane(jevStates);
  const baseline = aggregateLane(baselineStates);
  const errors = paired.flatMap((turn) => turn.errors);
  const done = paired.every((turn) => turn.done);
  return {
    turns: paired.length,
    completedTurns: paired.filter((turn) => turn.done).length,
    jev,
    baseline,
    phases: {
      jev: lanePhase("jev", jev, errors, done),
      baseline: lanePhase("baseline", baseline, errors, done),
    },
    model: diagramModel(paired.at(-1)?.params, "jev"),
  };
}
