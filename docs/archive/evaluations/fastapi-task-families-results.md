# FastAPI task families: first frozen paired run

> Historical experiment, archived 2026-09-23. Results describe the recorded
> runtime only. See the [evaluation index](../../evaluation/README.md) for newer runs.

## Result at a glance

This run supports **evidence reuse without tool bypass**, not a general claim
that the current Jev architecture is faster or cheaper. Observation reuse had
fewer LLM calls and a small estimated cost advantage despite zero successful
direct tool calls. The generation family was disrupted by model connectivity.
The mixed family passed its freshness/function checks but cost more than plain
LLM despite fewer steps and LLM calls.

| Family | Completed + checks, Jev / plain | Steps, Jev / plain | LLM calls, Jev / plain | Successful Jev direct tools | Estimated USD, Jev / plain |
|---|---:|---:|---:|---:|---:|
| Observation reuse (6 turns) | 6/6 / 6/6 | 13 / 19 | 11 / 19 | 0 | 0.017311 / 0.019116 |
| Generation (6 turns) | 4/6 / 6/6 | 29 / 21 | 20 / 21 | 1 | 0.049316 / 0.026838 |
| Mixed (8 turns) | 8/8 / 8/8 | 26 / 32 | 25 / 32 | 0 | 0.081053 / 0.068067 |

Overall: **38/40 lane-turns**, two of three paired families passed; process exit
was 1, not a clean pass. Generation Jev passed functional checks on 5/6 turns,
but only 4/6 also completed with an answer. Costs are configured estimates from
returned usage, not billing receipts; unreported usage of failed requests is
unknown. Steps include failed attempts, while recorded model-call counts do not
include the connection failures without call receipts.

## Frozen workload and provenance

The task descriptions, fixtures, behavioral probes and limits were fixed before
model calls. See [the contract](../../evaluation/fastapi-task-families.md). These are new
derived tasks, **not** a verbatim replay of the README or `fastapi_case.json`.
The generation family follows the same progression but specifies an importable
`app.py` and exact minimal endpoint contracts, using TestClient rather than a
long-running server. The eight-turn mixed family is a bounded cross-turn test,
not a stress test of very long conversation compression or large repositories.

- Agent baseline: `a22f61c`, runtime digest `cdf87e8f5b6b`, unchanged throughout.
  In particular, the diagnosed phase/binding confidence coupling was not fixed
  during this experiment.
- Suite: `fastapi-task-families-v1`, frozen JSON digest `4219df3152a5`.
- Started `2026-09-22T21:31:39+08:00`, finished `21:41:44+08:00`.
- Models: unchanged aliases `jev-latest` / `deepseek-chat`.
- Run stamp: `7d73caf40e9e`; journal IDs are
  `bench-fastapi-{observation-reuse|generation|mixed}-7d73caf40e9e-t{N}`.
- Image: `sha256:b19bfc61a4f4d9c9f60b611655f8c8bf6a3db08d552c94e65ed39d156c468587`.
- Suite-source SHA256:
  `316b55a58276eb69bb7fde6bc966b1d14f2efb8a7a6ae867533c574382fe9878`.
- Runner-source SHA256:
  `c8ca045ddd5db51315c05c5b9cd3654085f7d0946c59179cf4c9c2fe682e4a91`.
- Full local frozen suite, reports, ledgers and journals:
  `backend/artifacts/bench/fastapi-families-20260922/`.

Both lanes share the same tools, image, 20-step and 600-second per-turn limits,
0.5/0.4 confidence/ambiguity settings and disabled progress floor. Lanes run
sequentially, Jev first, in separate persistent containers per family. This is
not the historical dashboard's parallel/per-turn-container protocol; avoid
cross-protocol latency comparisons. All six family volumes have successful
deletion receipts in `report.json.cleanup`; the separate preflight volume was
also removed. No failure was rerun, discarded or resolved by a harness bypass.

## 1. Observation reuse: no direct execution, yet real reuse benefit

Jev's first turn used two batch reads to obtain six files. Turns 2–5 then only
answered using existing evidence. It first read the runbook in turn 6. Plain LLM
read all seven fixture files in turn 1, then reread configuration, inventory,
routes and runbook in turns 3–6. Jev had 3 READ calls versus plain's 8, and 2 LIST
calls versus 5. There was no repeated Jev file read to inflate a bypass metric.

Recorded LLM calls decreased from 19 to 11, and estimated total cost decreased
9.4%. This is compatible with a benefit from batching and evidence reuse, even
though **successful direct tool calls were zero**. It is one pair of trajectories,
not proof that Jev always batches or reuses better. ANSWER still uses an LLM;
answering from earlier observations is not counted as LLM-free execution.

There were two exact scalar candidate matches, both LIST calls in turn 1:

- Root defaults: a prior model connection failure forced recovery arbitration.
- `LIST_FILES(routes)`: the concrete arguments existed, but Jev initially chose
  ANSWER; arbitration selected LIST. Candidate availability alone does not prove
  the original operation was safe to execute.

A separate batch opportunity existed: Jev selected README + app with complete
read arguments, but weak batch-mode/member confidence led to parameter authoring
that produced the same normalized call. This is **not** included in the scalar
coverage count. The second batch helper expanded two proposed route files into
four files by adding config/data, so that call was not merely redundant copying.

Evidence: observation T1 journal step records at lines 12, 19, 26, 33;
T3 baseline reread at line 18. Read-only review confirmed the core answers against
the fixture, while noting plain's final runbook answer retained a DSML end tag.
Keyword/hash checks do not grade all output formatting or prose semantics.

## 2. Generation: few inherent direct opportunities; infrastructure confound

The only direct success was the initial default directory listing. Successful
model-backed operations were LIST 2, SEARCH 1, WRITE 6, BASH 8, ANSWER 4. Current
bindings provide references/defaults, not new program content or complete shell
commands; low direct execution is expected for much of this work.

Eight model connection failures occurred on the Jev lane. Three consecutive
failures in turn 2 triggered NO_PROGRESS_LIMIT before `app.py` existed; the strict
probe consequently failed import. Turn 3 recovered by discovering the missing
app and creating it along with the requested test. Turn 6 wrote working PATCH
code and passed all functional probes, but three subsequent connection failures
prevented the final answer and again triggered NO_PROGRESS_LIMIT.

Thus the 4/6 completion result cannot be treated as two demonstrated code-generation
failures, and the high cost/step counts cannot cleanly isolate architectural
quality from recovery work. It also cannot be called a successful six-turn
delivery. Plain completed and passed all six turns. No clean generation-family
advantage or parity is established by this run.

## 3. Mixed: freshness works, but fewer calls did not mean lower cost

Both lanes passed all eight frozen turn checks. At turn 5 both actually reread
the changed config/data and reported `cedar-v2`, `west-depot`, default limit 1,
and Orion Thermos stock 19. The initial Jev ANSWER attempt was declined before
dispatch; subsequent observation recovered correctly. At turn 6 both implemented
and tested `/snapshot`; the independent probe changed temporary files between
requests and verified current values, including all-item counts rather than the
paginated subset. Turn 8's final handoff matched current facts. Plain's draft
typo `celeb-v2` was corrected by another real WRITE before final acceptance.

Jev used 25 LLM calls versus 32 and 26 runtime steps versus 32, but estimated
total cost was **19.1% higher**. Returned usage identifies an important billing
factor that total token/call counts alone hide:

| Mixed LLM usage | Jev lane | Plain lane |
|---|---:|---:|
| Input tokens | 453,803 | 697,093 |
| Cache-hit input tokens | 307,712 | 659,072 |
| Cache-miss input tokens | 146,091 | 38,021 |
| Cache-hit share | 67.8% | 94.5% |
| Output tokens | 9,500 | 10,606 |
| Estimated LLM cost | $0.071434 | $0.068067 |
| Additional Jev cost | $0.009619 | $0 |

The current estimator prices misses above hits. Different cache utilization plus
the extra Jev calls explains why fewer total LLM input tokens did not imply lower
estimated total cost here. Whether dynamic tool schemas/bound constants/helper
messages caused the cache difference requires a separate experiment; this run
does not establish that causal mechanism.

**Answer-faithfulness limitation:** Jev turn 4 only produced ANSWER, reusing turn
3's real test evidence. Its table nevertheless described `q=kItE` and
`q=zzz&limit=1` as actual results, although those exact combinations were not run
in either relevant agent script. Most of the table had historical evidence, and
the independent functional probe passed, but unexecuted cases must not be stated
as observed results. This is a qualitative defect outside the frozen function
scorer, not a reason to retrospectively change its pass count. See mixed T4 lines
7/12 and T3 line 21.

Mixed traces also retained one INVALID_PROPOSAL on each lane, one EFFECT_UNKNOWN
on Jev and two on plain. Examples include failed `git` commands (git unavailable)
and a temporary verification script with a failed import before corrected
PYTHONPATH. `resolved=true` for an ended command does not establish zero side
effects. Selected fixture hashes passed, but the scorer does not prove no
temporary writes or no changes outside those protected paths. Neither lane used
the broad process-name cleanup patterns flagged in the earlier README replay.

## Timing and metric cautions

| Family | Raw Jev seconds | Raw plain seconds | Jev MODEL_UNAVAILABLE attempts | Measured failed-attempt seconds |
|---|---:|---:|---:|---:|
| Observation reuse | 86.220 | 22.906 | 2 | 61.258 |
| Generation | 290.380 | 35.069 | 8 | 237.818 |
| Mixed | 99.102 | 55.220 | 1 | 30.784 |

Failed-attempt durations come from journal `attempt_started` to the corresponding
MODEL_UNAVAILABLE observation. They are not server-side timing. These failures
have no model-call receipt, so summing `latency_ms` alone misses their waits.
Do not call the residual "tool time", attribute all delay to the service backend,
or subtract the waits and present the result as a controlled no-failure replay:
failures also changed recovery decisions and later trajectories.

**Legacy `direct_jev_steps` is not successful direct execution.** It counts
attempts without helpers, including connection failures. Native totals are
2 / 9 / 1 for the three families; all but generation's single real LIST success
are the 11 connection failures. The new `trace_analysis.successful_direct`
requires a real tool operation, model-call evidence, no LLM helper and SUCCEEDED.
Use **0 / 1 / 0**, not the generic renderer's inflated direct/avoid columns.
Runtime metrics were not silently changed in this frozen experiment.

Exact scalar candidate matches on successful calls were 2 / 2 / 1; only one was
executed directly. This diagnostic excludes synthetic batch combinations and
does not judge whether each action was necessary, optimal or originally selected
correctly. It is not a universal opportunity-normalized direct success rate.

## What this changes in our judgment

1. Evidence reuse and avoided redundant work should be first-class metrics:
   observation tasks can improve without any LLM-free tool execution.
2. Candidate availability, candidate selection, execution permission and actual
   success need separate accounting. A complete candidate is not evidence that
   arbitration was unnecessary; the batch and phase-confidence paths deserve
   targeted validation before changing thresholds.
3. Locked-operation generation must demonstrate net benefit after Jev overhead
   and cache behavior. Lower arbitration/call counts alone are insufficient.
4. Functional correctness and truthful execution reporting are separate. Preserve
   reuse, but distinguish "previously verified", "inferred" and "executed now".

No new prompt/driver/runtime fixes were made after observing these results.
Validation receipts: 288 host tests passed, 9 dependency-specific controls skipped;
16 sandbox control tests passed; Ruff and diff whitespace checks passed. Independent
review covered runner failure accounting/cleanup and the observation/mixed traces.
