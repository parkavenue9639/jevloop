import type { Lane, LaneError, LaneState, RunEvent } from "./types";

export const emptyLane = (): LaneState => ({
  steps: [], metrics: null, awaiting: false, answer: null, finished: false,
  activity: null, unknownAcknowledgements: 0,
});

/** Everything derived from one run's event stream. `useRunStream` keeps this
 *  in React state; the conversation model snapshots it when a turn retires. */
export interface StreamData {
  /** Transport state is presentation-only; historical JSON replay has none. */
  connection?: "connecting" | "connected" | "reconnecting" | "closed";
  lanes: Partial<Record<Lane, LaneState>>;
  errors: LaneError[];
  done: boolean;
  /** run params from the meta event — the goal feeds the chat's user bubble */
  params: Record<string, unknown> | null;
  createdAt: string | null;
}

export const emptyStream = (): StreamData => ({
  lanes: {}, errors: [], done: false, params: null, createdAt: null,
});

const patch = (data: StreamData, lane: Lane, fn: (l: LaneState) => LaneState): StreamData => ({
  ...data,
  lanes: { ...data.lanes, [lane]: fn(data.lanes[lane] ?? emptyLane()) },
});

/** Pure SSE-event reducer, shared by the live subscription and by replay/test
 *  harnesses. Mirrors the server contract: lane defaults to "jev"; terminal
 *  steps (no decision) are bookkeeping and never join the timeline; the final
 *  event's metrics snapshot is authoritative. */
export function applyRunEvent(data: StreamData, payload: RunEvent): StreamData {
  switch (payload.type) {
    case "meta":
      return { ...data, params: payload.params ?? {}, createdAt: payload.created_at ?? null };
    case "sandbox_ready":
      return payload.lanes.reduce(
        (next, lane) => patch(next, lane, (l) => ({
          ...l, activity: { stage: "starting" },
        })),
        data,
      );
    case "attempt_started":
      return patch(data, payload.lane ?? "jev", (l) => ({
        ...l, awaiting: false, activity: { stage: "deciding" },
        decisionFrame: { attemptId: payload.attempt_id, step: payload.step },
      }));
    case "jev_request":
    case "jev_response":
      return patch(data, payload.lane ?? "jev", (l) => {
        if (l.finished || l.decisionFrame?.attemptId !== payload.attempt_id) return l;
        return { ...l, decisionFrame: { ...l.decisionFrame,
          ...(payload.type === "jev_request" ? { questions: payload.questions } : { response: payload.response }),
        } };
      });
    case "llm_started":
    case "llm_completed":
      return patch(data, payload.lane ?? "jev", (l) => {
        if (l.finished || l.decisionFrame?.attemptId !== payload.attempt_id) return l;
        return { ...l, activity: payload.type === "llm_started" ? { stage: "authoring", operation: payload.operation } : null,
          decisionFrame: { ...l.decisionFrame, llm: { kind: payload.kind, operation: payload.operation, reason: payload.reason,
            status: payload.type === "llm_started" ? "running" : payload.status } } };
      });
    case "decision_ready":
      return patch(data, payload.lane ?? "jev", (l) => ({
        ...l,
        decisionFrame: l.decisionFrame?.attemptId === payload.attempt_id ? {
          ...l.decisionFrame, finalOperation: payload.operation, finalBinding: payload.binding_mode,
          needsAuthoring: payload.needs_authoring, escalated: payload.escalated,
        } : l.decisionFrame,
        activity: {
          stage: payload.needs_authoring ? "authoring" : "preparing",
          operation: payload.operation,
        },
      }));
    case "intent":
      return patch(data, payload.lane ?? "jev", (l) => ({
        ...l, activity: { stage: "preparing", operation: payload.operation },
        decisionFrame: l.decisionFrame ? { ...l.decisionFrame, committed: payload.operation !== "BLOCKED" } : undefined,
      }));
    case "dispatch_started":
      return patch(data, payload.lane ?? "jev", (l) => ({
        ...l, activity: { stage: "executing", operation: payload.operation },
      }));
    case "observation":
      return patch(data, payload.lane ?? "jev", (l) => ({
        ...l, activity: null,
      }));
    case "unknown_acknowledged":
      return patch(data, payload.lane ?? "jev", (l) => ({
        ...l,
        unknownAcknowledgements: l.unknownAcknowledgements + payload.count,
      }));
    case "step":
      // a fresh step also implies a paused lane has resumed; steps without a
      // decision are terminal bookkeeping ("final": completed/stopped) and are skipped
      if (!payload.step?.decision) return data;
      return patch(data, payload.lane ?? "jev", (l) => ({
        ...l, steps: [...l.steps, payload.step], awaiting: false, activity: null,
        decisionFrame: undefined,
      }));
    case "metrics":
      return patch(data, payload.lane ?? "jev", (l) => ({ ...l, metrics: payload.metrics }));
    case "awaiting_continue":
      return patch(data, payload.lane ?? "jev", (l) => ({
        ...l, awaiting: true, activity: null,
      }));
    case "final":
      // final carries the authoritative last metrics snapshot — apply it so
      // every panel shows the same numbers (no drift between tiles and table)
      return patch(data, payload.lane ?? "jev", (l) => ({
        ...l, awaiting: false, finished: true, activity: null,
        metrics: payload.metrics,
        answer: payload.final.answer ?? l.answer,
      }));
    case "error":
      return { ...data, errors: [...data.errors, { lane: payload.lane ?? null, message: payload.message }] };
    case "done":
      return { ...data, done: true };
    default:
      return data;
  }
}

/** Fold an ordered event sequence into stream state. */
export function reduceEvents(events: RunEvent[]): StreamData {
  return events.reduce(applyRunEvent, emptyStream());
}
