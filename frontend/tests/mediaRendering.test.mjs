import test from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";
import { emptyStream, reduceEvents } from "../src/stream.ts";
import { emptyPlayback } from "../src/playback.ts";

const image = { type: "image", asset_id: "b".repeat(64), mime_type: "image/png", width: 20, height: 10, detail: "high", name: "evidence.png" };
const step = { decision: { operation: "VIEW_IMAGE", confidence: null, latency_ms: null, decision_source: "visual_llm" },
  model_calls: [{ kind: "visual_decision", model: "vision" }], outcome: { status: "ok", images: [image] } };

test("actual conversation, tool row, composer and graph render persisted images and honest routes", async (t) => {
  // Compile actual TSX without a listening HTTP server or a model connection.
  const server = await createServer({ configFile: false, server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    esbuild: { jsx: "automatic" }, optimizeDeps: { noDiscovery: true, include: [] }, appType: "custom" });
  t.after(() => server.close());
  const [{ MessageList }, { ChatInput }, { LoopDiagram }] = await Promise.all([
    server.ssrLoadModule("/src/components/MessageList.tsx"),
    server.ssrLoadModule("/src/components/ChatInput.tsx"),
    server.ssrLoadModule("/src/components/LoopDiagram.tsx"),
  ]);
  const fetch = t.mock.method(globalThis, "fetch", () => { throw new Error("Rendering must be read-only"); });
  const events = [{ type: "meta", params: { goal: "", images: [image], profile: "paired_shadow" }, created_at: "2026-09-26" },
    { type: "step", lane: "jev", step }, { type: "done" }];
  const data = reduceEvents(events);
  const chat = { turns: ["saved"], activeRunId: "saved", stream: data, streamOf: () => data,
    canControl: false, pendingGoal: null, pendingImages: [], playback: emptyPlayback() };
  const saved = renderToStaticMarkup(createElement(MessageList, { chat }));
  assert.match(saved, /data-testid="user-bubble"/);
  assert.equal((saved.match(/<img /g) ?? []).length, 2); // user + attributed tool outcome
  assert.equal((saved.match(new RegExp(`src="/api/assets/${image.asset_id}"`, "g")) ?? []).length, 2);
  assert.match(saved, /视觉 LLM 决策/);
  const replayed = renderToStaticMarkup(createElement(MessageList, { chat: { ...chat, playback: { ...emptyPlayback(), status: "paused" } } }));
  assert.equal(replayed, saved);

  const pending = renderToStaticMarkup(createElement(MessageList, { chat: { ...chat, turns: [], pendingGoal: "", pendingImages: [image] } }));
  assert.match(pending, /data-testid="user-bubble"/);
  assert.match(pending, new RegExp(`src="/api/assets/${image.asset_id}"`));

  const composer = renderToStaticMarkup(createElement(ChatInput, { disabled: true, starting: false, error: null, onSend: () => assert.fail("Must not send") }));
  assert.match(composer, /data-testid="image-input"[^>]*disabled=""/);
  assert.match(composer, /type="submit" disabled=""/);

  for (const baseline of [false, true]) {
    const graph = renderToStaticMarkup(createElement(LoopDiagram, { state: data.lanes.jev, baseline, live: false, connected: false, done: true, error: false }));
    assert.match(graph, /视觉 LLM 决策/);
    assert.match(graph, /data-testid="visual-delegation"/);
    assert.doesNotMatch(graph, /原始选择|等待 Jev|choice-explainer|LLM 补参/);
  }
  assert.equal(fetch.mock.callCount(), 0);
  assert.deepEqual(emptyStream().params, null);
});
