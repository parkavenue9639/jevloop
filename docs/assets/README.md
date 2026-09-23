# README visuals

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
