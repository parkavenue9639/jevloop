import test from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";
import { applyRunEvent, reduceEvents } from "../src/stream.ts";
import { loopView } from "../src/loopView.ts";
import { candidateView, liveDecision, observedRoute } from "../src/candidateView.ts";

const questions = { action__inspect: { type: "choice", criteria: { VIEW_IMAGE: "Read an image for the task", READ_FILE: "Read text" } } };
const response = { operation: "VIEW_IMAGE", binding_mode: "candidate", confidence: .9, latency_ms: 3, target: "asset:image",
  consumed_heads: [{ role: "action", head: "action__inspect", selected: "VIEW_IMAGE", probabilities: { VIEW_IMAGE: .9, READ_FILE: .1 } }] };
const events = [
  { type: "attempt_started", attempt_id: "read", step: 1 },
  { type: "jev_request", attempt_id: "read", questions },
  { type: "jev_response", attempt_id: "read", response },
  { type: "decision_ready", attempt_id: "read", operation: "VIEW_IMAGE", needs_authoring: false, escalated: false, binding_mode: "candidate" },
  { type: "intent", operation: "VIEW_IMAGE" },
  { type: "dispatch_started", operation: "VIEW_IMAGE" },
];
const reading = { type: "llm_started", attempt_id: "read", intent_id: "read-intent", kind: "visual_read", operation: "VIEW_IMAGE" };
const completed = { ...reading, type: "llm_completed", status: "returned" };
const recorded = { intent_id: "read-intent", decision: response, model_calls: [
  { kind: "jev_decision", model: "jev", request: { questions }, response },
  { kind: "visual_read", model: "vision", latency_ms: 20 },
], outcome: { status: "ok", observation: { evidence: "The plotted series rises from 2 to 8." } } };
const opts = { live: true, connected: true, done: false, error: false };

test("visual_read stays in tool execution and does not replace Jev routing or imply LLM-free execution", () => {
  for (const lane of ["jev", "baseline"]) {
    const prefix = lane === "jev" ? events : events.filter((event) => !event.type.startsWith("jev_"));
    let stream = reduceEvents([...prefix, reading].map((event) => ({ ...event, lane })));
    const state = stream.lanes[lane];
    const view = loopView(state, { ...opts, baseline: lane === "baseline" });
    assert.equal(state.activity.stage, "executing");
    assert.equal(state.decisionFrame.visualRead.status, "running");
    assert.equal(state.decisionFrame.visualRead.intentId, "read-intent");
    assert.equal(state.decisionFrame.llm, undefined);
    assert.equal(view.active, "kernel");
    assert.equal(view.visual, false);
    assert.equal(view.visualRead, true);
    assert.equal(view.direct, false);
    assert.deepEqual(observedRoute(state.decisionFrame), { assisted: false, direct: false });
    assert.equal(candidateView(undefined, state.decisionFrame).originalOperation, lane === "jev" ? "VIEW_IMAGE" : null);
    assert.match(liveDecision(state.decisionFrame, false).at(-1), /Reading image with task context/);
    stream = applyRunEvent(stream, { ...completed, lane });
    assert.equal(loopView(stream.lanes[lane], { ...opts, baseline: lane === "baseline" }).active, "kernel");
    assert.match(liveDecision(stream.lanes[lane].decisionFrame, false).at(-1), /awaiting recorded tool result/);
    stream = applyRunEvent(stream, { type: "observation", lane, operation: "VIEW_IMAGE" });
    assert.equal(loopView(stream.lanes[lane], opts).active, null);
  }
});

test("visual read preserves earlier parameter-authoring/arbitration evidence and rejects stale completion", () => {
  for (const kind of ["parameter_authoring", "arbitration"]) {
    const prefix = [...events.slice(0, 3), { ...reading, kind }, { ...completed, kind }, ...events.slice(3), reading];
    let stream = reduceEvents(prefix);
    assert.equal(stream.lanes.jev.decisionFrame.llm.kind, kind);
    assert.equal(stream.lanes.jev.decisionFrame.llm.status, "returned");
    assert.equal(observedRoute(stream.lanes.jev.decisionFrame).assisted, true);
    assert.equal(loopView(stream.lanes.jev, opts).active, "kernel");
    stream = applyRunEvent(stream, { type: "attempt_started", attempt_id: "next", step: 2 });
    const next = stream.lanes.jev;
    stream = applyRunEvent(stream, completed);
    assert.equal(stream.lanes.jev, next);
    assert.equal(stream.lanes.jev.decisionFrame.visualRead, undefined);
  }
});

test("recorded and failed image reads retain normal Jev history without a fabricated authoring route", () => {
  for (const failed of [false, true]) {
    const stream = reduceEvents([...events, reading, { ...completed, status: failed ? "failed" : "returned" }]);
    assert.equal(stream.lanes.jev.decisionFrame.visualRead.status, failed ? "failed" : "returned");
    assert.equal(stream.lanes.jev.decisionFrame.llm, undefined);
    const step = failed ? { ...recorded, outcome: { status: "error", reason: "Image provider failed" } } : recorded;
    const persisted = applyRunEvent(stream, { type: "step", step });
    const view = loopView(persisted.lanes.jev, { ...opts, live: false });
    assert.equal(view.visual, false);
    assert.equal(view.assisted, false);
    assert.equal(view.direct, false);
    assert.deepEqual(view.route, ["state", "decision", "kernel", "evidence"]);
    assert.equal(candidateView(step).originalOperation, "VIEW_IMAGE");
  }
});

test("actual visual_read components preserve the standard graph, active execution node and visual observation", async (t) => {
  const server = await createServer({ configFile: false, server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    esbuild: { jsx: "automatic" }, optimizeDeps: { noDiscovery: true, include: [] }, appType: "custom" });
  t.after(() => server.close());
  const [{ LoopDiagram }, { ActivityGroup }, { ActivityRow }] = await Promise.all([
    server.ssrLoadModule("/src/components/LoopDiagram.tsx"), server.ssrLoadModule("/src/components/ActivityGroup.tsx"),
    server.ssrLoadModule("/src/components/ActivityRow.tsx"),
  ]);
  const state = reduceEvents([...events, reading]).lanes.jev;
  const graph = renderToStaticMarkup(createElement(LoopDiagram, { state, ...opts }));
  assert.match(graph, /data-testid="decision-flow"/);
  assert.match(graph, /data-testid="visual-read"/);
  assert.match(graph, /正在执行：结合任务上下文读取图片/);
  assert.match(graph, /class="flow-svg-node tone-tool is-active/);
  assert.doesNotMatch(graph, /class="flow-svg-node tone-llm is-active|data-testid="visual-delegation"|直通执行|旧路由记录/);
  assert.match(graph, /Jev 原始选择/);
  const conversation = renderToStaticMarkup(createElement(ActivityGroup, { state, lane: "jev", phase: "running", historical: true }));
  assert.match(conversation, /VIEW_IMAGE 工具内读图/);
  assert.doesNotMatch(conversation, /视觉 LLM 决策|LLM 正在生成/);
  const history = renderToStaticMarkup(createElement(ActivityRow, { step: recorded, index: 1, lane: "jev" }));
  assert.match(history, /data-testid="visual-observation"/);
  assert.match(history, /The plotted series rises from 2 to 8/);
  assert.match(history, /VIEW_IMAGE 工具内读图/);
  assert.doesNotMatch(history, /视觉 LLM 决策/);
});
