# Evaluation guide

This index separates workloads, published evidence and revision-specific runs.
"Latest" below means latest **recorded experiment as of 2026-09-23**, not a
continuous benchmark or a claim about every subsequent revision.

## Which workload?

| Workload | Definition / entry point | What its checks establish |
|---|---|---|
| Published README FastAPI session | [Case study](fastapi-case-study.md), [public manifest](../evidence/fastapi-6turn-20260922/manifest.json); replay: `backend/scripts/replay_readme_fastapi.py` | Six original user requests; native replay checks mean answered, not semantic equivalence |
| Derived FastAPI families | [Workload contract](fastapi-task-families.md); runner: `backend/scripts/run_fastapi_families.py` | Observation reuse 6, generation 6, mixed 8 turns per lane; fixed hard probes, not exhaustive prose verification |
| General repository bench | `backend/benchmarks/`, `make bench` | The selected suite's own checks; `fastapi_case.json` is not the original README session |

The README replay verifies original source-journal hashes and requires the
corresponding Git-ignored local logs. The public redacted bundle alone is enough
to verify published metrics and regenerate visuals, not to recreate those raw
requests. Do not replace missing originals with invented inputs and call it the
same replay.

## Latest recorded branch result

[Independent-projection replay](fastapi-projection-results.md) — source `937e002`,
runtime `03a2e8bfb9d6`, run on 2026-09-22. Covers both the three derived families
and original README six turns. Includes historical comparison, token/cache/cost
accounting, connection failures, trace-quality limitations and cleanup receipts.

This is a branch measurement. It does not replace the published README sample
or silently update its SVGs. Raw prompts, tool outputs and generated workspaces
remain local under `backend/artifacts/bench/` and are not public evidence.

## Earlier experiments

All entries below are historical, retained without rewriting their metrics.

| Stage | Report | Scope |
|---|---|---|
| Initial observation binding | [Supplemental evaluation](../archive/evaluations/observation-view-evaluation.md) | Procurement 10-turn; not the requested FastAPI baseline |
| Initial observation binding | [README replay](../archive/evaluations/readme-fastapi-observation-replay.md) | Original README six-turn, runtime `a6e1c6ec8d2e` |
| Jev prompt alignment | [Prompt experiment](../archive/evaluations/jev-prompt-contract-experiment.md) | Original README six-turn, runtime `cdf87e8f5b6b` |
| First derived-family run | [Family results](../archive/evaluations/fastapi-task-families-results.md) | Separate 6/6/8-turn workloads, runtime `cdf87e8f5b6b` |
| Stable LLM schemas | [Family results](../archive/evaluations/fastapi-cache-optimization-results.md) | Three families, runtime `642c5ed3bd9a` |
| Stable LLM schemas | [README replay](../archive/evaluations/readme-fastapi-cache-replay.md) | Original six-turn, runtime `642c5ed3bd9a` |

## Reading and adding results

- Freeze inputs, source/runtime identity, image, model aliases, parameters,
  schedule and scoring before execution. Alias names are not immutable provider
  model-version identities.
- Distinguish answered turns, hard functional checks and semantic trace review.
  Do not equate a lower step count with equivalent completed work.
- Separate successful direct effects from direct attempts; record all failures
  and unknown usage. Costs are estimates unless actual billing is available.
- Separate model latency, failed-request intervals and remaining agent runtime.
  Removing failure intervals does not reconstruct a failure-free trajectory.
- Historical runs with different revisions/trajectories are not controlled,
  repeated causal A/B tests. State limits rather than replacing old evidence.
- Keep published redacted evidence at stable paths. New public bundles require
  a sensitivity review; private local reports are not automatically publishable.

For offline public-evidence verification and vector regeneration, follow
[visuals and checks](../assets/README.md). For runtime development tests, use the
repository [development guide](../../README.md#development).
