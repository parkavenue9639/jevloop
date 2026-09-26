import test from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createServer } from "vite";
import { costComplete, costText } from "../src/cost.ts";
import { aggregateMetrics, aggregateSessionComparison } from "../src/comparison.ts";
import { emptyPlayback } from "../src/playback.ts";
import { emptyStream } from "../src/stream.ts";

function metrics(complete, subtotal = 0) {
  return { elapsed_ms: 10, cost_complete: complete, est_cost_usd: subtotal,
    jev: { calls: 0, median_ms: null, p95_ms: null, max_ms: null, input_tokens: 0, output_tokens: 0, est_cost_usd: 0 },
    helper: { calls: 1, median_ms: 10, total_ms: 10, input_tokens: 10, output_tokens: 1, cache_hit_tokens: 0,
      cost_complete: complete, unpriced_calls: complete === false ? 1 : 0, est_cost_usd: subtotal },
    routing: { decision_steps: 1, jev_steps: 0, direct_jev_steps: 0, llm_assisted_jev_steps: 0, llm_avoidance_rate: null, plain_steps: 0, visual_steps: 1 },
    pricing: { jev_input_per_mtok: 1, llm_input_per_mtok: 2, llm_output_per_mtok: 3, llm_cache_hit_per_mtok: 1 },
    lark: { calls: 0, executed_ms: 0, avg_ms: null, dry_run_calls: 0, failed: 0 }, denials: [], escalations: null };
}
const lane = (m) => ({ steps: [{ decision: { operation: "ANSWER", confidence: null, target: null, latency_ms: 10, decision_source: "visual_llm" },
  model_calls: [{ kind: "visual_decision", model: "vision" }] }], metrics: m, awaiting: false, answer: null, finished: true, activity: null, unknownAcknowledgements: 0 });

test("unknown image pricing never becomes a free or comparable total; legacy flags preserve semantics", () => {
  assert.equal(costText(0, false, true), "未知（价格未配置）");
  assert.equal(costText(0, false, false), "Unknown (pricing not configured)");
  assert.match(costText(.01, false, true), /\$0.010000.*部分费用.*价格未配置/);
  assert.equal(costComplete(metrics(undefined)), true);
  assert.equal(costText(0, costComplete(metrics(undefined)), false), "$0.000000");
  assert.equal(costComplete({ ...metrics(true), helper: { ...metrics(false).helper } }), false);
});

test("cross-turn aggregation retains incomplete total/helper costs, unpriced calls and visual steps", () => {
  for (const items of [[metrics(false), metrics(true, .02)], [metrics(undefined, .02), metrics(false)]]) {
    const aggregate = aggregateMetrics(items);
    assert.equal(aggregate.cost_complete, false);
    assert.equal(aggregate.helper.cost_complete, false);
    assert.equal(aggregate.helper.unpriced_calls, 1);
    assert.equal(aggregate.routing.visual_steps, 2);
    assert.equal(aggregate.est_cost_usd, .02);
    assert.match(costText(aggregate.est_cost_usd, costComplete(aggregate), false), /partial cost/);
  }
  assert.equal(costComplete(aggregateMetrics([metrics(undefined), metrics(true)])), true);
});

test("rendered totals, session summary and meters label incomplete cost and never award a cost winner", async (t) => {
  const server = await createServer({ configFile: false, server: { middlewareMode: true, hmr: false, ws: false, watch: null },
    esbuild: { jsx: "automatic" }, optimizeDeps: { noDiscovery: true, include: [] }, appType: "custom" });
  t.after(() => server.close());
  const [{ CompareTable }, { SessionSummaryPanel }, { ActivityGroup }, { LoopConsole }] = await Promise.all([
    server.ssrLoadModule("/src/components/CompareTable.tsx"), server.ssrLoadModule("/src/components/SessionSummaryPanel.tsx"),
    server.ssrLoadModule("/src/components/ActivityGroup.tsx"), server.ssrLoadModule("/src/components/LoopConsole.tsx"),
  ]);
  const jev = lane(metrics(false));
  const baseline = lane(metrics(true, .01));
  const table = renderToStaticMarkup(createElement(CompareTable, { jev, baseline, phases: { jev: "done", baseline: "done" } }));
  for (const label of ["LLM 预估费用", "预估成本", "费用占比"]) {
    const row = table.match(new RegExp(`<tr[^>]*>(?:(?!</tr>)[\\s\\S])*?${label}(?:(?!</tr>)[\\s\\S])*?</tr>`))?.[0];
    assert.ok(row, label);
    assert.match(row, /价格未配置/);
    assert.doesNotMatch(row, /bg-good|text-good|∞|Infinity|100.0%/);
  }
  assert.doesNotMatch(table, /估价口径/);
  const single = renderToStaticMarkup(createElement(ActivityGroup, { lane: "jev", state: jev, phase: "done" }));
  assert.match(single, /未知（价格未配置）/);
  const stream = { ...emptyStream(), done: true, lanes: { jev, baseline }, params: { goal: "Inspect image" } };
  const next = { ...stream, lanes: { jev: lane(metrics(true, .02)), baseline } };
  const comparison = aggregateSessionComparison([stream, next]);
  assert.equal(comparison.jev.metrics.cost_complete, false);
  const summary = renderToStaticMarkup(createElement(SessionSummaryPanel, { comparison }));
  assert.match(summary, /部分费用；价格未配置/);
  assert.doesNotMatch(summary, /\$\$/);
  const chat = { turns: ["cost"], activeRunId: "cost", stream, streamOf: () => stream,
    canControl: false, pendingGoal: null, pendingImages: [], playback: emptyPlayback() };
  const graph = renderToStaticMarkup(createElement(LoopConsole, { chat }));
  assert.match(graph, /未知（价格未配置）/);
  const meters = graph.match(/<div class="meter-row[^\"]*is-incomplete[^\"]*">[\s\S]*?<\/strong><\/div>/g);
  assert.equal(meters?.length, 2);
  for (const meter of meters) assert.doesNotMatch(meter, /width:/);
});
