import test from "node:test";
import assert from "node:assert/strict";
import { emptyPlayback, playbackReducer, replayTimeline, replayDelay } from "../src/playback.ts";
import { reduceEvents } from "../src/stream.ts";

const recording = (runId, events) => ({ runId, events: events.map((event, index) => ({ seq: index + 1, event })) });
const a = recording("turn-a", [
  { type: "meta", params: { goal: "inspect", profile: "paired_shadow" } },
  { type: "attempt_started", lane: "jev", attempt_id: "j", step: 1 },
  { type: "attempt_started", lane: "baseline", attempt_id: "b", step: 1 },
  { type: "jev_request", lane: "jev", attempt_id: "j", questions: { phase: { type: "choice", criteria: { ACT: "Act" } } } },
  { type: "llm_started", lane: "baseline", attempt_id: "b", kind: "plain", operation: "READ_FILE" },
  { type: "jev_response", lane: "jev", attempt_id: "j", response: { operation: "ANSWER" } },
  { type: "awaiting_continue", lane: "jev" },
  { type: "step", lane: "baseline", step: { decision: { operation: "ANSWER" } } },
  { type: "final", lane: "baseline", final: { answer: "baseline done" }, metrics: null },
  { type: "step", lane: "jev", step: { decision: { operation: "ANSWER" } } },
  { type: "final", lane: "jev", final: { answer: "jev done" }, metrics: null },
  { type: "done" },
]);
const b = recording("turn-b", [
  { type: "meta", params: { goal: "follow-up" } },
  { type: "error", message: "saved failure", lane: "jev" },
  { type: "done" },
]);
const start = () => playbackReducer(emptyPlayback(), { type: "start", timeline: replayTimeline([a, b]) });
const tick = (state) => playbackReducer(state, { type: "tick", generation: state.generation, position: state.position });

test("one cursor replays interleaved paired lanes, with no future turn or answer leakage", () => {
  let state = start();
  assert.deepEqual(state.turns, []);
  for (let index = 0; index < 4; index++) state = tick(state);
  assert.deepEqual(state.turns, ["turn-a"]);
  assert.equal(state.activeRunId, "turn-a");
  assert.equal(state.streams["turn-b"], undefined);
  assert.ok(state.streams["turn-a"].lanes.jev.decisionFrame.questions);
  assert.equal(state.streams["turn-a"].lanes.jev.decisionFrame.response, undefined);
  assert.equal(state.streams["turn-a"].lanes.baseline.decisionFrame.attemptId, "b");
  assert.equal(state.streams["turn-a"].lanes.jev.answer, null);
  assert.equal(state.streams["turn-a"].connection, undefined);
  state = tick(tick(tick(state)));
  assert.equal(state.streams["turn-a"].lanes.jev.awaiting, true);
  assert.equal(state.status, "playing"); // recorded pause does not wait for backend control
  while (state.status === "playing") state = tick(state);
  assert.equal(state.status, "ended");
  assert.equal(state.position, a.events.length + b.events.length);
  assert.deepEqual(state.turns, ["turn-a", "turn-b"]);
  for (const run of [a, b]) {
    assert.deepEqual(state.streams[run.runId], reduceEvents(run.events.map(({ event }) => event)));
  }
});

test("pause, seek, speed, restart and exit reject stale clock callbacks", () => {
  let state = tick(start());
  const stale = { type: "tick", generation: state.generation, position: state.position };
  state = playbackReducer(state, { type: "pause" });
  assert.equal(state.status, "paused");
  assert.equal(playbackReducer(state, stale), state);
  state = playbackReducer(state, { type: "play" });
  assert.equal(playbackReducer(state, stale), state);
  state = tick(state);
  const position = state.position;
  state = playbackReducer(state, { type: "speed", speed: 4 });
  assert.equal(state.position, position);
  assert.equal(state.speed, 4);
  state = playbackReducer(state, { type: "seek", position: 6 });
  assert.equal(state.status, "paused");
  assert.deepEqual(state.streams["turn-a"], reduceEvents(a.events.slice(0, 6).map(({ event }) => event)));
  state = playbackReducer(state, { type: "seek", position: 0 });
  assert.deepEqual(state.streams, {});
  assert.equal(state.activeRunId, null);
  state = playbackReducer(state, { type: "seek", position: 9999 });
  assert.equal(state.status, "ended");
  state = playbackReducer(state, { type: "play" });
  assert.equal(state.position, 0);
  state = playbackReducer(tick(state), { type: "restart" });
  assert.equal(state.position, 0);
  assert.equal(state.status, "playing");
  state = playbackReducer(state, { type: "exit" });
  assert.equal(state.status, "idle");
  assert.deepEqual(state.streams, {});
  assert.deepEqual(state.timeline.frames, []);
  assert.equal(playbackReducer(state, stale), state);
});

test("seek backward removes later steps, answers, errors and whole turns", () => {
  let state = playbackReducer(start(), { type: "seek", position: 999 });
  const finished = state.streams["turn-a"];
  state = playbackReducer(state, { type: "seek", position: 4 });
  assert.equal(state.streams["turn-b"], undefined);
  assert.deepEqual(state.streams["turn-a"].lanes.jev.steps, []);
  assert.equal(state.streams["turn-a"].lanes.jev.answer, null);
  assert.equal(state.streams["turn-a"].done, false);
  assert.equal(finished.lanes.jev.answer, "jev done"); // snapshots never mutated
});

test("recordings sort by server seq, require complete events and never synthesize telemetry", () => {
  const shuffled = { ...a, events: [...a.events].reverse() };
  assert.deepEqual(replayTimeline([shuffled]).frames, replayTimeline([a]).frames);
  assert.equal(replayTimeline([a]).partialTelemetry, false);
  const legacy = recording("old", [{ type: "meta", params: {} }, { type: "step", step: { decision: { operation: "ANSWER" } } }, { type: "done" }]);
  const timeline = replayTimeline([legacy]);
  assert.equal(timeline.partialTelemetry, true);
  assert.deepEqual(timeline.frames.map((f) => f.event.type), ["meta", "step", "done"]);
  assert.throws(() => replayTimeline([{ ...a, events: a.events.slice(0, -1) }]), /completed/);
  assert.throws(() => replayTimeline([{ ...a, events: a.events.slice(1) }]), /sequence/);
  assert.throws(() => replayTimeline([{ ...a, events: [a.events[0], ...a.events] }]), /sequence/);
  assert.throws(() => replayTimeline([a, a]), /Duplicate/);
});

test("invalid positions or speeds cannot poison the cursor; pacing is synthetic and bounded", () => {
  const state = start();
  assert.equal(playbackReducer(state, { type: "seek", position: NaN }), state);
  assert.equal(playbackReducer(state, { type: "speed", speed: 0 }), state);
  for (const { event } of a.events) {
    assert.ok(replayDelay(event, 4) > 0);
    assert.equal(replayDelay(event, 2) * 2, replayDelay(event, 1));
  }
});
