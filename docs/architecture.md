# Current architecture

Implementation overview, checked against `937e002` on 2026-09-23. This describes
the code that exists, not the original M1–M5 roadmap. The annotated original
plan is retained in the [archive](archive/architecture-20260921.md).

## One source, two decision strategies

JevLoop runs a Jev-driven agent and a plain LLM agent through the same execution
kernel. Jev chooses operations and optional complete argument candidates; the
LLM generates arguments/content or arbitrates when required. Engineering owns
validation, authorization, dispatch, durability and model-context projections.

```text
                       durable Transcript
                       /                \
             Jev workspace/view       LLM chat/tool messages
             + dynamic candidates     + stable canonical tool catalog
                       \                /
                      decision proposal
                              |
                RuntimeKernel: validate and freeze
                              |
                policy / budgets / durable intent
                              |
                  provider dispatch -> result
                              |
                    append to Transcript
```

The plain lane makes its proposal through the LLM view. It does not bypass the
shared kernel or gain a different tool policy. The diagram's stable catalog
applies to canonical parameter, arbitration and plain requests; the retained
legacy text helper uses the same message projection without a tools field.

### Context and arguments

- `Transcript.dump()` is the detached durable source, including committed calls,
  provider evidence and recovery metadata. Sessions persist this source.
- `rebuild_workspace()` recovers Jev state and bounded observations from those
  records. `llm_messages()` independently creates the LLM view; it is not the
  persisted ledger. Each projection owns its own display budget.
- Candidates are deterministic projections of observed tool facts plus declared
  tool defaults. Candidate construction never mechanically interprets user
  natural language or performs hidden discovery calls.
- Every available operation retains `LLM_PARAMETERS`. It means choose the
  operation and ask the LLM for **all** arguments, not fill holes in a partially
  bound invocation. LLM requests receive stable canonical schemas, not the
  dynamic candidate set or candidate-specific schema constraints.
- A complete candidate can run without LLM assistance only after applicable
  decision-confidence, recovery and execution-policy checks. A candidate's
  existence does not imply that the tool is available or authorized.

Detailed boundaries: [projections](contracts/transcript-projection.md),
[observations](contracts/observation-views.md),
[LLM tool contract](contracts/llm-cache.md).

## Shared runtime and execution

`JevDriver` and `PlainLlmDriver` return decision proposals. `RuntimeKernel` owns
materialization, canonical argument validation, policy/budget checks, frozen
intents, provider execution, observations, accounting and termination. Providers
are injected through `ToolSpec`/`ToolProvider`; the kernel does not implement
Docker or Lark transport.

Before provider I/O, the runtime durably records intent and dispatch-started
events. Execution dispositions distinguish `SUCCEEDED`, `PLANNED`,
`NOT_APPLIED` and `UNKNOWN`. A dispatched mutation with an uncertain outcome
is not automatically relabeled as unexecuted. Scope-limited sandbox cleanup
evidence can allow continuation without erasing uncertainty; unresolved unsafe
effects block continuation. Recovery failures are bounded, not an unlimited
model-repair loop. This is not a general exactly-once guarantee or automatic
provider reconciliation service.

The source transcript and persisted run events have different roles: the former
supports conversation restore and context reconstruction; the latter supports
execution auditing, metrics and dashboard replay. Neither model projection is
a second durable conversation source.

## Tools, isolation and application profiles

- Sandbox file tools are scoped/bounded `LIST_FILES`, ranged `READ_FILE`,
  `SEARCH_FILES`, `WRITE_FILE` and open-parameter `BASH`. They execute inside
  Docker, not a host workspace directory. Bash output is evidence, not an
  implicit file-list protocol.
- Paired lanes use one immutable image with separate containers, named workspace
  volumes and transcripts. Dashboard turns recreate containers while retaining
  session workspace volumes; process state is not persisted by the volume.
- Supported application profiles are `single_live`, `single_shadow` and
  `paired_shadow`. There is no paired-live remote-write profile. Shadow Lark
  writes are planned/dry-run; sandbox-local operations still execute.
- Sandbox networking is an explicit setting; the sandbox does not receive host
  credentials or home mounts. Network-enabled shell actions can have external
  effects and must not be treated as inherently read-only.
- External write budgets, recipient validation and confidence policy remain
  code-owned checks. Model confidence and resource text do not grant permission.

## Persistence, comparison and observability

Sessions live under `backend/artifacts/sessions/`; run event streams under
`backend/artifacts/runs/`. Session checkpoints use atomic replacement, and run
events are durably appended. Dashboard replay reads recorded events without
new model calls. The frontend presents both per-turn and session aggregates.

The execution graph and conversation stay mounted together in one resizable
workspace (always side by side, default 72% graph / 28% conversation), driven by
one `useChat` subscription and one persistent composer in the conversation pane. Each pane scrolls
independently. Inspecting an older graph does not freeze the live conversation.
The conversation shows received decision milestones before a completed step;
these are presentation telemetry, not invented assistant messages or new ledger
records. Its tail follows content/pane resizing only while the reader remains
near the bottom; changing sessions resets the conversation's scroll identity.
The horizontal graph uses a readable-size floor rather than shrinking with pane
height; dense pools scroll locally and an explicit overview can fit the whole
canvas. Candidate groups use compact rows and 2–4 columns as panel width allows;
extreme pools expand downward. All submitted candidates remain present. Active nodes enlarge internally
without moving ports or reflowing branches; reduced motion removes transitions.

The console draws the single-transcript / independent-projection contract per
lane; the paired baseline has its own transcript. Jev choices are nodes inside
that flow, not a second context store. Optional `jev_request` telemetry carries
the actual compiled questions immediately before HTTP; `jev_response` carries
the original parsed choice before arbitration or weak-binding fallback.
Both are correlated by `attempt_id`. `decision_ready` identifies the final
proposal and its binding mode, not successful tool execution.
`llm_started` / `llm_completed` bracket the actual arbitration, parameter and
text-authoring calls, including arbitration before `decision_ready`. A returned
helper is not yet validated or committed. The UI renders the request's complete
choice pool before a response, retains it while highlighting consumed branches,
and shows the Jev-to-LLM handoff without waiting for a completed `step`.

The graph has two writeback routes: accepted calls/authored progress before
dispatch, and execution/refusal evidence afterward. An `intent` follows the
pre-dispatch checkpoint; a raw Jev response is not a ledger commit. Run-limit
bookkeeping is not drawn as a newly appended tool result. The Jev workspace is
an incrementally maintained projection, not a per-call reload from disk.

These events never enter the model-facing transcript, and optional presentation
observer exceptions do not alter a decision. Durable event-sink failures still
stop execution; a failed append locks that journal against further writes because
its final record may already have reached disk. A new attempt clears the display's previous candidates;
after a step is recorded, the UI reads its original Jev model-call evidence.
Older runs without these events remain replayable but cannot show a pre-response
candidate preview. Missing probabilities, arbitration state and selections stay
unknown. Historical, paused, disconnected and terminal views do not animate as
live execution. Frontend selector/reducer checks run with
`pnpm --filter jevloop-web test` (Node 22.6+); the production build also type-checks.
With Vite running, `/tests/flow-preview.html` is an explicitly labeled offline
fixture: manual gates hold the request, original choice, LLM handoff, return and
commit stages for visual inspection without model calls or run-data writes.

Recorded calls distinguish Jev decisions, plain decisions, parameter authoring,
content authoring and arbitration. Reports separate provider usage, cache
hits/misses/unknown tokens and estimated cost. A failed call without usage
receipts is unknown cost, not free. Direct-attempt counters are not evidence
of successful LLM-free execution.

Matched image, tools, policies, budgets and isolated lane cache namespaces
control comparison conditions; they do not guarantee identical trajectories,
equal work or provider-cache internals. Scheduling is runner-specific: dashboard
README replay is parallel within a turn; the derived FastAPI-family runner is
sequential. See [evaluation](evaluation/README.md) before comparing results.

## Code ownership and validation

Use [backend layout](backend-layout.md) for the enforced dependency graph.
Principal entry points:

| Concern | Source |
|---|---|
| Shared loop | [runtime/kernel.py](../backend/jevloop/runtime/kernel.py) |
| Decision strategies | [decision/drivers.py](../backend/jevloop/decision/drivers.py) |
| Jev question compilation and validation | [decision/model.py](../backend/jevloop/decision/model.py) |
| Durable source and both projections | [context/](../backend/jevloop/context/) |
| Tool/argument/policy contracts | [contracts/](../backend/jevloop/contracts/) |
| Concrete providers | [tools/](../backend/jevloop/tools/) |
| Persistence | [storage/](../backend/jevloop/storage/) |
| Dashboard/API assembly | [apps/server.py](../backend/jevloop/apps/server.py) |
| Evaluation and smoke | [evaluation/](../backend/jevloop/evaluation/) |

Architecture/import, projection/restore, schema, kernel/policy, sandbox and
replay tests live in [backend/tests](../backend/tests/). Historical test counts
belong to dated implementation receipts, not a permanent architecture claim.
The [development commands](../README.md#development) remain the setup entry point.

## Not implementation guarantees

The old roadmap's generic replay driver, declarative per-tool verification,
automatic restart reconciliation, stateful Lark simulator and frozen experiment
profile registry are not promised by this overview. Benchmark post-turn checks
are not a general runtime verification layer. New work needs an explicit current
contract; archived migration phases are not an instruction to implement it.
