# Stable LLM tool contract

Current implementation contract, introduced 2026-09-22. Supersedes the
partial-binding rules in the [original observation design](../archive/observation-view-contract-20260922.md).

Context boundary: [Transcript projection contract](transcript-projection.md).
All LLM paths consume its deterministic model view, not raw recovery records.
Schema stability and context isolation are separate required invariants.

## Two routes, no partial authoring

- Jev sees bounded, evidence-derived **complete** invocation candidates and an
  unconditional `LLM_PARAMETERS` alternative for each available operation.
- A complete selected candidate can execute without an LLM after passing
  applicable confidence/recovery routing and execution-policy checks. Weak
  binding confidence may instead require full LLM parameter generation;
  operation uncertainty or recovery may require arbitration.
- `LLM_PARAMETERS` selects only the operation. The LLM generates all arguments
  from the goal and conversation evidence. No candidate list, partial binding,
  candidate enum or dynamic const is passed to its tool schema or instructions.
- Incomplete direct proposals are rejected, not silently converted into partial
  authoring. Historical execution evidence remains available in the ledger.
- Candidate construction never interprets user natural language. Canonical
  schema defaults are allowed; unobserved/incomplete values are not invented.

## Stable request surface and enforcement

- Parameter authoring, Jev arbitration and plain LLM use the same deterministically
  ordered canonical tool catalog for a provider configuration, including ANSWER
  and the fixed CANNOT_BIND control response. DONE is not an advertised action.
- The catalog is not filtered by the selected operation, candidate window or
  current availability. Actual registry/configuration changes may change it;
  cache reuse never overrides authorization or a schema update.
- Authoring specifies the selected operation only in the appended request note.
  Code rejects a changed operation, malformed/incomplete arguments, multiple
  calls and truncation. CANNOT_BIND is a billed, non-executed recoverable refusal,
  never a successful executable tool or an arbitration endorsement.
- Drivers retain allowed-action validation. The kernel checks request-time
  availability and rechecks before dispatch, in addition to parameter, recipient,
  confidence and budget checks. Catalog visibility grants no execution rights.
- Keep existing tool_choice semantics (authoring required, arbitration/plain
  auto) to isolate this change. Preserve append-only committed history, canonical
  executed arguments, recovery/UNKNOWN handling and accounting.
- Legacy injected target/text drivers remain a compatibility path; normal Jev
  and plain decisions use the canonical contract above.

## Acceptance

- WRITE_FILE references without content cannot become direct candidates;
  complete READ_FILE/default invocations still can. Batch candidates cannot
  combine synthetic controls or incomplete invocations.
- Tools are identical across authoring operations, observation changes and
  arbitration/plain requests. No candidate-derived const/enum or bound note.
- Full parameter generation may select a path absent from observations, subject
  to canonical validation and authorization. It cannot change the operation.
- Invalid calls, CANNOT_BIND, unavailable operations and permission changes cause
  no dispatch and retain usage/recovery evidence; complete direct calls use no LLM.
- Run offline regression and applicable lint checks. Schema equality alone does
  not establish a hit-rate or cost gain; provider measurements belong in
  [separate evaluation reports](../evaluation/README.md).

## Historical implementation receipt (2026-09-22)

These are the checks at implementation time, not the latest suite count.

- Offline regression: 304 passed, 9 skipped (host FastAPI dependencies absent;
  no dependencies installed). Ruff and diff whitespace checks passed.
- Real Docker smoke passed: the existing four-step legacy flow, six canonical
  provider checks, plus full-argument WRITE -> complete direct READ -> full-argument
  ANSWER through the kernel. The latter uses deterministic mocked LLM transport,
  asserts a shared tool catalog and exactly two authoring calls, and performs real
  sandbox file operations. This is not a real-model/cache performance measurement.
- Image: `sha256:b19bfc61a4f4d9c9f60b611655f8c8bf6a3db08d552c94e65ed39d156c468587`.
  Smoke-owned containers and anonymous workspace volumes are removed in cleanup;
  the shared immutable image is retained.
- Independent review accepted after fixing explicit operation-only routing,
  plain allowed-action tail notes, and malformed-default candidate rejection.
- No historical benchmark artifacts or published performance claims changed.
