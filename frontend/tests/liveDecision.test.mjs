import test from "node:test";
import assert from "node:assert/strict";
import { liveDecision } from "../src/candidateView.ts";
import { applyRunEvent, emptyStream } from "../src/stream.ts";

test("conversation milestones render before a recorded step", () => {
  let data = applyRunEvent(emptyStream(), { type: "attempt_started", attempt_id: "a", step: 1 });
  const emit = (event) => { data = applyRunEvent(data, { attempt_id: "a", ...event }); return liveDecision(data.lanes.jev.decisionFrame, false); };
  assert.deepEqual(emit({ type: "jev_request", questions: { phase: { type: "choice", criteria: { INSPECT: "Inspect", ACT: "Act" } } } }), ["Sent 1 groups / 2 candidates to Jev"]);
  const response = { operation: "READ_FILE", binding_mode: "llm_parameters", consumed_heads: [{ head: "phase", role: "phase", selected: "INSPECT" }] };
  const selected = emit({ type: "jev_response", response });
  assert.equal(selected[0], "Sent 1 groups / 2 candidates to Jev"); // excludes deterministic bindings
  assert.match(selected[1], /INSPECT → LLM_PARAMETERS/);
  assert.match(emit({ type: "llm_started", kind: "parameter_authoring", operation: "READ_FILE" }).at(-1), /LLM parameters · READ_FILE · started/);
  assert.match(emit({ type: "llm_completed", kind: "parameter_authoring", operation: "READ_FILE", status: "returned" }).at(-1), /not yet a commit/);
  assert.equal(data.lanes.jev.steps.length, 0);
  assert.match(emit({ type: "intent", operation: "READ_FILE" }).at(-1), /Call committed/);
});

test("failed helper never implies a commit or success", () => {
  const lines = liveDecision({ attemptId: "a", step: 1, llm: { kind: "arbitration", operation: "BASH", status: "failed" } }, true);
  assert.deepEqual(lines, ["LLM 复核 · BASH · 失败"]);
});
