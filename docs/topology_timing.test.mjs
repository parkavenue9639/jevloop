import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { replaySchedule } from "./topology_timing.mjs";

const fixture = JSON.parse(readFileSync(new URL("./assets/topology-example.json", import.meta.url), "utf8"));
const stops = [3, 4, 5, 7, 9, 12, 15, 16, 18, 23];
test("3x follows journal time within one GIF frame, without extra reading holds", () => {
  const schedule = replaySchedule(fixture.events, stops, fixture.source.timing);
  const exact = fixture.source.timing.end_offset_ms / 3;
  assert.ok(schedule.duration_ms >= exact && schedule.duration_ms - exact < 20);
  assert.equal(schedule.duration_ms, 1440);
  assert.ok(Math.abs(schedule.frames.reduce((n, frame) => n + frame.duration * 1000, 0) - schedule.duration_ms) < .001);
  assert.ok(schedule.frames.every(frame => frame.duration >= .02));
  assert.equal(schedule.frames.at(-1).stage, 9);
  let elapsed = 0;
  for (const frame of schedule.frames) {
    const originalTime = fixture.events.find(e => e.seq === stops[frame.stage]).offset_ms;
    assert.ok(elapsed >= originalTime / 3 - .0001, "State appeared before its event");
    assert.ok(elapsed - originalTime / 3 < 20.001, "State was artificially delayed");
    elapsed += frame.duration * 1000;
  }
  assert.ok(schedule.frames.some(frame => frame.stage === 1));
  assert.ok(schedule.frames.some(frame => frame.stage === 6));
});
test("missing, backwards, or invalid timing fails closed", () => {
  assert.throws(() => replaySchedule([{ seq: 3 }], [3], fixture.source.timing));
  assert.throws(() => replaySchedule([{ seq: 3, offset_ms: 1 }, { seq: 4, offset_ms: 0 }], [3, 4], fixture.source.timing));
  assert.throws(() => replaySchedule(fixture.events, stops, { ...fixture.source.timing, speed: 0 }));
  assert.throws(() => replaySchedule(fixture.events, stops, { ...fixture.source.timing, gif_quantum_ms: 10 }));
});
