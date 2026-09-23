# FastAPI families after stable LLM schemas

> Historical experiment, archived 2026-09-23. Results describe the recorded
> runtime only. See the [evaluation index](../../evaluation/README.md) for newer runs.

## Frozen rerun protocol

Requested rerun of all three existing task families, on commit `4e75eb7`,
2026-09-22. No runtime, prompt, task, scoring or configuration tuning during
execution. Compare with `fastapi-task-families-results.md`, preserving failures.

- Output: `backend/artifacts/bench/fastapi-families-cache-20260922-223416/`.
- Same `fastapi-task-families-v1` workload: observation reuse 6, generation 6,
  mixed 8 turns per lane. Identical seeds, external updates and strict probes.
- Unmodified runner `backend/scripts/run_fastapi_families.py`: sequential paired
  lanes (Jev first), fresh isolated family/lane volumes and cache namespaces,
  persistent within each family, 20 steps / 600 seconds per lane-turn.
- Same thresholds: escalation .5, ambiguity .4, write confidence .6, no progress
  floor. Same model aliases and sandbox image; record actual metadata in report.
- Report task quality, actual successful direct effects, model calls, cache
  hit/miss tokens, estimated costs, and Jev/LLM latency separately. Transport
  failures without usage receipts remain unknown cost, not free calls.
- This rerun changes both candidate/full-generation semantics and LLM request
  schemas; it is not a schema-only causal experiment. Plain LLM also receives
  the new stable catalog and allowed-operation tail notes. A single pair is not
  a general performance guarantee or proof of provider cache internals.

## Results

All **40/40 lane-turns completed and passed the frozen hard checks**, versus
38/40 in the previous run. Runner exit 0. This is not a claim of perfect answer
semantics: independent trace review found the limitations below.

| Family | Steps Jev / plain | LLM calls Jev / plain | Successful direct tools | Estimated USD Jev / plain | Jev cost vs plain |
|---|---:|---:|---:|---:|---:|
| Observation reuse, 6 turns | 12 / 13 | 11 / 13 | 1 | 0.013581 / 0.012620 | +7.6% |
| Generation, 6 turns | 22 / 26 | 21 / 26 | 1 | 0.038928 / 0.044404 | -12.3% |
| Mixed, 8 turns | 24 / 36 | 24 / 36 | 0 | 0.052037 / 0.074188 | -29.9% |
| Total | 58 / 75 | 56 / 75 | 2 | 0.104546 / 0.131212 | -20.3% |

Costs include Jev calls and use the unchanged configured estimator, not billing
receipts. The two direct successes were root LIST_FILES defaults. Mixed still
had zero successful direct tool calls, despite fewer LLM calls and lower cost.
Native direct-attempt counters are not used as successful-bypass evidence.

## Cache evidence

The weighted input hit rate improved in every Jev family. All current LLM
requests, including arbitration and plain, used the same canonical schema hash
`200c4930788a` (SHA256 of sorted-key JSON, truncated for diagnostics). Each lane
retained its own isolated cache scope. This hash is not a provider cache key.

| Family | Jev old -> new hit share | Current plain hit share | Jev old -> new cache-miss tokens | Schema variants old -> new |
|---|---:|---:|---:|---:|
| Observation reuse | 65.9% -> 87.4% | 88.2% | 25,711 -> 10,089 | 3 -> 1 |
| Generation | 63.7% -> 91.7% | 93.2% | 89,206 -> 23,135 | 5 -> 1 |
| Mixed | 67.8% -> 92.9% | 94.9% | 146,091 -> 29,061 | 6 -> 1 |

Across all three Jev families, **LLM calls stayed at 56**, input tokens changed
only from **775,184 to 767,565**, but cache-miss tokens fell from **261,008 to
62,285 (-76.1%)**. Weighted hit share rose from **66.3% to 91.9%**. Jev total
estimated cost fell from 0.147680 to 0.104546. Different trajectories, outputs
and infrastructure conditions prevent assigning all cost savings to schemas,
but the observed gain is not merely from reducing the number of LLM calls.

Old Jev schema switches were 6 / 14 / 18; current switches are 0 / 0 / 0.
Existing `required` vs `auto` modes remain. For example, observation T5's
recovery arbitration (the family's first `auto` call) hit 9,216 of 9,382 input
tokens, despite preceding parameter requests using `required`. This run does
not show a large cold reset from that mode transition; it does not establish
that all providers treat these modes identically.

Messages remain append-only on successful paths. Full prior-message prefixes
were retained in 9/10, 20/20 and 22/23 Jev request transitions; the two tail forks
were refused authoring notes, not historical context rewrites. Current plain
retained full prefixes in 12/12, 25/25 and 34/35 transitions. Raw per-request
diagnostics and their generating script are saved locally as `cache-analysis.json`
and `analyze_cache.py` under the output directory. Cache-unknown tokens are zero.

## Latency: no overall speed win

These are sums of measured agent times, excluding post-turn probes. They are
not the experiment's total wall time. Model latency sums come from call receipts;
the remainder includes tools, persistence, serialization and orchestration.

| Family | Agent seconds Jev / plain | Jev-model seconds | Jev lane LLM seconds | Plain LLM seconds | Other Jev seconds |
|---|---:|---:|---:|---:|---:|
| Observation reuse | 24.152 / 17.813 | 7.710 | 15.610 | 16.725 | 0.832 |
| Generation | 55.914 / 55.442 | 13.792 | 37.602 | 49.149 | 4.520 |
| Mixed | 60.757 / 60.594 | 17.079 | 40.882 | 55.322 | 2.796 |

Total agent time: **140.823 s Jev vs 133.849 s plain (+5.2%)**. The LLM time
savings did not cover the added Jev decision time. Observation reuse is the
clearest overhead-dominated case: saving only two LLM calls did not offset twelve
Jev decisions, and its total cost was also slightly higher than plain.

There were **no MODEL_UNAVAILABLE errors** in this rerun. The previous Jev run
had 2 / 8 / 1 such failures, with approximately 61.3 / 237.8 / 30.8 seconds from
attempt start to failure observation. Consequently the large old-to-new wall
time reductions cannot be described as cache-driven speedups. No failed turn
was retried or removed in either run.

## Quality and remaining limitations

- Observation T5: Jev selected ANSWER, but its authoring response attempted
  READ_FILE(routes/items.py); the operation lock rejected it without execution.
  Recovery answered correctly from other evidence, but attributed the answer
  to routes/items.py despite never successfully reading that file. Evidence:
  observation T5 journal lines 6 (rejected proposal), 13/17 (answer/final).
- Mixed T5: a similar ANSWER -> READ_FILE proposal was refused; recovery did
  reread the changed settings and inventory. Both lanes reported fresh
  `cedar-v2`, `west-depot`, Paper Kite 3 and Orion Thermos 19. This was not stale
  context reuse. Mixed T4 and T6 also have real agent-side TestClient execution,
  unlike the earlier run's unsupported exact-test claims at T4.
- **Mixed Jev semantic error:** T5/T8 interpret Chinese "warehouse" as a Git
  repository and say it is unrecorded, while separately retaining the correct
  `warehouse=west-depot` field. T8's handoff document repeats this irrelevant
  repository interpretation. Keyword checks pass despite the contradiction.
  Evidence: mixed T5 journal lines 13/31 and T8 lines 8/15/26. The current
  inventory and total stock 22 are otherwise correct.
- Both lanes correctly distinguish revision from a checksum at mixed T7;
  however, the frozen scorer does not check that explanation. Fact-keyword
  checks do not establish source attribution, all prose semantics or complete
  filesystem nonmutation.
- Generation's core testing claims have real BASH output: hello, pytest, query,
  DATA_FILE override/current-file reads and PATCH. Some commands use `| tail`
  without pipefail or print failures without asserting; actual output must be
  checked, not exit code alone. This run's relevant outputs do show success.
  Plain T6's "all non-integers return 422" is broader than the test (which used
  `"many"`) and its non-strict integer field validation.
- Remaining arbitration is not a cache problem: generation had 8 low-confidence
  arbitrations (7 with phase confidence < .5 but operation confidence >= .5),
  mixed had 9 (8 with that pattern). This diagnostic does not prove those
  arbitrations can safely be removed, especially for mutations.

## Provenance and cleanup

- Start `2026-09-22T22:34:49+08:00`, finish `22:39:40+08:00`.
- Runtime `642c5ed3bd9a`, verified unchanged after the run; source `4e75eb7`.
- Suite digest `4219df3152a5`, stamp `fff3af4ced4e`.
- Suite-source SHA256:
  `316b55a58276eb69bb7fde6bc966b1d14f2efb8a7a6ae867533c574382fe9878`.
- Runner SHA256:
  `c8ca045ddd5db51315c05c5b9cd3654085f7d0946c59179cf4c9c2fe682e4a91`.
- Model aliases unchanged: `jev-latest` / `deepseek-chat`; immutable resolved
  provider model versions are not established by these aliases.
- Image `sha256:b19bfc61a4f4d9c9f60b611655f8c8bf6a3db08d552c94e65ed39d156c468587`.
- All six owned volume removals returned exit 0; a subsequent Docker inventory
  check confirmed none remain. Containers were closed by the runner; the shared
  immutable image was retained. No host dependencies were installed.
- Raw local artifacts: frozen suite, report JSON/Markdown, per-turn JSONL journals,
  session ledgers and the cache analysis. They are not included in the Git commit.

Conclusion: the cache-contract change substantially improved observed cache
reuse. In this sample, generation and mixed gained cost efficiency without a
latency win; observation reuse still did not amortize Jev overhead. Direct-tool
rate is not the primary explanation for these gains, and semantic grounding
remains a separate quality concern.
