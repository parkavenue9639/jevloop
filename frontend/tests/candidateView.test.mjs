import test from "node:test";
import assert from "node:assert/strict";
import { candidateView, liveDecision } from "../src/candidateView.ts";

const original = {
  operation: "READ_FILE", phase: "INSPECT", binding_mode: "llm_parameters", target: null,
  consumed_heads: [
    { role: "phase", head: "phase", selected: "INSPECT", confidence: .91, probabilities: { INSPECT: .8, ACT: .2 } },
    { role: "action", head: "action__inspect", selected: "READ_FILE", confidence: .85, probabilities: { READ_FILE: .7, LIST_FILES: .3 } },
    { role: "target", head: "target__inspect__read_file", selected: "LLM_PARAMETERS", confidence: .82, probabilities: { file_a: .2, LLM_PARAMETERS: .8 } },
  ],
};
const request = { questions: {
  phase: { type: "choice", criteria: { INSPECT: "Inspect", ACT: "Act" } },
  action__inspect: { type: "choice", criteria: { READ_FILE: "Read", LIST_FILES: "List" } },
  target__inspect__read_file: { type: "choice", criteria: { file_a: { file: "a.py", arguments: '{"path":"a.py"}' }, LLM_PARAMETERS: { binding: "Author all parameters" } } },
  action__act: { type: "choice", criteria: { BASH: "Run" } },
} };
const step = { decision: { ...original, operation: "BASH", binding_mode: "arbitrated" }, escalation: { from: { action: "READ_FILE" }, to: { action: "BASH" } }, model_calls: [{ kind: "jev_decision", request, response: original }] };

test("original Jev pick stays distinct from the LLM override", () => {
  const view = candidateView(step);
  assert.equal(view.originalOperation, "READ_FILE");
  assert.equal(view.finalOperation, "BASH");
  assert.equal(view.cards[1].selected, "READ_FILE");
  assert.equal(view.originalBinding, "llm_parameters");
  assert.equal(view.finalBinding, "arbitrated");
});
test("LLM_PARAMETERS remains visibly selected even though target is null", () => {
  const target = candidateView(step).cards[2];
  assert.equal(target.selected, "LLM_PARAMETERS");
  assert.equal(target.options.find((option) => option.key === "LLM_PARAMETERS").selected, true);
  assert.match(target.options[0].detail, /a.py/);
});
test("unconsumed questions never borrow a pick or probability", () => {
  const view = candidateView(step);
  assert.equal(view.unused.length, 1);
  assert.equal(view.unused[0].key, "action__act");
  assert.equal(view.unused[0].selected, null);
  assert.equal(view.unused[0].options[0].probability, null);
});
test("deterministic constants do not display invented 100 percent model scores", () => {
  const response = { ...original, consumed_heads: [{ role: "action", head: null, selected: "ANSWER", probabilities: { ANSWER: 1 }, deterministic: true }] };
  const view = candidateView({ decision: response, model_calls: [{ kind: "jev_decision", response }] });
  assert.equal(view.cards[0].deterministic, true);
  assert.equal(view.cards[0].options[0].probability, null);
  assert.equal(view.cards[1].selected, "LLM_PARAMETERS");
  assert.equal(view.cards[1].deterministic, true);
});
test("missing original evidence on an overridden legacy step stays unknown", () => {
  const view = candidateView({ ...step, model_calls: [] });
  assert.equal(view.cards.length, 0);
  assert.equal(view.originalOperation, "READ_FILE");
  assert.equal(view.unused.length, 0);
});
test("invalid or missing values never become probability or argmax picks", () => {
  const response = { consumed_heads: [{ role: "action", head: "a", probabilities: { A: 2, B: NaN, C: .8 }, selected: null }] };
  const view = candidateView({ decision: {}, model_calls: [{ kind: "jev_decision", response }] });
  assert.equal(view.cards[0].selected, null);
  assert.deepEqual(view.cards[0].options.map((option) => option.probability), [null, null, .8]);
  assert.equal(view.cards[0].options.some((option) => option.selected), false);
});
test("multiple target membership heads preserve each include or skip decision", () => {
  const response = { consumed_heads: [
    { role: "target_member", head: "include__0", selected: "include", probabilities: { include: .9, skip: .1 } },
    { role: "target_member", head: "include__1", selected: "skip", probabilities: { include: .4, skip: .6 } },
  ] };
  const view = candidateView({ decision: {}, model_calls: [{ kind: "jev_decision", response }] });
  assert.deepEqual(view.cards.map((card) => card.selected), ["include", "skip"]);
});

test("live-decision milestones name the chosen decision model", () => {
  const frame = {
    attemptId: "a1", step: 1,
    questions: request.questions,
    response: original,
  };
  const jevLines = liveDecision(frame, false, "jev");
  assert.ok(jevLines.some((line) => line.includes("to Jev")));
  const layaLines = liveDecision(frame, false, "laya");
  assert.ok(layaLines.some((line) => line.includes("to Laya")));
  assert.ok(layaLines.some((line) => line.startsWith("Laya returned")));
});
