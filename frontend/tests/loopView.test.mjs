import test from "node:test";
import assert from "node:assert/strict";
import { loopView, formatCost, formatTime } from "../src/loopView.ts";
import { applyRunEvent, emptyStream, reduceEvents } from "../src/stream.ts";

const options = { live: true, connected: true, done: false, error: false };
const decision = { operation: "READ_FILE", confidence: .9, target: "a.py", latency_ms: 123 };
const direct = { decision, model_calls: [{ kind: "jev_decision", model: "jev" }], outcome: { status: "ok" } };

test("live nodes follow actual stage events, not a timer-driven loop", () => {
  let data = emptyStream();
  for (const [event, node] of [
    [{ type: "sandbox_ready", lanes: ["jev"], image_id: "test" }, "state"],
    [{ type: "attempt_started", attempt_id: "a", step: 1 }, "decision"],
    [{ type: "decision_ready", attempt_id: "a", operation: "READ_FILE", needs_authoring: true }, "authoring"],
    [{ type: "intent", operation: "READ_FILE" }, "kernel"],
    [{ type: "dispatch_started", operation: "READ_FILE" }, "kernel"],
    [{ type: "observation" }, null],
    [{ type: "step", step: direct }, null],
  ]) {
    data = applyRunEvent(data, event);
    assert.equal(loopView(data.lanes.jev, options).active, node);
  }
});

test("a complete binding does not mark LLM assistance active", () => {
  const data = reduceEvents([{ type: "decision_ready", attempt_id: "a", operation: "READ_FILE", needs_authoring: false }]);
  assert.equal(loopView(data.lanes.jev, options).active, "kernel");
});

test("historical, disconnected, paused, errored and ended lanes never animate", () => {
  const state = { steps: [], activity: { stage: "executing" }, finished: false };
  for (const [lane, opts, expected] of [
    [state, { live: false }, "recorded"],
    [state, { connected: false }, "disconnected"],
    [{ ...state, awaiting: true }, {}, "paused"],
    [state, { error: true }, "error"],
    [state, { done: true }, "ended"],
    [{ ...state, finished: true }, {}, "ended"],
  ]) {
    const view = loopView(lane, { ...options, ...opts });
    assert.equal(view.active, null);
    assert.equal(view.status, expected);
  }
});

test("a paused lane does not suppress the other lane's activity", () => {
  const data = reduceEvents([
    { type: "awaiting_continue", lane: "jev", step_index: 0 },
    { type: "dispatch_started", lane: "baseline", operation: "BASH" },
  ]);
  assert.equal(loopView(data.lanes.jev, options).status, "paused");
  assert.equal(loopView(data.lanes.baseline, options).active, "kernel");
});

test("no authoring/escalation subtraction to guess a legacy direct route", () => {
  const view = loopView(undefined, { ...options, step: { decision } });
  assert.equal(view.direct, false);
  assert.equal(view.route.includes("binding"), false);
});

test("a pending attempt does not borrow a historical route", () => {
  const state = { steps: [direct], decisionFrame: { attemptId: "next", step: 2 }, activity: { stage: "deciding" } };
  const current = loopView(state, options);
  assert.equal(current.step, undefined);
  assert.deepEqual(current.route, []);
  assert.equal(loopView(state, { ...options, live: false, step: direct }).step, direct);
});

test("recorded LLM calls, including parameter authoring and arbitration, use assistance", () => {
  for (const kind of ["parameter_authoring", "arbitration", "authoring", "future_helper"]) {
    const step = { ...direct, model_calls: [...direct.model_calls, { kind, model: "llm" }] };
    const view = loopView(undefined, { ...options, step });
    assert.equal(view.direct, false);
    assert.equal(view.route.includes("authoring"), true);
  }
});

test("an LLM-free denied step is blocked, never equated with tool success", () => {
  const view = loopView(undefined, { ...options, step: { ...direct, outcome: undefined, denied: "budget" } });
  assert.equal(view.direct, true);
  assert.equal(view.blocked, true);
});

test("baseline never uses a Jev direct candidate path", () => {
  const view = loopView(undefined, { ...options, baseline: true, step: direct });
  assert.equal(view.direct, false);
  assert.equal(view.route.includes("binding"), false);
  assert.equal(view.route.includes("authoring"), false);
});

test("live and replay folds retain identical authoritative final metrics", () => {
  const metrics = { elapsed_ms: 999, est_cost_usd: .00051 };
  const events = [
    { type: "metrics", metrics: { elapsed_ms: 200 } },
    { type: "step", step: direct },
    { type: "final", final: { answer: null }, metrics },
    { type: "done" },
  ];
  const replay = reduceEvents(events);
  const live = events.reduce(applyRunEvent, emptyStream());
  assert.deepEqual(live, replay);
  assert.equal(replay.lanes.jev.metrics, metrics);
  assert.equal(loopView(replay.lanes.jev, { ...options, done: replay.done }).status, "ended");
  assert.equal(replay.lanes.jev.answer, null);
});

test("formatters distinguish unknown measurements from zero", () => {
  assert.equal(formatCost(null), "—");
  assert.equal(formatCost(0), "$0.00000");
  assert.equal(formatTime(undefined), "—");
  assert.equal(formatTime(0), "0 ms");
  assert.equal(formatTime(2500), "2.5 s");
});
