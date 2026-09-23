# One transcript, independent model projections

Current implementation contract, introduced 2026-09-22. This supersedes descriptions equating the
durable transcript with the LLM-visible message list, including the original
architecture and observation-view storage/budget rules. Stable tool schemas and
complete-arguments-or-LLM_PARAMETERS routing remain unchanged.

## Boundaries

1. **One durable source.** Transcript records contain user/assistant messages,
   exact committed calls, provider evidence and runtime recovery metadata.
   Persistence and recovery read `dump()`, never a model-facing projection.
   Model display budgets must not truncate durable evidence or recovery fields.
   Provider execution/output bounds still apply; this is not unlimited capture.
2. **Independent Jev projection.** Workspace, bounded observation views and
   invocation candidates are recovered from the durable records. New results
   store provider observation facts and the runtime-owned permitted reference
   kinds, not a pre-rendered bounded Jev view. Legacy `observation_view` records
   remain readable without migration or rewriting historical artifacts.
3. **Independent LLM projection.** A dedicated deterministic projector builds
   ordinary chat/tool messages, preserving committed call arguments and pairing.
   It exposes execution disposition, errors, useful result evidence, scope and
   completeness, but not internal fingerprints, recovery identities, candidate
   structures or Jev views. Use explicit result fields, not a blacklist whose
   default leaks every newly added internal feature. Domain IDs needed by tools
   remain evidence, unlike internal execution IDs.
4. **One LLM surface.** Parameter authoring, arbitration, plain decisions and
   legacy text authoring all use the same projector. Their appended request
   instructions may differ; the historical projection does not. Canonical
   parameter, arbitration and plain requests share the tool catalog; legacy
   text authoring sends projected messages without a tools field.
5. **Stable and isolated.** Projection does not interpret the user goal, make
   model calls, query the environment, modify source records, or depend on later
   messages. Appending records preserves the previous projected prefix.
   JSON save/load reproduces both model views. Returned snapshots cannot mutate
   the durable source. Projector/version changes across software upgrades are
   not claims of byte-identical provider caches across releases.

## Evidence and compatibility

- Bash exposes one output representation; commands already exist in calls.
- Read exposes per-file content/range/failure records once; list/search retain
  observed entries or matches and paging/truncation information, not a stale
  aggregate resource pool in place of the current result.
- ANSWER text already lives in the committed call; its successful tool receipt
  need not repeat the delivered answer. Rejected/superseded/interrupted calls
  retain explicit failure/nonexecution semantics and pairing.
- Raw user/resource text is data. Do not recursively delete strings or keys
  resembling internal fields inside file content, message bodies or generic
  provider `data`. Only code-owned record envelopes define metadata.
- Legacy mixed results are adapted at read time. Unknown or malformed evidence
  cannot silently become a successful result; preserve a bounded diagnostic
  and conservative uncertainty. Recovery continues to inspect original records.
- LLM result limits apply after projection, structurally with explicit omission
  or truncation. They cannot alter Jev references, durable errors, or arguments.
  Jev limits apply only to its own projection.
- New providers use explicit evidence fields (including generic `data`) or add
  a tested result adapter. Internal record fields are not an implicit LLM API.

## Scope of the original implementation

No confidence/ambiguity changes, new tools, completion-policy changes, semantic
history summarization, shell-policy changes, benchmark retuning, paid reruns,
publishing or merging. This fixes a core boundary, not a claimed performance win.

## Acceptance

- Sentinel internal fields survive durable save/load and recovery but are absent
  from every actual LLM request path; unknown future envelope fields do not leak.
- Normal, failed, refused, superseded and UNKNOWN calls retain their semantics.
- Long evidence does not shrink to accommodate Jev metadata; LLM limits do not
  change durable records or live/restored Jev views.
- Result evidence appears once, with file paths, resource IDs and completeness
  retained; nested user data resembling internal metadata remains verbatim.
- Stable prefixes, detached snapshots and restore parity are tested. Existing
  safety, session, schema and observation tests remain applicable.
- Run offline regression, lint and a real sandbox smoke with deterministic
  model transport where available. Record actual receipts separately from
  unmeasured provider cache or performance claims.

## Historical implementation receipt (2026-09-22)

These are the checks at implementation time, not the latest suite count. Later
real-provider runs are indexed in [evaluation](../evaluation/README.md).

- Explicit `Transcript.llm_messages()` is used by all four LLM request paths;
  `messages()` is a compatibility alias, while `dump()` remains the detached
  durable source. Plain decisions clone that source before projecting.
- New records retain provider `observation` facts plus runtime-owned reference
  kinds. Jev normalizes them independently; legacy bounded views remain readable.
- Offline regression: 325 passed, 9 skipped (optional host FastAPI dependencies
  unavailable; no dependencies installed). Lint and whitespace checks passed.
- Real Docker smoke passed the existing legacy flow, canonical sandbox tools,
  full-argument generation and direct read, plus LLM metadata isolation and
  save/restore projection parity. Model transport was deterministic/mocked,
  not a paid provider run or a cache/performance measurement.
- Independent review found malformed legacy shell notes, fallback budget overflow
  and new/legacy evidence precedence issues; fixed with regression coverage.
- No thresholds, benchmark expectations, tool capabilities or historical traces
  were changed. Directory/package organization is a separate follow-up change.
