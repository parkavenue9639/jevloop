import test from "node:test";
import assert from "node:assert/strict";
import { flowLayout, contextWires, horizontalCurve, NODE_WIDTH, NODE_HALF_HEIGHT, OPTION_HALF_WIDTH } from "../src/flowLayout.ts";

const question = (count, pick = null) => ({ key: "target", role: "target", options: Array.from({ length: count }, (_, i) => ({ key: `candidate-${i}`, selected: i === pick, detail: "", probability: null })) });

test("horizontal main route and fan ports remain separated", () => {
  for (const count of [1, 2, 4, 5]) {
    const graph = flowLayout([question(count)]);
    const fan = graph.groups[0];
    assert.ok(graph.jevProjection.x < graph.jev.x && graph.jev.x < fan.start.x);
    assert.ok(fan.options.every((o) => o.x - OPTION_HALF_WIDTH > fan.start.x && o.x + OPTION_HALF_WIDTH < fan.end.x));
    assert.ok(fan.end.x < graph.exit.x - NODE_WIDTH / 2);
    assert.ok(graph.exit.x < graph.commit.x && graph.commit.x < graph.kernel.x);
    assert.equal(graph.exit.y, graph.kernel.y);
    assert.equal(graph.llm.x, graph.exit.x);
    assert.ok(graph.llm.y - NODE_HALF_HEIGHT > graph.exit.y + NODE_HALF_HEIGHT);
  }
});

test("every candidate including late selections remains present and inside the canvas", () => {
  const input = [question(40, 38), {...question(3), key:"a"}, {...question(20), key:"b"}];
  const graph = flowLayout(input);
  assert.equal(graph.groups[0].options.length, 40);
  assert.equal(graph.groups[0].options.find((o) => o.selected).key, "candidate-38");
  for (const g of graph.groups) {
    for (const o of g.options) {
      assert.ok(o.x - OPTION_HALF_WIDTH > 0 && o.x + OPTION_HALF_WIDTH < graph.width);
      assert.ok(o.y + 14 < graph.selectionBusY);
    }
  }
  assert.ok(graph.height > graph.selectionBusY);
  assert.ok(graph.record.y + 22 < graph.height - 24);
  assert.equal(input[0].options.length, 40);
});

test("activation/selection does not change candidate coordinates or main node positions", () => {
  const requested = [question(4), { ...question(6), key: "a" }, { ...question(3), key: "b" }];
  const returned = requested.map((q) => ({ ...q, consumed: true, options: q.options.map((o, i) => ({ ...o, selected: i === 1 })) }));
  const coordinates = (items) => {
    const g = flowLayout(items);
    return {...g, groups: g.groups.map(group => ({start:group.start,end:group.end,options:group.options.map(({x,y,key})=>({x,y,key}))}))};
  };
  assert.deepEqual(coordinates(requested), coordinates(returned));
});

test("empty and empty-head layouts stay finite without synthetic candidates", () => {
  for (const input of [[], [question(0)]]) {
    const g = flowLayout(input);
    assert.equal(g.groups.flatMap(x=>x.options).length, 0);
    assert.ok(Number.isFinite(g.width) && Number.isFinite(g.height));
    assert.ok(g.transcript.y < g.jev.y);
    assert.ok(g.kernel.x + NODE_WIDTH / 2 * 1.1 < g.width);
  }
  assert.equal(horizontalCurve({x:10,y:20},{x:50,y:80}), "M10 20 C30 20 30 80 50 80");
});

test("context restore leaves transcript bottom and enters LLM top, not its output port", () => {
  for (const input of [[], [question(1)], [question(3), {...question(3),key:'a'}]]) {
    const g = flowLayout(input);
    const wires = contextWires(g);
    assert.ok(wires.transcriptToLlm.startsWith(`M${g.transcript.x + 80} ${g.transcript.y + NODE_HALF_HEIGHT} V92`));
    assert.ok(wires.contextToLlm.endsWith(`V${g.llm.y - NODE_HALF_HEIGHT}`));
    assert.ok(wires.contextToLlm.includes(`V${g.llm.y - 48} H${g.llm.x + 45}`));
    assert.ok(!wires.contextToLlm.includes(`V${g.llm.y} `));
    assert.ok(wires.acceptedProgress.includes(`H${Math.max(g.commit.x,g.transcript.x+180)} V12`));
  }
});

test("many empty candidate groups still fit inside the expanded canvas", () => {
  const g = flowLayout(Array.from({length:20},(_,i)=>({...question(0),key:`empty-${i}`})),800);
  assert.equal(g.groups.length,20);
  assert.ok(g.groups.every(head=>head.label.y<g.poolBottom && head.start.y<g.poolBottom && head.end.y<g.poolBottom));
  assert.ok(g.selectionBusY < g.height && g.record.y < g.height);
});

test("main nodes stay fixed from empty through changing and extreme candidate pools", () => {
  for (const width of [800, 1200, 1700]) {
    const base = flowLayout([], width);
    for (const input of [[question(1)], [question(8)], Array.from({length:24}, (_,i)=>({...question(i+1),key:`q-${i}`}))]) {
      const next = flowLayout(input, width);
      for (const key of ["transcript", "jevProjection", "jev", "llmProjection", "exit", "llm", "commit", "kernel", "record"]) {
        assert.deepEqual(next[key], base[key], key);
      }
      assert.equal(next.width, base.width);
      assert.ok(next.height >= base.height);
    }
  }
});

test("ordinary request fits a compact canvas; wider panels add columns before height", () => {
  const ordinary = [question(2),question(3),question(3),question(2),question(1)];
  assert.ok(flowLayout(ordinary, 1300).height <= 600);
  const manyHeads = Array.from({length:16},(_,i)=>({...question(i % 4 === 0 ? 4 : 2),key:`head-${i}`}));
  const narrow = flowLayout(manyHeads, 1200);
  const wide = flowLayout(manyHeads, 1700);
  assert.ok(wide.height < narrow.height);
  assert.equal(wide.groups.length, 16);
  assert.equal(new Set(wide.groups.map(g=>g.start.x)).size, 4);
  assert.ok(wide.height <= 850);
});
