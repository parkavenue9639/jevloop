import test from "node:test";
import assert from "node:assert/strict";
import { assetUrl, imageParts, uploadImage, MAX_IMAGE_BYTES } from "../src/media.ts";
import { applyRunEvent, emptyStream, reduceEvents } from "../src/stream.ts";
import { loopView } from "../src/loopView.ts";
import { candidateView, liveDecision } from "../src/candidateView.ts";

const image = { type: "image", asset_id: "a".repeat(64), mime_type: "image/png", width: 2, height: 3, detail: "auto", name: "capture.png" };
const options = { live: true, connected: true, done: false, error: false };

test("attachments require explicit validated image lists, not nested JSON or host paths", () => {
  assert.deepEqual(imageParts([image]), [image]);
  for (const value of [JSON.stringify([image]), { images: [image] }, [{ ...image, asset_id: "../../etc/passwd" }], [{ ...image, width: 0 }]]) {
    assert.deepEqual(imageParts(value), []);
  }
  assert.equal(assetUrl(image), `/api/assets/${image.asset_id}`);
});

test("upload posts only image bytes to assets; never starts a run", async (t) => {
  const requests = [];
  t.mock.method(globalThis, "fetch", async (url, init) => {
    requests.push({ url, init });
    return new Response(JSON.stringify({ image }), { headers: { "Content-Type": "application/json" } });
  });
  const signal = new AbortController().signal;
  assert.deepEqual(await uploadImage(new File([new Uint8Array([0, 128, 255])], "capture.png", { type: "image/png" }), signal), image);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, "/api/assets");
  assert.deepEqual(JSON.parse(requests[0].init.body), { name: "capture.png", data: "AID/" });
  assert.equal(requests[0].init.signal, signal);
});

test("invalid type/size fail before network and server decoder errors remain visible", async (t) => {
  const fetch = t.mock.method(globalThis, "fetch", async () => new Response(JSON.stringify({ error: "Animated images are unsupported" }), { status: 400 }));
  for (const file of [new File(["x"], "x.svg", { type: "image/svg+xml" }), new File([], "empty.png", { type: "image/png" }), { type: "image/png", size: MAX_IMAGE_BYTES + 1 }]) {
    await assert.rejects(uploadImage(file, new AbortController().signal));
  }
  assert.equal(fetch.mock.callCount(), 0);
  await assert.rejects(uploadImage(new File(["GIF"], "anim.gif", { type: "image/gif" }), new AbortController().signal), /Animated images are unsupported/);
});

test("session change or removal cancels upload before dispatch and after a stale response", async (t) => {
  let finish;
  const fetch = t.mock.method(globalThis, "fetch", () => new Promise((resolve) => { finish = resolve; }));
  const controller = new AbortController();
  const file = new File(["png"], "capture.png", { type: "image/png" });
  controller.abort();
  await assert.rejects(uploadImage(file, controller.signal), { name: "AbortError" });
  assert.equal(fetch.mock.callCount(), 0);
  const next = new AbortController();
  const pending = uploadImage(file, next.signal);
  await new Promise((resolve) => setImmediate(resolve));
  next.abort();
  finish(new Response(JSON.stringify({ image })));
  await assert.rejects(pending, { name: "AbortError" });
});

test("legacy visual_decision has no Jev choices and retains its recorded LLM circuit in both lanes", () => {
  for (const lane of ["jev", "baseline"]) {
    const data = reduceEvents([
      { type: "attempt_started", lane, attempt_id: "visual", step: 1 },
      { type: "llm_started", lane, attempt_id: "visual", kind: "visual_decision", operation: null, reason: "image_evidence_requires_vision" },
    ]);
    const state = data.lanes[lane];
    const view = loopView(state, { ...options, baseline: lane === "baseline" });
    assert.equal(view.visual, true);
    assert.equal(view.active, "decision");
    assert.equal(view.direct, false);
    assert.deepEqual(candidateView(undefined, state.decisionFrame).submitted, []);
    const lines = liveDecision(state.decisionFrame, false);
    assert.deepEqual(lines, ["Vision LLM decision (session contains image evidence) · started"]);
    assert.doesNotMatch(lines.join(), /Jev|null|understood/);
  }
});

test("live and replay retain initial and lane-local tool images without uploads or fake Jev picks", (t) => {
  const fetch = t.mock.method(globalThis, "fetch", () => { throw new Error("Replay must not call a network mutation"); });
  const step = { decision: { operation: "VIEW_IMAGE", confidence: null, latency_ms: 50, decision_source: "visual_llm" },
    model_calls: [{ kind: "visual_decision", model: "vision" }], outcome: { status: "ok", images: [image] } };
  const events = [
    { type: "meta", params: { goal: "", images: [image], profile: "paired_shadow" }, created_at: "2026-09-26" },
    { type: "step", lane: "jev", step },
    { type: "done" },
  ];
  const live = events.reduce(applyRunEvent, emptyStream());
  const replay = reduceEvents(events);
  assert.deepEqual(replay, live);
  assert.deepEqual(imageParts(replay.params.images), [image]);
  assert.deepEqual(imageParts(replay.lanes.jev.steps[0].outcome.images), [image]);
  assert.equal(replay.lanes.baseline?.steps.length ?? 0, 0);
  const view = loopView(replay.lanes.jev, { ...options, done: true });
  assert.equal(view.visual, true);
  assert.deepEqual(view.route, ["state", "decision", "kernel", "evidence"]);
  assert.equal(candidateView(step).originalOperation, null);
  assert.deepEqual(candidateView(step).submitted, []);
  assert.equal(fetch.mock.callCount(), 0);
});
