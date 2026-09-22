# README visuals

## Paired execution screenshot

`agent-loop-paired-run.png` is a browser capture of a **compact historical
replay**, not a new live run or a claim that every task is faster.

- Source: session `3b6792da363e`, turn 6, run `ed280f843333`, 2026-09-22.
- User request: “帮我新增一个改数据的接口” (add an endpoint to update data).
- The card headers and all 19 step rows were exported from the local frontend's
  replay of that turn, using its actual rendered markup and compiled styles.
- A documentation-only layout places those rows side by side, hides the long
  command/answer bodies and confidence detail, and adds the turn totals.
  The running application and original history were not modified.
- JevLoop: 26,421 ms, 5 decision steps; baseline: 64,059 ms, 14 decision steps.
  The screenshot rounds these to one decimal second, like the frontend.
- All steps are retained in their original per-lane order, including JevLoop's
  denied third step, LLM-review badges, and model-call latency labels.
- Rows align by step index, **not by a shared time axis**. The small row latency
  labels refer to model calls, not complete step duration.
- All five JevLoop steps were LLM-assisted in the recorded routing metrics.
  Fewer runtime steps must not be read as five LLM-free steps.
- Only task-related filenames, step labels, model timings, and this request are
  shown. Prompts, full tool outputs, credentials, and unrelated sessions are not
  included. The two lane answers were not independently scored for equivalence.

The [public trace](../evidence/fastapi-6turn-20260922/trace.jsonl) preserves
operations, ordering, timings, denial and routing metrics; content is redacted.
See the [case study](../fastapi-case-study.md) for methodology and full metrics.

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

The summary panels cover **all six turns**; the execution screenshot shows only
**turn 6**. Their totals intentionally differ. Updating history data does not
automatically refresh a committed image: regenerate it and submit a PR.
