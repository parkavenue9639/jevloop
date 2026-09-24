import test from "node:test";
import assert from "node:assert/strict";
import { applyRunEvent, emptyStream, reduceEvents } from "../src/stream.ts";
import { candidateView, observedRoute } from "../src/candidateView.ts";

const questions = {
  phase: { type: "choice", criteria: { INSPECT: "Inspect evidence", ACT: "Change something" } },
  action__inspect: { type: "choice", criteria: { READ_FILE: "Read a file", BASH: "Run a command" } },
  target__inspect__read_file: { type: "choice", criteria: { file_a: { arguments: '{"path":"a.py"}' }, LLM_PARAMETERS: "LLM fills parameters" } },
};
const response = { operation: "READ_FILE", binding_mode: "candidate", target: "file_a", consumed_heads: [
  { role: "action", head: "action__inspect", selected: "READ_FILE", probabilities: { READ_FILE: .8, BASH: .2 } },
  { role: "target", head: "target__inspect__read_file", selected: "file_a", probabilities: { file_a: .7, LLM_PARAMETERS: .3 } },
] };
const start = { type: "attempt_started", attempt_id: "a", step: 1 };
const request = { type: "jev_request", attempt_id: "a", questions };
const returned = { type: "jev_response", attempt_id: "a", response };

test("current request expands all submitted choice heads, without guessing picks", () => {
  const data = reduceEvents([start, request]);
  const view = candidateView(undefined, data.lanes.jev.decisionFrame);
  assert.equal(view.cards.length, 3);
  assert.equal(view.originalOperation, null);
  assert.equal(view.finalOperation, null);
  assert.ok(view.cards.every((q) => q.options.every((o) => !o.selected && o.probability === null)));
});

test("response highlights original picks before final weak-binding adjustment", () => {
  let data = reduceEvents([start, request, returned]);
  let view = candidateView(undefined, data.lanes.jev.decisionFrame);
  assert.equal(view.cards[1].selected, "file_a");
  assert.equal(view.finalOperation, null);
  data = applyRunEvent(data, { type: "decision_ready", attempt_id: "a", operation: "READ_FILE", binding_mode: "llm_parameters", needs_authoring: true });
  view = candidateView(undefined, data.lanes.jev.decisionFrame);
  assert.equal(view.originalBinding, "candidate");
  assert.equal(view.finalBinding, "llm_parameters");
  assert.equal(view.cards[1].selected, "file_a");
});

test("new attempt clears prior choices; delayed responses and other lanes cannot contaminate it", () => {
  let data = reduceEvents([start, request, returned, { ...start, attempt_id: "b", step: 2 }]);
  data = applyRunEvent(data, returned);
  data = applyRunEvent(data, { ...request, lane: "baseline", attempt_id: "b" });
  assert.equal(data.lanes.jev.decisionFrame.attemptId, "b");
  assert.equal(data.lanes.jev.decisionFrame.response, undefined);
  assert.equal(data.lanes.jev.decisionFrame.questions, undefined);
  assert.equal(data.lanes.baseline.decisionFrame, undefined);
  assert.equal(candidateView(undefined, data.lanes.jev.decisionFrame).cards.length, 0);
});

test("recorded step replaces transient telemetry and preserves replay selection", () => {
  const step = { decision: { ...response, operation: "BASH", binding_mode: "arbitrated" }, model_calls: [{ kind: "jev_decision", request: { questions }, response }] };
  const data = reduceEvents([start, request, returned, { type: "step", step }]);
  assert.equal(data.lanes.jev.decisionFrame, undefined);
  assert.equal(candidateView(data.lanes.jev.steps[0]).originalOperation, "READ_FILE");
  assert.equal(candidateView(data.lanes.jev.steps[0]).finalOperation, "BASH");
});

test("late telemetry after final does not change the frozen decision frame", () => {
  const data = reduceEvents([start, request, { type: "final", final: { answer: null }, metrics: {} }, returned]);
  assert.equal(data.lanes.jev.decisionFrame.response, undefined);
});

test("bookkeeping limits are never labeled as a Jev selection", () => {
  const view = candidateView({ decision: { operation: "RUN_LIMIT", confidence: null, latency_ms: null }, denied: "budget" });
  assert.equal(view.originalOperation, null);
  assert.equal(view.cards.length, 0);
});

test("legacy final proposals with unknown arbitration never claim an LLM-free route", () => {
  assert.deepEqual(observedRoute({ finalOperation: "READ_FILE", needsAuthoring: false }), { direct: false, assisted: false });
  assert.equal(observedRoute({ finalOperation: "READ_FILE", needsAuthoring: false, escalated: true }).direct, false);
  assert.equal(observedRoute({ finalOperation: "READ_FILE", needsAuthoring: false, escalated: false, finalBinding: "candidate" }).direct, true);
});

test("a raw Jev response or final proposal is not a durable transcript commit", () => {
  let data = reduceEvents([start, request, returned, { type: "decision_ready", attempt_id: "a", operation: "READ_FILE", needs_authoring: true }]);
  assert.equal(data.lanes.jev.decisionFrame.committed, undefined);
  data = applyRunEvent(data, { type: "intent", operation: "READ_FILE" });
  assert.equal(data.lanes.jev.decisionFrame.committed, true);
  data = applyRunEvent(data, { ...start, attempt_id: "b", step: 2 });
  assert.equal(data.lanes.jev.decisionFrame.committed, undefined);
});

test("LLM handoff appears while arbitration is in flight, before any final proposal or step", () => {
  const data = reduceEvents([start, request, returned, { type: "llm_started", attempt_id: "a", kind: "arbitration", operation: "READ_FILE", reason: "low_confidence" }]);
  const lane = data.lanes.jev;
  assert.equal(lane.steps.length, 0);
  assert.equal(lane.activity.stage, "authoring");
  assert.equal(lane.decisionFrame.finalOperation, undefined);
  assert.equal(lane.decisionFrame.llm.status, "running");
  assert.equal(observedRoute(lane.decisionFrame).assisted, true);
  assert.equal(candidateView(undefined, lane.decisionFrame).cards[0].selected, "READ_FILE");
});

test("completed LLM is not yet a committed or successful call", () => {
  let data = reduceEvents([start, request, returned, { type: "llm_started", attempt_id: "a", kind: "parameter_authoring", operation: "READ_FILE" }]);
  data = applyRunEvent(data, { type: "llm_completed", attempt_id: "a", kind: "parameter_authoring", operation: "READ_FILE", status: "returned" });
  assert.equal(data.lanes.jev.activity, null);
  assert.equal(data.lanes.jev.decisionFrame.committed, undefined);
  assert.equal(data.lanes.jev.decisionFrame.llm.status, "returned");
});

test("submitted pool stays complete after Jev picks its consumed heads", () => {
  const pending = candidateView(undefined, reduceEvents([start, request]).lanes.jev.decisionFrame);
  const complete = candidateView(undefined, reduceEvents([start, request, returned]).lanes.jev.decisionFrame);
  assert.deepEqual(complete.submitted.map((q) => [q.key, q.options.map((o) => o.key)]), pending.submitted.map((q) => [q.key, q.options.map((o) => o.key)]));
  assert.equal(complete.submitted[0].selected, null);
  assert.equal(complete.submitted[1].selected, "READ_FILE");
});
