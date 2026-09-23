# FastAPI replay after independent context projections

Latest recorded branch experiment as of 2026-09-23, not a rolling benchmark.
See [evaluation index](README.md) for earlier runs and workload distinctions.

## Frozen protocol

Requested rerun on `937e002`, runtime digest `03a2e8bfb9d6`, 2026-09-22.
Compare with the stable-schema run (`642c5ed3bd9a`), not with a different task
suite. No tuning, selective turn retries, runtime edits, scoring changes or injected task hints
during execution. Preserve failures and separate quality from completion.

- Three task families: observation reuse (6 turns), generation (6), mixed (8),
  both lanes, same seeds, updates and strict probes. Original sequential paired
  schedule, Jev first, 20 steps / 600 seconds per lane-turn.
- Original README session: six hash-verified requests, parameter digest
  `775a58a7dd7c`; original parallel lane schedule, per-turn containers with
  persistent workspace volumes, 600-second watchdog. Native checks mean answered,
  not functional verification.
- Run the suites serially to avoid introducing cross-suite resource contention.
  Each uses fresh isolated ledger/cache/volume scopes. Same configured aliases
  `jev-latest` and `deepseek-chat`, thresholds and immutable sandbox image.
  Aliases do not establish immutable provider model versions.
- Outputs: `backend/artifacts/bench/fastapi-families-projection-20260922-234200/`
  and `backend/artifacts/bench/readme-fastapi-projection-20260922-234200/`.
- Compare input/output tokens, cache-hit/miss counts, estimated cost, actual
  direct executions, total/model latency and semantic evidence. Historical
  comparisons are not repeated/interleaved causal A/B trials.
- Independently review the traces. No paid model or extra sandbox probes are
  added for post-hoc review. Raw artifacts remain local and Git-ignored.

## Results

Three families finished with **40/40 lane-turns passing the frozen hard checks**,
unchanged from the previous stable-schema run. No failed turn was rerun.
README replay finished with **12/12 answered turns**, a completion-only check,
not functional or semantic verification. Both runners exited 0.

### Three families: previous Jev vs current Jev

| Family | LLM calls old → new | Input tokens old → new | Estimated USD old → new | Cost change |
|---|---:|---:|---:|---:|
| Observation reuse | 11 → 11 | 80,361 → 47,203 | 0.013581 → 0.010665 | -21.5% |
| Generation | 21 → 22 | 277,215 → 147,658 | 0.038928 → 0.027468 | -29.4% |
| Mixed | 24 → 24 | 409,989 → 194,466 | 0.052037 → 0.031322 | -39.8% |
| Total | 56 → 57 | 767,565 → 389,327 | 0.104546 → 0.069455 | -33.6% |

Input tokens fell **49.3%**, cache-miss tokens **62,285 → 25,935 (-58.4%)**,
and weighted input cache-hit share **91.9% → 93.3%**. Output tokens fell
17,796 → 16,189. The gain is not explained by fewer LLM calls. All requests
still use one canonical tool-schema hash `200c4930788a`, with zero schema
switches. Full previous message prefixes survive 9/10, 20/21, 22/23 Jev request
transitions; each exception is a refused proposal's temporary tail note. Plain
prefix preservation is 12/12, 34/34, 32/32. Cache-unknown tokens are zero for
recorded calls; failed calls without receipts remain unaccounted.

Successful direct tools remain **1 / 1 / 0**, the same two root listings as
before. Raw steps rose 58 → 66, including seven connection-failure attempts;
subtracting those attempts leaves 59 versus 58, not a step-count improvement.

### Concurrent-version comparison (same run, sequential lanes)

| Family | Current Jev / plain LLM calls | Current Jev / plain USD | Jev cost vs plain |
|---|---:|---:|---:|
| Observation reuse | 11 / 13 | 0.010665 / 0.008958 | +19.1% |
| Generation | 22 / 35 | 0.027468 / 0.047139 | -41.7% |
| Mixed | 24 / 33 | 0.031322 / 0.037745 | -17.0% |
| Total | 57 / 81 | 0.069455 / 0.093842 | -26.0% |

Plain also receives the independent LLM projection. Its input tokens fell
1,277,437 → 775,196 and cost 0.131212 → 0.093842, despite calls rising 75 → 81.
Thus this is a shared-context improvement, not an exclusively Jev mechanism.
This does not mean every plain family became cheaper: generation cost rose
0.044404 → 0.047139 (+6.2%), with calls increasing 26 → 35.
Observation reuse still does not amortize Jev's added decision cost.
Costs use the unchanged estimator and recorded usage, not billing receipts;
unreceipted failed-request costs are unknown, not zero.

### Latency: infrastructure prevents a clean speed claim

| Family | Current Jev / plain seconds | Jev model receipts | Jev LLM receipts | Failed decision attempt seconds | Unattributed remainder |
|---|---:|---:|---:|---:|---:|
| Observation reuse | 54.660 / 17.416 | 10.167 | 12.781 | 30.667 | 1.045 |
| Generation | 210.180 / 72.834 | 13.991 | 34.825 | 156.232 | 5.132 |
| Mixed | 86.311 / 56.559 | 18.072 | 35.517 | 30.004 | 2.718 |

Seven Jev decision-stage `MODEL_UNAVAILABLE` observations account for about
**216.903 seconds**, measured from their corresponding attempt starts. They
have no model latency/usage receipt. Do not classify that residual as tool or
projection overhead. No such errors occurred in the previous stable-schema run.

Raw Jev agent time is **351.151 s vs previous 140.823 s**. Subtracting the
observed failed-attempt intervals leaves 134.248 s, but this is a descriptive
decomposition, **not a counterfactual failure-free runtime**: recovery changes
the later trajectory. Successful Jev-model receipt time actually rose
38.581 → 42.230 s; Jev-lane LLM receipt time fell 94.094 → 83.123 s. These
single historical samples cannot establish an architecture-driven speedup.

### Trace quality, beyond hard checks

- Observation reuse: prior T5 unsupported source attribution did not recur.
  The current Jev T1 successfully read `routes/items.py`, so later reuse has a
  real source. T1 still has an ANSWER/READ_FILE operation-lock refusal, but its
  recovered answer does not claim the refused runbook read succeeded. T6 reads
  the runbook after recovering from a connection failure. Minor prose errors
  remain, including a wrong count of top-level files.
- Generation: both lanes really implemented and tested file persistence,
  `DATA_FILE`, fresh reads and PATCH. Shell pipelines masked actual failing
  tests as exit 0: Jev T5 line 22 had a bad Response-object assertion; plain T5
  line 74 had a wrong relative test path. Both later repaired/retested (Jev
  line 29; plain lines 81/88/95). Jev's final claim that the precise override
  plus `q=app` branch was successfully tested is too strong; plain still
  overstates strict integer rejection. The implementation's tested main paths
  passed, not every prose claim or input edge.
- **Mixed Jev T4 regressed on execution evidence:** only ANSWER occurred in
  that turn (journal lines 8/12), but it presented exact `q=BrAsS cOmPaSs` and
  explicit `limit=2` requests as executed tests. T3 line 29 actually exercised
  `q=COMPASS`, `q=co`, default limit and `limit=1`, not those exact cases.
  Plain T4 line 18 really ran mixed-case assertions. Most endpoint behavior
  has evidence, but this is not fulfillment of the current verification
  request and is not merely harmless reuse. The previous stable-schema run
  had real T4 executions in both lanes; 40/40 hard checks hide this regression.
- Mixed freshness and warehouse interpretation are better in this sample:
  T5 rereads changed configuration/inventory (line 13), T8 rereads and writes
  the correct `west-depot` handoff (lines 12/19/26); the previous Git-repository
  misinterpretation did not recur. T7 successfully reads the runbook and
  correctly distinguishes revision from checksum. These are single-sample
  observations, not proof that a general semantic failure was fixed.
- Mixed T6 has real tests in both lanes. Jev verifies config refresh and file
  override but does not change inventory after the first request in that
  process; plain verifies both. Do not inflate the Jev agent-side test scope.

## Original README six-turn replay

| Jev metric | Previous stable-schema run | Current independent projection |
|---|---:|---:|
| Steps | 26 | 32 |
| LLM calls | 25 | 30 |
| Successful direct tools | 1 | 0 |
| LLM input tokens | 467,012 | 386,978 |
| LLM output tokens | 13,026 | 18,023 |
| Cache-miss tokens | 29,764 | 15,010 |
| Weighted input cache-hit share | 93.6% | 96.1% |
| Estimated total USD | 0.061296 | 0.060725 |
| Agent seconds | 118.406 | 179.634 |

Input fell 17.1%, cache misses 49.6%, but output rose 38.4% and LLM calls 20%.
Estimated cost is therefore almost unchanged (**-0.9%**). The current 30 LLM
calls comprise 22 arbitration, 6 parameter-authoring and 2 content-authoring
calls. One stable schema and 29/29 full message-prefix extensions are retained.
There are no successful direct tool effects; do not misinterpret the native
two direct-step counts, which correspond to failed decision attempts.

Current plain has 52 calls (unchanged), 896,661 input tokens, 26,116 output
tokens, 23,061 cache misses, 97.4% hit share, USD 0.096106 and 187.698 seconds.
Its cost fell 38.1%, with one schema and 51/51 prefix extensions. Current Jev
cost is nominally 36.8% lower than plain, but scope and verification depth
still differ: plain T6 added/tested more CRUD behavior and a 30-request
single-process concurrency sample. This is not identical-work efficiency proof.

Jev had two decision connection failures at T1, totaling 61.277 seconds with
unknown usage. Recorded successful Jev time is 20.421 seconds, LLM time 66.258,
and the remainder after accounting for failed attempts is 31.678. Subtracting
failed intervals from total gives 118.357 seconds, numerically close to the
old 118.406, but again **not** a failure-free counterfactual. Plain has 109.702
seconds of LLM receipts and 77.996 other runtime. No general speed win is shown.

### Quality relative to the previous README run

- **T3/T4 materially better in this sample:** Jev now really starts and tests
  the service, then implements/tests query endpoints, instead of merely citing
  an earlier test and asking for query clarification. Evidence: T3 journal
  `1adbbea4a9ec.jsonl` line 18; T4 `dd783cc1c173.jsonl` lines 22/32. This makes
  an unchanged-cost comparison more meaningful than the raw cost delta alone,
  but is still not a repeated causal result.
- **T5 real restart persistence:** `e56213d3d798.jsonl` lines 142/163 show
  Jev stopping the actual old process, confirming the connection fails, then
  starting a new process and reading persisted data. Earlier attempts using
  missing `pkill`/`ps` failed, including an address-in-use error (lines 96/114/124).
  Plain also has real stop/restart/readback at line 131, after correcting an
  invalid Python path at lines 58/86. Old Jev's unsupported stop sequence does
  not recur, but its final "clean seed state" is inaccurate: a test-added grape
  remains (lines 170/190). Some listed T5 checks were not executed in that turn.
- **T6:** `3d05910c0bbf.jsonl` line 65 contains real Jev PATCH/PUT responses,
  disk contents, restart/readback, 400/404/422 cases and regressions. Explicit
  null fields remain an untested static edge: the generated Optional PATCH
  model plus `exclude_unset=True` permits null through to storage (line 30),
  where it is ignored rather than written (line 16). Explicit null rejection
  is not established; unlike a null overwrite, this does not demonstrate
  persisted invalid values. The "only supplied fields are changed" wording
  should distinguish null from other supplied values.
  Plain's update/restart/concurrency output is real (lines 72/88/95), but its
  final claim that `max(id)+1` never reuses a deleted maximum ID is false
  (line 127). No new post-hoc endpoint probes were run.
- Both lanes still confuse observation-time service state with persistence
  across container lifetimes in some prose. Only the workspace volume persists
  between turns. Plain also overclaims T2 process cleanup despite missing
  `pkill`/`ps`, and recovers from an old-PID stop error in T4. These limitations
  remain separate from the native answered-turn score.

## Representation-only check

Applying the new deterministic LLM projection to the **same old request
histories**, without rerunning models or changing their old action sequence,
reduces summed serialized message characters as follows:

| Historical workload | Lane | Old characters | Projected characters | Reduction |
|---|---|---:|---:|---:|
| Three families | Jev | 2,279,323 | 1,274,527 | 44.1% |
| Three families | Plain | 3,875,243 | 2,074,940 | 46.5% |
| README six turns | Jev | 1,389,134 | 808,125 | 41.8% |
| README six turns | Plain | 5,379,144 | 2,889,987 | 46.3% |

This measures request-message representation, excludes tool schemas, and counts
repeated history in every request. It is **not** a tokenizer estimate, context
window size, billing estimate, or evidence of identical model behavior. The
new source also preserves provider evidence independently of Jev display budgets;
the change is not merely removing repeated characters. Durable transcripts remain
the source of truth; this projection is not source-data deletion or deduplication.

## Provenance and cleanup

- Source `937e002`; projection change `ed74843`; package reorganization changes
  imports/layout rather than the benchmark policy. The previous comparison
  runtime is `642c5ed3bd9a` from the stable-schema implementation `4e75eb7`.
- Family run began `2026-09-22T23:42:37+08:00`, stamp `904ef6d06980`;
  suite digest `4219df3152a5`, runtime `03a2e8bfb9d6`, verified unchanged.
- Family suite source SHA256
  `316b55a58276eb69bb7fde6bc966b1d14f2efb8a7a6ae867533c574382fe9878`;
  frozen task file matches the previous run byte for byte.
- Relocated runner SHA256
  `a3d69d5682e6288818e95790c1440d8d468281e2325ad821893e06ba500a7802`.
- Every family sandbox used image
  `sha256:b19bfc61a4f4d9c9f60b611655f8c8bf6a3db08d552c94e65ed39d156c468587`,
  the same image as the previous cache runs.
- Family runner exited 0. All six exact-owned volume removals returned 0;
  subsequent Docker inventory confirmed none remain. No shared image was removed.
- README began `2026-09-22T23:51:21+08:00`, session
  `readme-fastapi-3dd65d87d1b7`; parameter digest `775a58a7dd7c`, same image and
  runtime. Runtime was independently rechecked after both suites. Cleanup
  returned normally and both exact-owned session volumes were verified absent.
- README journal IDs in turn order: `4514d975a8a4`, `822c95182482`,
  `1adbbea4a9ec`, `dd783cc1c173`, `e56213d3d798`, `3d05910c0bbf`.
- Per-turn journals, frozen suite, reports, `analyze_comparison.py` and derived
  `comparison.json` stay in the local ignored output directories above.
  The analysis reads existing receipts only; no post-hoc model calls or sandbox
  tests were used to repair or regrade the experiment.

## Interpretation

The strongest result is **less model-visible context at comparable Jev-lane LLM
call counts**, with lower recorded Jev cost in all three constrained families.
Both lanes have lower aggregate input and cost, but plain generation cost rises;
higher direct rate is neither observed nor needed to explain the input reduction. The
README workload shows why input reduction alone does not guarantee a proportional
cost reduction: more work, output and arbitration offset the savings.

This supports keeping the independent-projection contract. It does **not**
establish a general speedup or "no quality regression": current-task execution
versus historical evidence is still confused in some answers, and hard checks
miss those mistakes. The next design question should be evidence-backed
completion/verification, not forcing direct calls or modifying the durable source
to optimize a single model's view. No such follow-up change was made in this run.
