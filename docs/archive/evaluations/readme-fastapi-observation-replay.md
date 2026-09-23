# README FastAPI six-turn replay after observation-binding refactor

> Historical experiment, archived 2026-09-23. Results describe the recorded
> runtime only. See the [evaluation index](../../evaluation/README.md) for newer runs.

Date: 2026-09-22. Branch: `feat/observation-view-bindings`.
Runtime commit: `79ba34b`; executable digest: `a6e1c6ec8d2e`.

## Exact workload and method

This replays the README case from session `3b6792da363e`, **not** the different
six-turn `backend/benchmarks/fastapi_case.json` suite and not procurement.
The six original goals and whitelisted parameters were extracted from the
original event logs only after verifying all six SHA256 hashes against
`docs/evidence/fastapi-6turn-20260922/manifest.json`.

The goals cover inventory, creating a FastAPI Hello World project, starting and
testing it, adding a query endpoint, replacing in-memory storage with JSON,
and adding a mutation endpoint. No question was rewritten or added.

The replay uses `Dashboard._execute_async`, preserving parallel lanes per turn,
new isolated lane ledgers/cache namespaces and persistent session file volumes.
The original thresholds (confidence 0.5, ambiguity 0.4), disabled progress gate,
max_steps=0, max_writes=0 and sandbox networking are preserved. An external
600-second per-turn watchdog did not fire. The sandbox image changed as expected
for the tool refactor; the original image was not silently represented as used.

Source-parameter digest: `775a58a7dd7c`.
Image: `sha256:b19bfc61a4f4d9c9f60b611655f8c8bf6a3db08d552c94e65ed39d156c468587`.
Models: `jev-latest` / `deepseek-chat`.
Local full reports, sessions and model/tool events:
`backend/artifacts/bench/readme-fastapi-observations-20260922/`.

## Results

| Metric | Jev + LLM | Plain LLM |
|---|---:|---:|
| Completed with an answer | 6/6 | 6/6 |
| Runtime attempts/steps | 27 | 55 |
| Sum of lane elapsed time | 179.644 s | 210.440 s |
| Estimated cost | $0.066160 | $0.195624 |
| Jev model calls | 27 | 0 |
| LLM model calls | 27 | 55 |
| Total model calls | 54 | 55 |
| Successful direct tool calls without LLM | 0 | 0 |
| Parameter authoring calls | 3 | 0 |
| Content authoring calls | 3 | 0 |
| Arbitration calls | 21 | 0 |
| LLM input tokens | 453,345 | 2,126,989 |
| LLM output tokens | 13,211 | 30,266 |

Within this run Jev used **50.9% fewer runtime steps**, **14.6% less lane elapsed
time**, and **66.2% less estimated cost**. This did not come from skipping the
LLM: every Jev attempt used an LLM call. Total model calls were almost equal.
Twenty arbitrations were triggered by low confidence and one by a recoverable
observation; five picks were upheld and sixteen overridden.

| Turn | Jev steps | Plain steps | Jev seconds | Plain seconds |
|---|---:|---:|---:|---:|
| Inventory | 2 | 2 | 4.325 | 1.519 |
| Create Hello World | 6 | 9 | 26.049 | 20.222 |
| Start and test service | 2 | 5 | 13.557 | 14.944 |
| Add query endpoint | 6 | 8 | 33.805 | 25.662 |
| JSON persistence | 7 | 20 | 47.188 | 98.636 |
| Add mutation endpoint | 4 | 11 | 54.720 | 49.457 |

All failure/recovery overhead is retained. Jev had one ANSWER parameter refusal
and one BASH exit-127 outcome classified EFFECT_UNKNOWN; the plain lane had one
invalid proposal. Both lanes eventually terminated as completed with answers.
That does not establish that every intermediate effect was successfully applied.

## Comparison with the historical README result

| Jev metric | Historical README | This replay |
|---|---:|---:|
| Steps | 22 | 27 |
| Lane elapsed | 103.5 s | 179.6 s |
| Estimated cost | $0.040872 | $0.066160 |
| Direct attempts | 2/22 | 0/27 |

The new implementation still beats the concurrent plain lane on this sample,
but **does not demonstrate an improvement over the historical Jev result**.
The old/new comparison is not an interleaved controlled experiment: service
latency, mutable model aliases, generated code and paths, cache behavior, tool
schemas, and sandbox implementation can vary. Do not attribute the entire
difference to one architectural change.

As in the README, "6/6" means completed and answered, **not** independent semantic
or endpoint-equivalence grading. Costs use configured runtime prices, not billing
receipts. The generic binding implementation is covered by tests, including a
zero-LLM observed read, but its direct-execution benefit was not realized here.
The next investigation should focus on the high arbitration rate and explicit
current-turn evidence coverage, without user-text matching or benchmark tuning.

Replay command (choose a fresh output directory):

```sh
cd backend
PYTHONPATH=. .venv/bin/python scripts/replay_readme_fastapi.py \
  --out artifacts/bench/readme-fastapi-observations-new-run
```

Validation: 268 backend tests passed; Ruff passed for runtime, tests and replay
script. Historical raw logs, README images and public evidence were not modified.
