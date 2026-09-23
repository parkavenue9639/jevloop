# Original observation-view contract (historical)

> Archived on 2026-09-23. Partial binding and shared source/display budgets
> below have been superseded. Retained for design history and original run
> receipts, not as implementation guidance. See the current
> [observation contract](../contracts/observation-views.md).

Status: implementation contract, 2026-09-22. Branch:
`feat/observation-view-bindings`, baseline `7836c03`.

Update: [Transcript projection contract](../contracts/transcript-projection.md)
supersedes storing bounded Jev views in the source and shared ledger/display
budgets. New records persist observation facts; Jev and LLM recover independent
views. Historical implementation receipts below are retained as history.

Update: [Stable LLM tool contract](../contracts/llm-cache.md) supersedes the
partial-binding/remaining-parameter authoring design below. Historical run
receipts remain unchanged; current candidates must be complete, or Jev selects
LLM_PARAMETERS for unrestricted full-argument generation for the chosen tool.

## Objective and authority

Refactor the shared agent runtime so Jev chooses operations and may select
arguments grounded in previous tool observations, while every available tool
always permits LLM-authored parameters. Implement and validate both drivers,
then rerun the prior FastAPI baseline specified by the user. The initial
interpretation as `procurement_10turn.json` was incorrect; its two runs are
retained as supplemental evidence, not completion of the requested baseline.
Do not publish, merge, alter benchmark expectations, or mutate external Lark data.

## Non-negotiable invariants

1. Never mechanically match, parse, or rank against user natural-language input
   to choose operations, resolve objects, or manufacture argument candidates.
   Models interpret intent; deterministic code projects execution evidence.
2. Tool availability is independent of observed argument candidates. Capability,
   authorization and runtime prerequisites still constrain availability.
3. Every available operation has an `LLM_PARAMETERS` binding alternative, even
   when complete direct candidates exist. It is ordinary parameter authoring,
   not arbitration and not an endorsement overriding write-confidence policy.
4. Tools have one canonical typed argument contract shared by Jev, authoring,
   arbitration, plain LLM, validation, frozen intent and execution. Missing
   arguments are generated together, including authored content. No hidden
   second authoring or unbounded repair loop.
5. Tool observations contain bounded evidence and optional grounded references.
   Unprojectable raw output remains visible evidence; arbitrary Bash stdout is
   never assumed to be a file listing. No extra hidden discovery executions.
6. A model-facing view and its binding candidates come from the same bounded
   snapshot. Reference identity includes the workspace/path or provider identity;
   correlated arguments remain together. Open arguments never require membership
   in the shortcut pool, but must pass tool-specific and authorization validation.
7. Observations are historical, not guaranteed-current world snapshots. New
   observations replace/update the relevant view; references retain provenance.
   Staleness and truncation are explicit. State is not a complete resource index.
8. The append-only ledger remains the recovery source. Persist exact frozen
   arguments and projection metadata before execution; resume never regenerates
   arguments for an old call. Live and restored bounded views must agree.
9. All lanes use the same runtime, tool surface, policy, budgets and validators.
   Existing UNKNOWN-effect, idempotency, duplicate/no-progress and recipient
   protections remain intact. Authoring failures are pre-dispatch observations.

## Architecture

`tool result -> observation view -> references -> compatible bindings`

- Observation view: source operation/call, execution scope, bounded evidence,
  typed references, completeness metadata. Keep the latest bounded views rather
  than flattening every historical object into each speculative question.
- Binding: a stable reference/label plus complete or partial canonical arguments.
  Deterministic bindings use only result fields, invocation context and declared
  safe defaults, never inferred user intent or Cartesian products of parameters.
- Compiler: operation choices plus conditional binding choices, including
  `LLM_PARAMETERS`, in the same Jev request. Bound invocation-wide candidate size.
- Materializer: resolve selected bindings, author missing/open arguments through
  the selected tool schema, validate, then freeze the canonical intent.
- Executor: dispatch only frozen arguments; ledger and fingerprints include all
  executable arguments. Compatibility adapters accept existing target/text logs.

Prefer a small provider-extensible implementation over a universal ontology or
task-specific planning DSL. Preserve bounded multi-target reads where coherent.
Legacy LIST_FILES must become scoped/bounded rather than exhaustive recursive
inventory; read accepts a direct sandbox path and bounded range. Search/discovery
results should produce reusable references. Bash remains an open command tool;
only explicitly supported truthful result projections may yield references.

## Validation and comparison

- Offline tests: no candidates still allows READ + LLM parameters; direct known
  reference uses zero authoring calls; selected-operation authoring cannot change
  operation or bypass policy; canonical schema equality across lanes; malformed,
  unknown and out-of-sandbox parameters fail before dispatch.
- Projection tests: raw Bash evidence retained; scoped listing/search/range
  provenance; caps align state/questions; >20 references; cross-turn/resume
  equivalence; stale/deleted resources; truncated result metadata.
- Runtime tests: complete argument fingerprints and ledger calls, exact accounting
  of authoring/arbitration, recovery/UNKNOWN behavior, existing suites and Docker
  smoke. Keep source changes and baseline data separate.
- Rerun the exact six-turn README FastAPI session on both Jev and plain LLM,
  preserving original goals, thresholds, budgets and parallel dashboard scheduling.
  Record source identity, suite hash, model configuration, per-turn success,
  steps, direct/authoring/arbitration calls, elapsed time, tokens and cost.
  The historical README reports answered turns, not independent semantic grading.
  Supplemental procurement keyword checks do not replace this FastAPI replay.
  Do not claim universal gains from one paired run.

## Progress / handoff

### Issue #11 feedback incorporated

Source: https://github.com/typesafe-ai/skills/issues/11#issuecomment-5775252156
(2nd1st, 2026-09-22). The commenter reports a benign 36-call measurement:
untrusted-data instructions reduced asserted-option probabilities but did not
change the winning option. This is a reported measurement, not an independently
reproduced result or an official security guarantee.

- Separate code-owned operation/parameter contracts and policy from untrusted
  observation evidence, labels, file contents and summaries in the model state.
- Confidence and instructions are routing signals, not a trust boundary.
  Evidence cannot declare new tools, executable bindings, permissions, budgets,
  or successful completion. Only registered provider projections build bindings.
- Preserve code-owned argument/path/recipient validation for every route,
  including high-confidence direct choices and LLM parameter generation.
- Add tests that hostile-looking result strings remain data, cannot overwrite
  tool definitions or trusted metadata, and cannot escape parameter validation.
  Offline tests do not establish prompt-injection resistance of either model.

- [x] Clean baseline verified; branch created.
- [x] Contract recorded before implementation.
- [x] Observation and argument contracts implemented.
- [x] Compiler, both drivers, materializer, providers and replay integrated.
- [x] Offline tests and sandbox validation complete.
- [x] Requested README FastAPI six-turn replay complete and analyzed.
- [x] Supplemental procurement 10-turn runs retained and analyzed.

Record consequential deviations and actual test/run receipts below; do not
silently relax the invariants to improve benchmark results.

### Implementation decisions and validation receipts

- Canonical file tools: scoped LIST_FILES, ranged READ_FILE (zero-based lines),
  bounded SEARCH_FILES, WRITE_FILE with separate path/content, and raw BASH.
  LIST_FILES offers declared root defaults even before an observation exists.
- Bindings copy a compatible observed path plus tool-owned defaults. Criteria
  display the resulting arguments, including read offset/limit. Partial bindings
  are locked while the LLM authors missing fields. Weak binding confidence falls
  back to parameter authoring; weak operation confidence uses arbitration.
- Observation window: 4 views, 3500 characters/view, 6000 characters combined;
  at most 20 file and 8 directory references, scoped to the isolated session's
  sandbox. Cross-session reference sharing is unsupported. Views retain paging,
  scan-budget and provenance metadata; raw output never declares trusted kinds.
- Invocation-wide ceilings: 128 question heads, 65536 question characters,
  262144 UTF-8 bytes for the complete Jev request. Oversize requests fail closed
  before transport, rather than silently cutting the user goal or tool contracts.
- Existing Lark tools use the schema adapter and retain closed target validation
  plus recipient authorization. Future canonical business tools must explicitly
  declare closed-reference validators; shortcut pools are not authorization.
- Runtime freezes independent argument copies, canonicalizes committed native
  calls before checkpoints, and passes an isolated decision copy to callbacks.
  Old target/text ledgers and injected legacy drivers remain supported.
  Provider normalizers must be pure/idempotent: revalidation cannot change
  committed arguments, and normalization cannot change a locked binding.
- Offline suite before the first real run: 261 tests passed; Ruff and frontend
  TypeScript checks passed. Final post-run regression receipt follows.
- Real Docker smoke passed: original 4-step kernel flow plus 6 canonical
  provider-to-container checks (scoped listing/paging, ranges, search references,
  separate write arguments, raw Bash). Image:
  `sha256:b19bfc61a4f4d9c9f60b611655f8c8bf6a3db08d552c94e65ed39d156c468587`.
  Canonical binding/replay are covered by offline kernel tests, not represented
  as being exercised by the legacy driver in that Docker kernel flow.
- Unchanged suite SHA-256:
  `1207b224441a240ede2e907e12d04e76c55b841459d606379196d5048049622b`.
  Both model credential presence checks passed without exposing their values.

### First-run finding (not hidden or retuned)

The first complete paired run is retained under
`backend/artifacts/bench/observation-views-20260922/`. It exposed mixed protocol
authoring: canonical file operations appended native tool calls while ANSWER
still used the legacy free-form content helper. Several ANSWER attempts returned
multi-parameter protocol serialization and were safely rejected before dispatch.
The implementation must route canonical ANSWER through the same locked-operation
schema helper. Legacy injected target/text drivers may keep their old adapter.
After this integration correction, rerun the whole unchanged paired suite in a
separate artifact directory; do not change routing thresholds or scoring.

### Final acceptance evidence

Implementation commit: `79ba34b`; executable runtime digest: `a6e1c6ec8d2e`.
Final offline validation: **264 passed**, Ruff passed, frontend TypeScript check
passed. Independent review verified canonical ANSWER call/result pairing and
single billing on success/refusal/truncation, binding locks and normalizer gates.

The corrected full run finished with Jev **9/10** and plain LLM **10/10**.
Jev: 40 attempts, 399.988 seconds, estimated $0.094744; plain: 40 attempts,
86.126 seconds, $0.103030. One successful observed four-file read bypassed LLM
authoring. Four transport-failed attempts must not be counted as successful
direct execution. Turn 10 answered from stale historical evidence without
reading the new offer. Thus the implementation and supplemental validation are complete,
but the design has not demonstrated performance or quality parity on that
supplemental suite; do not
merge based on an asserted speedup. See [evaluation](evaluations/observation-view-evaluation.md)
for both supplemental runs, error accounting and the next freshness/coverage
design question.

### Confirmed requested baseline: README FastAPI session

The user confirmed the README's six-turn version. This is **not** the different
six-turn `backend/benchmarks/fastapi_case.json` suite. The exact source is session
`3b6792da363e`, documented in `docs/evidence/fastapi-6turn-20260922/manifest.json`.
All six local source event logs passed the manifest SHA256 checks. The replay
script reads only whitelisted run parameters and runs the original dashboard
execution path with fresh lane volumes/ledgers and parallel lane scheduling.
It preserves max_steps=0, max_writes=0, the 0.5/0.4 routing thresholds and the
disabled progress gate. A 600-second per-turn external watchdog bounds the run;
the model/runtime policy is unchanged. The updated sandbox image is expected
because tool implementation is the variable under evaluation.

Replay entry point: `backend/scripts/replay_readme_fastapi.py`.
Raw new run logs and reports stay local under
`backend/artifacts/bench/readme-fastapi-observations-20260922/`.

Both lanes completed and answered 6/6 turns. Jev: 27 steps, 179.644 seconds,
estimated $0.066160; plain: 55 steps, 210.440 seconds, $0.195624. There were
zero successful direct tool calls; 21 of 27 Jev attempts required arbitration.
This sample shows a relative step/cost advantage over its plain lane, not proof
of new direct-binding gains or improvement over the historical README Jev run.
The completion-only scoring boundary and full results are documented in
[README FastAPI replay](evaluations/readme-fastapi-observation-replay.md). Final offline
regression receipt including the replay harness: 268 tests passed, Ruff passed.
