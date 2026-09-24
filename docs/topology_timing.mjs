import assert from "node:assert/strict";

/** Sample recorded milestones at a browser-safe GIF cadence. Transient states
 * can be coalesced, but no reading pauses or minimum per-stage holds are added. */
export function replaySchedule(events, stops, { end_offset_ms, speed, gif_quantum_ms }) {
  assert.ok(Number.isFinite(speed) && speed > 0);
  assert.ok(Number.isInteger(gif_quantum_ms) && gif_quantum_ms >= 20 && gif_quantum_ms % 10 === 0);
  assert.ok(events.length && stops.length);
  const offsets = new Map();
  let previous = -Infinity;
  for (const event of events) {
    assert.ok(Number.isFinite(event.offset_ms) && event.offset_ms >= previous && event.offset_ms >= 0, "Missing or non-monotonic journal time");
    assert.ok(!offsets.has(event.seq), "Duplicate event sequence");
    offsets.set(event.seq, event.offset_ms);
    previous = event.offset_ms;
  }
  const times = stops.map(seq => {
    assert.ok(offsets.has(seq), "Missing milestone timestamp");
    return offsets.get(seq);
  });
  assert.equal(times[0], 0);
  assert.ok(times.every((t, i) => i === 0 || t >= times[i - 1]));
  assert.ok(Number.isFinite(end_offset_ms) && end_offset_ms >= previous);
  const duration_ms = Math.ceil(end_offset_ms / speed / gif_quantum_ms) * gif_quantum_ms;
  const frames = [];
  let stage = 0;
  for (let tick = 0; tick < duration_ms; tick += gif_quantum_ms) {
    while (stage + 1 < times.length && times[stage + 1] <= tick * speed) stage++;
    if (frames.at(-1)?.stage === stage) frames.at(-1).duration_ms += gif_quantum_ms;
    else frames.push({ stage, duration_ms: gif_quantum_ms });
  }
  return { duration_ms, frames: frames.map(({ stage, duration_ms }) => ({ stage, duration: duration_ms / 1000 })) };
}
