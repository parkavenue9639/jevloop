import test from "node:test";
import assert from "node:assert/strict";
import { describeQuestion, describeOption } from "../src/candidateSemantics.ts";

const route = { phase: "RESPOND", operation: "ANSWER" };
const question = (changes) => ({ key: "phase", role: "phase", selected: null, confidence: null,
  deterministic: false, consumed: false, options: [], ...changes });

test("compiler heads expose hierarchy and ownership without pretending they are active", () => {
  const result = describeQuestion(question({ key: "target__inspect__read_file", role: "unconsumed",
    options: [{ key: "R1", probability: .99, selected: true, detail: "" }] }), route, true);
  assert.equal(result.level, 3);
  assert.equal(result.role, "target");
  assert.equal(result.phase, "INSPECT");
  assert.equal(result.operation, "READ_FILE");
  assert.equal(result.active, false);
  assert.match(result.condition, /INSPECT → READ_FILE/);
  assert.equal(describeQuestion(question({ key: "action__act", role: "unconsumed" }), route, true).level, 2);
});

test("engineering constants use their consumed role and original route", () => {
  const action = describeQuestion(question({ key: "constant-1", role: "action", deterministic: true,
    consumed: true, selected: "ANSWER" }), route, true);
  assert.equal(action.level, 2);
  assert.equal(action.phase, "RESPOND");
  assert.equal(action.operation, null);
  assert.equal(action.active, true);
  const binding = describeQuestion(question({ key: "constant-binding", role: "target", deterministic: true,
    consumed: true, selected: "LLM_PARAMETERS" }), route, false);
  assert.equal(binding.operation, "ANSWER");
  assert.match(binding.condition, /RESPOND → ANSWER/);
});

test("unconsumed constants and unknown legacy heads do not borrow route ownership", () => {
  for (const value of [question({ key: "constant-1", role: "action", deterministic: true }),
    question({ key: "legacy-target", role: "target", consumed: true, selected: "R2" }),
    question({ key: "include__2", role: "target_member", consumed: true, selected: "include" })]) {
    const result = describeQuestion(value, route, true);
    assert.equal(result.phase, null);
    assert.equal(result.operation, null);
    assert.match(result.condition, /未提供/);
  }
  const unknown = describeQuestion(question({ key: "custom_score", role: "choice" }), route, false);
  assert.equal(unknown.role, "unknown");
  assert.equal(unknown.title, "custom_score");
});

test("batch mode and membership have distinct semantics; index is never a reference", () => {
  const mode = describeQuestion(question({ key: "target_mode__inspect__read_file", role: "unconsumed" }), route, true);
  assert.equal(mode.role, "target_mode");
  assert.match(mode.explanation, /one.*many/);
  const member = describeQuestion(question({ key: "include__inspect__read_file__9", role: "unconsumed",
    options: [{ key: "include", detail: "Include offered target 'R27'." }] }), route, true);
  assert.equal(member.subject, "R27");
  assert.match(member.condition, /INSPECT → READ_FILE.*many/);
  assert.equal(describeQuestion(question({ key: "include__inspect__read_file__9", role: "unconsumed" }), route, true).subject, null);
  assert.equal(describeQuestion(question({ key: "include__inspect__read_file__9", role: "target_member",
    options: [{ key: "include", detail: "Original unknown description" }] }), route, true).subject, "Original unknown description");
});

test("binding glosses distinguish fixed rules, defaults, parameter authoring and batch decisions", () => {
  assert.match(describeOption("LLM_PARAMETERS", "target", true), /完整参数.*不换工具/);
  assert.match(describeOption("DEFAULT_ARGUMENTS", "target", true), /展示.*默认参数/);
  assert.match(describeOption("R", "deterministic", true), /规则确定.*非模型概率/);
  assert.equal(describeOption("R12", "target", true), "R12");
  assert.match(describeOption("one", "target_mode", false), /Single/);
  assert.match(describeOption("many", "target_mode", false), /Batch/);
  assert.match(describeOption("include", "target_member", false), /Include/);
  assert.match(describeOption("skip", "target_member", false), /Exclude/);
  assert.equal(describeOption("R12", "action", true), "R12");
  assert.equal(describeOption("UNKNOWN", "target", true), "UNKNOWN");
});
