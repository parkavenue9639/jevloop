# README visuals

## Decision topology replay

`decision-topology-{en,zh-CN}.gif` replays two consecutive real loop iterations
at **3× the original event-journal timing**: 4.292 seconds become approximately
1.44 seconds. Ten milestone snapshots are sampled at 20 ms GIF boundaries;
very brief intermediate states may share a frame. There are no artificial
reading holds or candidate-entry delays. The corresponding `.svg` is its sharp, static final
frame: use it to inspect small labels or avoid autoplay. GIF is used for README
motion because [GitHub does not guarantee SVG animation](https://docs.github.com/en/repositories/working-with-files/using-files/working-with-non-code-files).
No JavaScript, iframe, external hosting or live service is needed to view it.

- Source: run `43418c7bbefb`, original event sequence **3–23**. The checked-in
  [public fixture](topology-example.json) retains twenty events in that range;
  only the metrics event at seq 13 is omitted. Relative `offset_ms` values were
  recovered from the original on-disk JSONL journal's `ts` fields (the HTTP
  replay API drops those timestamps). Timing ends at the original `done` event,
  seq 28, so the final state retains its actual post-result interval. Absolute
  timestamps and unrelated runtime data are not included.
- These are **two iterations within one create-FastAPI task**, not separate user
  turns or separately spliced runs: ACT → WRITE_FILE, then RESPOND → ANSWER.
  Both use LLM authoring; neither demonstrates LLM-free execution. The final
  answer does not independently establish task correctness or a benchmark result.
- Loop 1 has **8 choice heads** and only `.gitkeep` as a READ_FILE candidate.
  After the recorded `write_file(main.py, 389 chars)` result, loop 2 has **14
  choice heads**: `main.py` is newly offered, and the two READ_FILE branches each
  gain one `one/many` head and two `include/skip` heads. These six new questions
  describe batch bindings, not six tool calls. Green outlines identify the new
  file candidates and heads; they never imply Jev selected or executed them.
- Original candidate keys, consumed-head probabilities, FIX selections and event
  order are preserved. Criteria descriptions, prompts, LLM response bodies, answer
  bodies, full tool outputs, hashes and original attempt/intent IDs are excluded.
  The short write-result label is retained to connect the two candidate pools.
  Descriptions used only to identify batch members retain the displayed filenames.
- Both submitted choice pools are shown in full. Additional **FIX** groups explain
  deterministic bindings after selection: one for WRITE_FILE parameters, two for
  ANSWER and its parameters. These are not extra model questions. Auxiliary ambiguity/progress questions are
  omitted, just as in the frontend candidate diagram.
- Read **purpose → tool → arguments**. `Only if` labels define ownership. A dim
  branch is unconsumed; `—` means no displayed probability, not zero. Batch
  `one/many` and member `include/skip` are parameter decisions, not extra tools.
- The diagram is exported from the actual `DecisionFlow` React component through
  server rendering, using the same warm paper background, charcoal text and
  terracotta accent as the other README vectors. Light-mode blue/green lane
  colors remain distinct; the interactive console's theme is unchanged. The export header,
  fixed canvas reserve and portable CSS conversion are documentation-specific.
  Main-node coordinates are checked across every frame. There is no flash effect
  or inferred intermediate stage.
- The GIF contains sampled event-stage frames, not a screen recording of continuous
  line animations. The next request changes the actual candidate pool and emphasizes
  only newly offered choices. No per-head construction events are invented. Each
  frame represents the latest milestone at that recorded time divided by three;
  boundary rounding is less than 20 ms. Both languages use the same schedule.

Regenerate the SVGs and validate the fixture with installed frontend dependencies:

```sh
node docs/render_topology.mjs
node docs/render_topology.mjs --check
node --test docs/topology_timing.test.mjs
python3 -m unittest discover -s docs -p 'test_topology_assets.py'
```

To also rebuild the GIFs, install the optional rasterizer in a temporary directory
(not the application dependency tree). `ffmpeg` and `ffprobe` must already be on PATH:

```sh
topology_tmp=$(mktemp -d)
npm install --prefix "$topology_tmp/raster" --no-save --package-lock=false --ignore-scripts @resvg/resvg-js@2.6.2
node docs/render_topology.mjs --frames "$topology_tmp/frames"
node docs/raster_topology.mjs "$topology_tmp/frames" "$topology_tmp/raster/node_modules/@resvg/resvg-js"
```

SVG generation is deterministic; GIF rasterization uses local system fonts and
may vary with OS/font/ffmpeg versions. Review both localized renders before
committing. These commands access only the public fixture, not a local run server.

## Paired execution vectors

`agent-loop-paired-en.svg` and `agent-loop-paired-zh-CN.svg` are **native vector
visualizations of historical execution**, not original screenshots or new live
runs. Text, cards, and labels are SVG elements, not embedded bitmaps or HTML.

- Source: session `3b6792da363e`, turn 6, run `ed280f843333`, 2026-09-22.
- User request: “帮我新增一个改数据的接口” (add an endpoint to update data).
- All 19 step rows, decision-call latencies, review/denial flags, and lane totals
  come directly from the public trace. The frontend supplies the color palette;
  no private logs, running backend, or browser session are needed to regenerate.
- A documentation-only layout places the steps side by side. Long command/answer
  bodies, file targets, and confidence details are omitted, not entire steps.
  The user request above was already published with the original screenshot;
  the English version is a translation. It is the only manually supplied task text.
- JevLoop: 26,421 ms, 5 decision steps; baseline: 64,059 ms, 14 decision steps.
  The vectors round these to one decimal second, like the frontend.
- All steps are retained in their original per-lane order, including JevLoop's
  denied third step, LLM-review badges, and model-call latency labels.
- Rows align by step index, **not by a shared time axis**. The small row latency
  labels refer to model calls, not complete step duration.
- All five JevLoop steps were LLM-assisted in the recorded routing metrics.
  Fewer runtime steps must not be read as five LLM-free steps.
- Only step labels, model timings, review/denial flags, and this request are
  shown. Prompts, full tool outputs, credentials, and unrelated sessions are not
  included. The two lane answers were not independently scored for equivalence.

The [public trace](../evidence/fastapi-6turn-20260922/trace.jsonl) preserves
operations, ordering, timings, denial and routing metrics; content is redacted.
See the [case study](../evaluation/fastapi-case-study.md) for methodology and full metrics.

Regenerate and check without third-party dependencies:

```sh
python3 docs/render_paired_loop.py
python3 docs/render_paired_loop.py --check
python3 -m unittest discover -s docs -p 'test_render_paired_loop.py'
```

The generator rejects missing final metrics and inconsistent step or denial
counts. Both localized assets are deterministic. The superseded PNG is retained
in Git history, not shipped alongside the vectors.

## Summary panels

`fastapi-session-en.svg` and `fastapi-session-zh-CN.svg` are deterministic,
GitHub-safe SVGs generated from the public six-turn `summary.json`. Their palette
is read from `frontend/src/styles.css`. They contain no scripts, external assets,
embedded HTML, or private runtime data.

From the repository root:

```sh
python3 docs/evidence/fastapi-6turn-20260922/verify.py
python3 docs/render_readme_panel.py
python3 docs/render_readme_panel.py --check
```

The summary panels cover **all six turns**; the paired execution vectors show only
**turn 6**. Their totals intentionally differ. Updating history data does not
automatically refresh a committed image: regenerate it and submit a PR.
