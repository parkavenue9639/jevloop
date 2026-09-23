# Jev observation-binding prompt contract experiment

> Historical experiment, archived 2026-09-23. This is not the current prompt
> specification. See the [evaluation index](../../evaluation/README.md) for newer runs.

## Pre-run contract

Optimize only the Jev request's runtime-owned instructions and criterion labels.
Separate operation purpose from dynamic argument uncertainty; treat observations
as bounded historical evidence; preserve complete provider selection guidance.
No natural-language matching or candidate construction from user text is allowed.

Keep question topology, candidate IDs and argument values, schemas, tools,
authorization, confidence/ambiguity thresholds, LLM prompts, model settings and
completion criteria unchanged. Existing invocation-wide prompt limits still apply.
Do not change the shared evidence projection to tune this run.

Control: `artifacts/bench/readme-fastapi-observations-20260922/`, runtime
`a6e1c6ec8d2e`. Replay the same hash-verified README six turns with
`scripts/replay_readme_fastapi.py`, not `benchmarks/fastapi_case.json`.
Preserve both raw runs; report client-side Jev and LLM latency separately from
residual elapsed time. Completed/answered is not independent semantic grading.
One sequential before/after sample cannot establish causality or general gains.

## Implemented changes and validation

- Phase means intended purpose, not invocation mechanism. Dynamic argument
  uncertainty is distinct from uncertainty about which operation to perform.
- Binding instructions distinguish exact complete values, partial locked values,
  defaults and the always-available LLM_PARAMETERS path.
- Prior reads no longer claim complete content remains in the bounded context.
  Scope, range, truncation, eviction and freshness qualify reuse.
- Jev action criteria retain the full code-owned provider description, including
  open paths, pagination and tool boundaries, subject to existing global limits.
- RESPOND and progress describe goal-specific requirements rather than a fixed
  artifact-production sequence. RESPOND guidance lives in the emitted phase head
  because the singleton ANSWER action does not have its own question.

Shared CORE_ACTIONS, ANSWER_TEXT, state projection, LLM helpers, schemas, routing
and execution logic are unchanged. Read-only independent review identified the
singleton-head placement issue before execution; it was corrected and tested.
274 backend tests and Ruff passed. A control/current in-memory compiler comparison
confirmed equal question keys/types, criterion IDs, compiled routing maps and
existing bound argument values for empty and observed workspaces. Added regression
tests cover full descriptions, operation/parameter separation, partial/evicted
evidence, goal-independent candidates, displayed defaults and prompt budgets.

## Run receipt

Started: `2026-09-22T21:02:05+08:00`.
Runtime digest: `cdf87e8f5b6b`; source-parameter digest: `775a58a7dd7c` (unchanged).
Models: `jev-latest` / `deepseek-chat` (unchanged aliases).
Image: `sha256:b19bfc61a4f4d9c9f60b611655f8c8bf6a3db08d552c94e65ed39d156c468587`
(same as the control).
Session: `readme-fastapi-44c0907077c9`.
Ordered run IDs: `3021dc3e89de`, `bdf7f541b19a`, `fb3879a86cce`,
`540e45d36dba`, `388ef5884b70`, `73ccb746a36a`.
Full local events and reports:
`backend/artifacts/bench/readme-fastapi-prompt-contract-20260922/`.
No runtime source was changed during execution. Session-specific volumes were
removed by the harness and their absence verified; raw evidence remains intact.

```sh
cd backend
PYTHONPATH=. .venv/bin/python scripts/replay_readme_fastapi.py \
  --out artifacts/bench/readme-fastapi-prompt-contract-new-run
```

## Jev before/after results

| Metric | Observation-binding control | Prompt-contract replay |
|---|---:|---:|
| Completed and answered | 6/6 | 6/6 |
| Runtime steps | 27 | 21 |
| Jev calls | 27 | 21 |
| LLM calls | 27 | 20 |
| Total model calls | 54 | 41 |
| Full arbitration calls | 21 | 11 |
| Locked-operation parameter authoring | 3 | 6 |
| Answer content authoring | 3 | 3 |
| Successful tool calls without LLM | 0 | 1 |
| Estimated total cost | $0.066160 | $0.056673 |
| Sum of lane elapsed time | 179.644 s | 126.005 s |

Steps decreased 22.2%, LLM calls 25.9%, and estimated cost 14.3% in this sample.
Arbitration rate fell from 77.8% to 52.4% of steps. Of 11 arbitrations, 8 upheld
the Jev pick and 3 overrode it (control: 5 upheld / 16 overridden).
All 11 were low-confidence triggers; none was recovery-triggered. The confidence
breakdown was 7 phase-only, 1 action-only, 3 both (control: 13 / 1 / 6 plus one
recovery). Action confidence is conditional on phase; this does **not** establish
that phase-only arbitration can safely be removed.

The one successful direct call was turn 1 LIST_FILES with
`{"path":".","offset":0,"limit":100}`. This demonstrates selection of a complete
default binding, **not** direct reuse of observed file references. Observed-reference
READ/WRITE bypass remains unproven by this run; 20 of 21 steps still called an LLM.

| Turn | Control steps | Replay steps | Control seconds | Replay seconds |
|---|---:|---:|---:|---:|
| Inventory | 2 | 2 | 4.325 | 2.868 |
| Create Hello World | 6 | 4 | 26.049 | 8.416 |
| Start and test | 2 | 2 | 13.557 | 13.860 |
| Add query endpoint | 6 | 3 | 33.805 | 20.622 |
| JSON persistence | 7 | 5 | 47.188 | 35.216 |
| Add mutation endpoint | 4 | 5 | 54.720 | 45.023 |

## Separate latency from routing

Values below sum raw step model-call `latency_ms`; residual is lane elapsed minus
model-call latency, not a measurement of tool execution alone.

| Jev-lane timing | Control | Replay |
|---|---:|---:|
| Jev client calls, cumulative | 78.687 s | 30.287 s |
| LLM calls, cumulative | 53.125 s | 46.746 s |
| Residual runtime/tool/persistence time | 47.832 s | 48.972 s |
| Jev call median | 1.798 s | 1.155 s |
| Jev call p95 (nearest rank) | 5.071 s | 3.886 s |
| Jev call max | 21.603 s | 5.517 s |

Of the 53.639-second elapsed reduction, 48.400 seconds is the reduction in
cumulative Jev client latency. Both call count and latency distribution changed.
Client timings include transport/provider behavior and possible retries; they do
not separate server queueing from inference. Thus the 29.9% elapsed reduction is
not a prompt-only speedup claim, nor proof that the control's tail was exclusively
service fluctuation. Requests also grew: serialized request sizes were
11,362–33,214 bytes versus 7,425–26,006; actual head counts were 8–17 versus 8–21
because observed resources and trajectories differed, not because topology rules
were changed.

## Failed plain lane and evidence limitations

The paired suite **failed overall** (exit 1, 7/12 answered lane-turns). The plain
lane answered only turn 1. Its fifth WRITE_FILE attempt in turn 2 timed out at
dispatch, yielding EFFECT_UNKNOWN; subsequent turns stopped before decisions
because the persisted session retained the unresolved effect. This is a tool
execution timeout, not an observed Jev transport failure. Its root cause is not
established. The external 600-second watchdog did not fire.

Plain totals (8 steps, 70.621 seconds, $0.005196) are retained but are **not a
comparable completed-work baseline**. Do not use the generic report's relative
performance ratios as evidence of either lane's advantage. There was no automatic
retry, failure deletion or safety-gate bypass. The stopped plain lane also changes
the concurrent workload relative to control; before/after latency is confounded.

Jev had no typed execution/validation errors in this replay. That is not proof of
semantic correctness: shell exit success can mask intermediate failures. Trace
inspection shows broad `pkill -f` / `pgrep -af` commands inside the isolated sandbox,
contrary to the existing PID-scoped tool guidance, and port-conflict recovery in
turn 6. Answer text also recommends broad process cleanup. These are unresolved
process-management/adherence issues, not silently counted as independent correctness
passes or fixed during this prompt-only experiment. The current gate remains
completed/answered; no endpoint-equivalence or process-policy scorer was added.

Conclusion: the sample supports better separation of operation selection and
parameter authoring, with fewer full arbitrations. It does not yet demonstrate
frequent observation-derived direct execution, a general causal improvement, or
a clean end-to-end win over a completed concurrent plain-LLM baseline.
