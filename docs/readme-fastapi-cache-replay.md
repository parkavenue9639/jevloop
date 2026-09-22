# README six-turn replay after cache optimization

## Frozen protocol

Requested replay of the original README six user messages, not the derived
FastAPI families or `backend/benchmarks/fastapi_case.json`.

- Runtime implementation `4e75eb7`, checkout `c57706a`, executable digest
  `642c5ed3bd9a`. No runtime, prompt or threshold changes during this experiment.
- All six historical source-file SHA256 checks passed. Whitelisted original
  parameters have unchanged digest `775a58a7dd7c`.
- Original dashboard execution: parallel lanes per turn, separate fresh session
  ledgers/cache namespaces and persistent session volumes; containers recreated
  per turn. Preserve max_steps=0, max_writes=0, sandbox networking, min confidence
  .6, escalation .5, ambiguity .4, disabled progress floor, no step pause.
- External watchdog: 600 seconds per turn. Retain failed turns; no selective
  retries, manual fixes, injected grading hints or runtime tuning.
- Same configured model aliases `jev-latest` / `deepseek-chat`; actual immutable
  provider model versions are not established by these aliases.
- Output: `backend/artifacts/bench/readme-fastapi-cache-20260922-224912/`.
- Compare with the main-published historical README result and branch prompt
  replay `a22f61c`. These are historical, not contemporaneous/interleaved main
  measurements. Sandbox changes are part of the branch refactor.
- Native pass means completed with an answer, not endpoint or semantic grading.
  Review tool/answer evidence separately and retain limitations.

## Results

Runner exited 0; both lanes answered all six turns. This is **completion-only
12/12, not functional 12/12**. The trace review below identifies omitted or
deferred work. The current Jev lane does not outperform the main-published
historical result on steps, estimated cost or elapsed time.

| Jev metric | Main published | Before cache fix (`a22f61c`) | Cache optimized |
|---|---:|---:|---:|
| Steps | 22 | 21 | 26 |
| LLM calls | 20 | 20 | 25 |
| Successful direct tools | 2 | 1 | 1 |
| Estimated total USD | 0.040872 | 0.056673 | 0.061296 |
| Agent seconds | 103.546 | 126.005 | 118.406 |
| LLM input tokens | 247,356 | 315,107 | 467,012 |
| LLM output tokens | 9,471 | 10,821 | 13,026 |
| Cache-hit input tokens | 200,576 | 235,904 | 437,248 |
| Cache-miss input tokens | 46,780 | 79,203 | 29,764 |
| Weighted input cache-hit share | 81.1% | 74.9% | 93.6% |
| Tool-schema variants / switches | 3 / 12 | 5 / 12 | 1 / 0 |

Against published main, current cost is **50.0% higher**, agent time **14.4%
higher**, and input tokens **88.8% higher**, despite **36.4% fewer cache-miss
tokens**. Against the previous branch replay, misses fell 62.4%, but cost rose
8.2% and time fell 6.0%. These single historical comparisons have different
trajectories and are not causal estimates of the cache change alone.

Current Jev LLM calls comprise 6 parameter-authoring, 5 content-authoring and
14 arbitration calls; Jev was called 26 times. Its one successful direct tool
was the root directory listing. All 25 LLM requests used one schema and all
24 successive request transitions retained the previous message prefix.
There are no unknown cache tokens in these receipts. A stable schema hash and
append-only messages support the intended request contract, not a claim about
the provider's internal cache key.

### Concurrent lanes

| Turn | Jev / plain steps | Jev / plain seconds | Jev / plain estimated USD |
|---|---:|---:|---:|
| 1: list workspace | 2 / 2 | 2.400 / 1.548 | 0.000935 / 0.000819 |
| 2: create FastAPI hello service | 6 / 12 | 18.900 / 23.311 | 0.006335 / 0.011587 |
| 3: start and test service | 1 / 4 | 3.376 / 13.499 | 0.001683 / 0.006779 |
| 4: add query endpoint | 1 / 9 | 4.918 / 28.649 | 0.001718 / 0.022181 |
| 5: persist data in JSON | 7 / 13 | 41.167 / 48.199 | 0.017860 / 0.049065 |
| 6: add update endpoint | 9 / 12 | 47.645 / 51.114 | 0.032765 / 0.064775 |
| Total | 26 / 52 | 118.406 / 166.320 | 0.061296 / 0.155206 |

Nominally Jev used 60.5% less estimated cost and 28.8% less agent time than
concurrent plain. **Do not interpret this as an equal-work win:** Jev did not
execute the turn-3 request and deferred turn-4 implementation. Plain executed
both. The current plain cache-hit share was 96.8%, with 52 LLM calls.

### Latency attribution

These are summed agent elapsed times, not whole-experiment wall time. The
remainder includes tools, persistence, serialization and orchestration; it is
not a direct measurement of tool time alone.

| Jev lane | Jev model seconds | LLM seconds | Other seconds | Total seconds |
|---|---:|---:|---:|---:|
| Main published | 23.908 | 41.915 | 37.723 | 103.546 |
| Before cache fix | 30.287 | 46.746 | 48.972 | 126.005 |
| Cache optimized | 21.248 | 55.166 | 41.992 | 118.406 |

Current plain: 101.418 seconds LLM plus 64.902 seconds other runtime. Current
Jev model latency is lower in aggregate than either historical Jev run; this
sample's overall regression versus main cannot be assigned to increased Jev
service time. More LLM calls, input and output tokens accompany the higher
LLM cost and aggregate latency. No current model-unavailable events occurred.
Costs use the configured estimator, not billing receipts.

## Semantic trace review

Independent read-only review and coordinator spot checks used the saved journals;
no post-hoc model calls, endpoint probes or corrections were added.

- **Turn 2:** Jev started uvicorn and obtained actual HTTP 200 responses for
  the hello service. Its attempted `pkill` cleanup failed because the command
  was absent; a later shell command masked that failure in the exit status.
  The answer disclosed the failed stop.
- **Turn 3:** Jev only answered, citing genuine earlier turn-2 tests. It did
  not start or test the service in this turn. Its suggestion that the earlier
  background process might remain running overlooked the per-turn container
  lifecycle; only the workspace volume is designed to persist across turns.
- **Turn 4:** Jev asked the user to choose query details, without claiming it
  had implemented them. No read, write or shell action occurred. This is a
  clarification response, not completed query-endpoint implementation.
- **Turn 5:** Jev implemented query routes and JSON persistence, including the
  deferred turn-4 work. A new process read the persisted record successfully.
  However, the answer's ordered stop-then-restart narrative is unsupported:
  `ps` was missing, and both old and new processes were stopped only later.
  Evidence supports cross-process persistence, not that claimed stop sequence.
  Plain's trace contains an actual stop, failed connection and restart/readback.
- **Turn 6:** Both lanes exercised update endpoints and read back persisted
  changes after starting another process. Jev covered PATCH/PUT and 404/422
  responses. Static review found an unprobed edge: its PATCH implementation
  permits explicit `name: null` / `price: null`, despite the answer's stated
  nonempty/nonnegative constraints. This was not a comprehensive API test.
- Jev has zero typed error observations, but that does not mean every shell
  subcommand succeeded. Plain has one `EFFECT_UNKNOWN` from a wrong-directory
  disk inspection in turn 6, followed by successful recovery and verification.

Thus the final artifacts include query, persistence and tested update paths,
but six answered turns do not establish six fulfilled execution requests.

## Reproduction and evidence

Run from `backend/`:

```sh
PYTHONPATH=. .venv/bin/python scripts/replay_readme_fastapi.py \
  --out artifacts/bench/readme-fastapi-cache-20260922-224912
```

The output directory contains native `report.json`, `report.md`, the six
`runs/*.jsonl` journals, and post-hoc `analyze_replay.py` / `comparison.json`.
These local artifacts are Git-ignored; no raw prompts or generated workspaces
are included in this document. Original source identities and hashes are in
`docs/evidence/fastapi-6turn-20260922/manifest.json`.

| Turn | Current journal ID | Original journal ID |
|---|---|---|
| 1 | `803925a071b8` | `6b8d05c853eb` |
| 2 | `c2401ce7ae0f` | `4fd790278ced` |
| 3 | `38214b75716d` | `580d508120ef` |
| 4 | `792245fa70fc` | `2fdbe673cb88` |
| 5 | `0fc01bc2840b` | `c50dafb5b94e` |
| 6 | `5f16bbc48299` | `ed280f843333` |

Useful journal lines: turn 2 line 79; turn 3 lines 12/16; turn 4 lines 23/27;
turn 5 lines 58/79/89/118; turn 6 lines 51/68/82/103 (Jev) and 86/96/107
(plain). Historical raw arbitration receipts count 13 main calls; do not
substitute the separate historical escalation counter for call accounting.

Session `readme-fastapi-253bf9f49bb3` ran 2026-09-22 22:49:31–22:52:23 +08:00.
Current sandbox image:
`sha256:b19bfc61a4f4d9c9f60b611655f8c8bf6a3db08d552c94e65ed39d156c468587`.
Main's published image was different:
`sha256:8ff3aa521320403b2415bbf207ffd4bda94a5fa7f89460af4394977e7bee30a3`.
This is not a same-image, contemporaneous main A/B test. Runner cleanup finished;
both session volumes and session containers were verified absent afterward.
The shared immutable image was retained. Runtime sources were not changed.
