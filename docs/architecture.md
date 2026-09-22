# Architecture

Current parameter-binding extension: [Observation views contract](observation-view-contract.md).
It supersedes the older target-enumeration/text-authoring split where they differ:
all tools expose canonical schemas, observation-grounded shortcuts are optional,
and every operation retains an LLM parameter-authoring path.

Status: **design of record**, agreed 2026-09-21. Section 2 describes the code as
merged on that date (the containerized sandbox, Phase M1, is landing in the same
changeset and is marked accordingly). Sections 3–7 specify the target the
migration converges to; every target concept is anchored to a concrete file,
symbol or contract that exists today or is specified here exactly. Paths are
relative to the repository root.

---

## 1. What this system is

A general agent-loop framework in which a fast, calibrated decision model
(TypeSafe Jev) picks every step, an LLM (DeepSeek) authors content and
adjudicates when Jev signals low confidence, and engineering owns observation,
execution and safety. One append-only conversation ledger is the single source
of truth; every model's context is recovered from it. A comparison harness
races two decision strategies over otherwise identical machinery, so the only
variable is **who decides**.

```text
ExperimentProfile
      │ (one immutable sandbox image, per-lane containers, budgets, lark mode)
      ▼
RuntimeKernel ─── DecisionDriver.decide(ledger, workspace, catalog) ───► Intent
      │  guardrails        Budget, WritePolicy, repeat/stuck guards (fail-closed)
      │  text authoring    text_helper on the ledger, or a genuine LLM turn
      │  execution         CompositeProvider(SandboxTools → container, LarkTools → adapter)
      │  durability        intent → effect → verification records (runstore JSONL)
      │  ledger            Transcript (LLM view) + Workspace (Jev projection, recovered)
      └  events            RunState → SSE stream + artifacts/runs/<run_id>.jsonl
```

Design invariants (carried from the current code, never relaxed by the target):

1. **Ledger as single truth.** The `Transcript` (backend/jevloop/transcript.py)
   is the LLM-visible record; the Jev view (`Workspace`,
   backend/jevloop/state.py) is a projection rebuilt by
   `projection.rebuild_workspace`, never persisted separately.
2. **Tool-agnostic core.** The loop, guardrails and escalation layer hold zero
   tool knowledge; the action catalog comes exclusively from mounted
   `ToolSpec`s (backend/jevloop/tools/base.py).
3. **Fail-closed writes.** Writes are refuse-by-default; confidence gates,
   budgets and recipient allowlists are enforced before any effect
   (backend/jevloop/guardrails.py).
4. **Calibration evidence.** Every decision's full distribution, and every
   (Jev distribution, LLM pick) pair, is recorded
   (backend/jevloop/metrics.py `RunMetrics.escalate`).

---

## 2. Currently implemented slice

### 2.1 Component map

| Concern | Symbol | File |
| --- | --- | --- |
| Shared execution loop | `RuntimeKernel`, `RuntimeKernel.run` | backend/jevloop/kernel.py |
| Decision strategies | `JevDriver`, `PlainLlmDriver`, `DriverProposal` | backend/jevloop/drivers.py |
| Jev client, question compiler, answer validation | `choose`, `compile_questions`, `validate_choice`, `action_catalog` | backend/jevloop/model.py |
| Question texts (core actions, progress rules, target preamble) | `CORE_ACTIONS`, `PROGRESS_RULES`, `TARGET_PREAMBLE`, `ANSWER_TEXT` | backend/jevloop/questions.py |
| Ledger | `Transcript` (`append_action` / `append_result` / `append_note` / `append_assistant` / `repair`) | backend/jevloop/transcript.py |
| Projection (ledger ↔ workspace) | `record_execution`, `rebuild_workspace`, `_enrich` | backend/jevloop/projection.py |
| Decision-layer state, candidate pools | `Workspace`, `PoolEntry`, `ChatRef`, `DocRef`, `POOL_NAMES` | backend/jevloop/state.py |
| Guardrails | `review`, `Budget`, `WritePolicy`, `GuardrailDenied` | backend/jevloop/guardrails.py |
| Escalation (path A) | `should_escalate`, `arbitrate` | backend/jevloop/escalation.py |
| LLM text authoring | `generate_text` | backend/jevloop/text_helper.py |
| Tool surface | `ToolSpec`, `ToolProvider`, `ToolContext`, `CompositeProvider` | backend/jevloop/tools/base.py |
| Sandbox tools (LIST/READ/WRITE_FILE, BASH) | `SandboxTools`, `SPECS` | backend/jevloop/tools/sandbox.py |
| Lark tools (9 Feishu actions) | `LarkTools`, `SPECS` | backend/jevloop/tools/lark.py |
| lark-cli transport, envelope contract | `LarkAdapter`, `run`, `ConfirmationRequired` | backend/jevloop/adapter/lark_cli.py |
| Dashboard server, lanes, SSE, replay | `Dashboard`, `RunState`, `serve` | backend/jevloop/server.py |
| Run event persistence | `append`, `load`, `list_runs` | backend/jevloop/runstore.py |
| Session persistence | `save`, `load`, `new_session_id` | backend/jevloop/sessions.py |
| Metrics / cost accounting | `RunMetrics`, `summary` | backend/jevloop/metrics.py |
| CLI | `jevloop run`, `jevloop serve` | backend/jevloop/cli.py |
| Dashboard UI (chat, paired turns, aggregate, replay) | frontend/src (`App.tsx`, `components/PairedTurn.tsx`, `SessionSummaryPanel.tsx`, `RunHistory.tsx`) | frontend/src |

Offline tests anchor each behavior: backend/tests/test_kernel.py (shared loop,
budgets, pause/abort and denied-call ledger repair), test_compiler.py (question
compilation), test_validate_choice.py (answer validation), test_escalation.py
(side-effect-free arbitration), test_guardrails.py, test_projection.py,
test_atomic.py, test_compare_runtime.py, test_envelope.py and test_runstore.py.

### 2.2 The comparison harness today

`Dashboard.start_run(params)` requires one tagged execution profile:
`single_live`, `single_shadow`, or `paired_shadow`. A paired run creates two
`RuntimeKernel` instances over different `DecisionDriver`s but the same
provider composition, policy, requested budgets and immutable sandbox image.
Each lane owns a private container. The old independent `compare` and `live`
booleans are not accepted.

| # | Status | Remaining asymmetry / hazard | Where |
| --- | --- | --- | --- |
| A1 | Closed | Both drivers execute one proposal per shared-kernel step and consume the same requested step/write budgets | kernel.py; drivers.py |
| A2 | Closed | Both lanes start distinct containers from one image; no mutable workspace is shared | server.py `_execute_async`; tools/sandbox.py |
| A3 | Closed | The same kernel `before_step` gate handles pause and abort for both lanes | server.py `_run_lane`; kernel.py `run` |
| A4 | Closed for implemented profiles | There is no paired-live profile; `paired_shadow` passes `live=False` to both Lark providers | server.py `RUN_PROFILES`, `_remote_live` |
| A5 | Partially closed | Sessions are atomically checkpointed before model work, after text materialization and after each step; automatic reconciliation of `DispatchStarted` without an effect remains M3 target work | kernel.py hooks; sessions.py; runstore.py |

### 2.3 Phase M1 (this changeset): containerized sandbox

M1 replaces the host-directory sandbox with Docker execution. It is specified
in full in §3.6 and lands as:

- backend/docker/sandbox/Dockerfile — the image recipe (non-root user,
  `/workspace`, the in-image helper).
- backend/docker/sandbox/sandboxfs.py — argv-driven list/read/write helper
  executed inside the container.
- backend/docker/sandbox/seed/ — the default (empty) seed tree baked into
  `/workspace`.
- backend/jevloop/tools/sandbox.py — rewritten onto
  `DockerSandboxImage` / `DockerSandboxContainer`; the module-global
  `SANDBOX_DIR` and the `JEVLOOP_SANDBOX` environment variable are deleted
  (clean cutover; no host-directory fallback).
- backend/tests/test_atomic.py — rewritten against a fake container runtime
  (CI does not require Docker).

M1 and M2 are implemented, together with the tagged profile gateway. Durable
effect recovery, typed slots, a stateful Lark simulator and outcome reports
remain target design.

---

## 3. Target design

### 3.1 RuntimeKernel and DecisionDriver variants

The execution loop is implemented once in `RuntimeKernel`; Jev and the plain
LLM implement the same decision-only contract:

```python
class DecisionDriver:
    name: str
    async def decide(self, context: DriverContext) -> DriverProposal
```

```python
class RuntimeKernel:
    def __init__(self, driver, provider, policy, *, live=False,
                 max_steps=30, max_writes=None, metrics=None,
                 transcript=None, workspace=None)
    async def run(self, goal, before_step=None)
```

The kernel exclusively owns transcript/workspace mutation, guardrail review,
budgets, text materialization, provider dispatch, execution recording and run
status. Run control has only `running`, `completed` and `stopped`; recoverable
step failures remain observations and do not terminate the run. The stuck-loop
guard refuses a third identical materialized intent only when the preceding two
observations were also identical. Drivers receive read access through
`DriverContext` and return exactly one `DriverProposal`; they cannot execute a
tool or spend a budget.

| Driver | Decision behavior |
| --- | --- |
| `JevDriver` | One Jev request over compiled questions; low-confidence arbitration returns a proposal plus transcript turns, which the kernel commits |
| `PlainLlmDriver` | One genuine LLM function-calling request with `parallel_tool_calls=false`; if a provider still returns multiple calls, only the first becomes the proposal |
| `ReplayDriver` | Target work: serve proposals from recorded events without model calls |

`escalation.arbitrate` is now side-effect-free: it returns the confidence note
and assistant message but never mutates `Transcript`. This prevents an invalid
or denied model call from leaving an unanswered tool call. For a committed
assistant tool call, the kernel always appends a tool result for success,
denial, abort, or tool failure. `agent.py` and `baseline.py` were removed;
server.py uses one `_run_lane` implementation for both drivers, while CLI and
the live-demo script construct the same kernel directly.

### 3.2 Typed slots and the Jev Choice / Noul / Score phases

An **Intent** is a set of typed slots; each slot type fixes who may fill it and
how it is validated:

| Slot | Type | Filled by | Validation |
| --- | --- | --- | --- |
| `operation` | Choice | Jev (or LLM on escalation) | `validate_choice`: legal argmax over a complete distribution (model.py) |
| `target` | Choice (optional) | Jev head / LLM | `validate_choice` over the head's criteria; resolution via `Workspace.find_entry` and folded keys (`target_extra`, e.g. `NEW`) |
| `text` / `answer` | Authored | LLM only (`generate_text`, or content returned with an arbitrated/plain proposal and consumed by `RuntimeKernel._dispatch`) | `text_helper` bounds: non-empty, ≤ 20000 chars |
| control (`DONE`, `BLOCKED`, `ANSWER`) | Choice | Jev / LLM | terminal semantics in the kernel |

The Jev wire protocol (one request, parallel typed questions —
`{"model", "state", "questions"}` as in `model.choose`) gains two question
types beside today's `choice`; the request stays single-round:

- **Choice phase** (`type: "choice"`). Categorical picks: the `operation`
  question from `action_catalog`, plus one speculative target head per
  distinct `(pool, filter, extra)` signature from `compile_questions`.
  Validation is exactly today's `validate_choice` contract: the chosen key is
  in the criteria, the distribution is complete, finite, in [0, 1], sums to
  ≈1, and is consistent with its own argmax. Unusable answers raise before any
  action executes (`"Invalid Jev response; no action executed."`).

- **Noul phase** (`type: "noul"`). A Noul asks one explicit yes/no
  proposition and returns `noul = P(yes)` in `[0, 1]`; it has neither a
  `confidence` field nor a probability map. It is therefore used for bounded
  gates such as `material_sufficient`, `observation_contains_untrusted_instruction`
  and `evidence_demonstrates_requirement`. Routing uses two directional
  thresholds: a clear no, a clear yes, and an abstention band around `0.5`
  that gathers evidence or escalates. None-of-the-above for a Choice remains
  an explicit Choice criterion when the catalog needs it; it is not a special
  Noul value.

- **Score phase** (`type: "score"`). A Score has 2–10 ordered textual levels
  and returns a probability distribution, confidence, legend, and expected
  level index `score = Σ(index × probability)`. Its numeric range is
  `0..len(levels)-1`, not a generic `[0,1]`. Suitable uses are ordered evidence
  coverage or risk severity. `validate_score` checks the returned legend and
  probability keys against the requested levels, finite normalized
  probabilities, score range, and agreement with the weighted sum. Routing
  retains both confidence and tail probabilities; equal means with different
  distributions are not treated as equivalent. Scores are evidence only and
  never grant authorization.

All three phases' raw answers land in the decision record
(`operation_probabilities`, `target_probabilities`, noul and score fields),
which flows into `RunMetrics` and the run's JSONL — the calibration ledger
grows from (distribution, LLM pick) pairs to (distribution, noul, score,
effect, verification) tuples.

### 3.3 Durable intent → effect → verification lifecycle

The implemented M3 slice records a durable write-ahead sequence in the same
JSONL stream used by dashboard replay:

```jsonc
{"type": "intent", "intent_id": "…", "lane": "jev",
 "operation": "WRITE_FILE", "target": "NEW", "write": false,
 "workspace_mutation": true, "text_sha256": "…", "text_length": 1374}
{"type": "dispatch_started", "intent_id": "…", "operation": "WRITE_FILE"}
{"type": "effect", "intent_id": "…", "disposition": "SUCCEEDED",
 "outcome": {"status": "ready", "action": "write_file(…)"}}
```

Current invariants:

1. Text is generated, normalized and frozen before `intent`; policy, pause and
   dispatch all observe that exact payload. `intent` must fsync successfully
   before `dispatch_started`, and `dispatch_started` must fsync before I/O.
2. Every intent receives a stable idempotency key. Lark send/reply receive that
   key from `ToolContext`; adapters cannot generate a new retry key.
3. Effect dispositions are distinct: `SUCCEEDED`, `PLANNED`, `NOT_APPLIED`
   and `UNKNOWN`. A dispatched mutation that fails remains `UNKNOWN`; a
   confirmed-ended sandbox command may carry a scoped runtime resolution that
   permits inspection/repair without relabeling the effect.
4. Single-lane sessions use atomic temp-write/fsync/replace checkpoints before
   model work, after text materialization, after each step and at finalization.
   Existing corrupt session files raise instead of becoming fresh sessions.
5. Run event append fsyncs and raises on failure. The runtime performs no tool
   call when intent persistence fails. Replay tolerates only a torn final JSONL
   line; a corrupt middle record or sequence gap fails closed.

Still target work: declarative `ToolSpec` postconditions, independent
`verification` events, and restart-time reconciliation of a durable
`DispatchStarted` that has no corresponding effect.

### 3.4 Crash recovery

Recovery is replay of the durable records, run at kernel start whenever a run
directory has a `meta` event but no `done` event (the same "unfinished"
detection `runstore.list_runs` already computes):

1. **Torn tail**: discard a trailing partial line (runstore.py `load` already
   skips unparseable JSON lines).
2. **Group by `intent_id`**:
   - intent + effect + verification → replay into the restored ledger (the
     ledger is reconstructed from the effect records exactly as
     `record_execution` wrote them; no re-execution).
   - intent + effect, no verification → re-run the machine check only (safe:
     it observes, never mutates).
   - intent only, with no durable dispatch marker → execution was never
     authorized; recovery may revalidate policy/preconditions and dispatch.
   - `DispatchStarted` with no result → **UNKNOWN**. The process may have died
     before or after the external effect. Recovery must call the tool's
     read-only reconcile contract and must never retry blindly.
   - sandbox effects: dashboard sessions retain a named workspace volume, so
     reconciliation can inspect the exact path after container restart.
     One-off anonymous-volume runs remain unrecoverable once teardown removes
     the volume and therefore stay UNKNOWN.
   - live Lark sends/replies: reuse the stable idempotency key bound to the
     intent and reconcile through a provider receipt/read API when one exists.
     Create/append operations without a proven queryable receipt remain
     UNKNOWN and require operator handling.
3. **Ledger protocol projection**: `Transcript.repair()` may still produce a
   provider-compatible tool response, but it must render the true
   `NOT_APPLIED` or `UNKNOWN` state. It may not turn a dispatched effect into
   “aborted before execution.”
4. Unresolved external or cleanup-uncertain `UNKNOWN` effects block automatic
   continuation. A sandbox command whose process group ended and whose lane
   container is still responsive keeps `UNKNOWN` as evidence but is resolved
   for continuation; it is never retried automatically.

The system does not claim general exactly-once execution. It guarantees a
durable immutable intent, single-owner dispatch, stable idempotency key where
supported, explicit effect uncertainty, and fail-closed recovery when the
runtime itself cannot confirm cleanup.

### 3.5 Experiment profiles

The implemented API already replaces the unsafe `compare`/`live` cross-product
with tagged profiles (`single_live`, `single_shadow`, `paired_shadow`) and
rejects every other value. The target replaces the remaining free-form
parameters with frozen, versioned profile objects:

```python
@dataclass(frozen=True)
class ExperimentProfile:        # backend/jevloop/profiles.py (target)
    profile_id: str             # e.g. "compare-v1"
    version: int
    drivers: tuple[str, ...]    # ("jev", "plain") or ("jev",)
    image_seed: Path | None     # seed root; its content hash enters the image tag
    lark_mode: str              # "simulator" | "shadow" | "live"  (§3.7)
    budget: BudgetSpec          # max_steps, max_writes — shared by ALL lanes
    policy: PolicySpec          # min_confidence, escalate_threshold, allowed_recipients
    fairness: FairnessSpec      # identical-image, blank-start, scheduler assertions (§3.8)
```

Profiles are code-defined in a registry keyed by `profile_id`; the run request
names a profile plus its goal. The resolved profile — including image id,
driver assignment, budgets and Lark mode — is frozen into the run's `meta`
event. `runstore.list_runs` currently derives its historical compare/live
display flags from the tagged profile while still reading old stored runs.
Validity guards:

- more than one driver ⇒ `lark_mode` is `simulator` or `shadow`;
- `lark_mode = live` ⇒ exactly one driver;
- the single-active-run guard remains.

### 3.6 Sandbox: one immutable image, two independent containers

Local execution (file and shell operations) moves from a shared host directory
into per-lane Docker containers. **Exactly one image** is built per
experiment; it seeds **two independent mutable container instances** — one per
lane. Never two images, never a shared mutable workspace.

Image and container API (implemented in M1; names are the contract):

```python
class DockerSandboxImage:                       # backend/jevloop/tools/sandbox.py
    image_id: str
    reference: str                              # "jevloop-sandbox:<sha256[:16]>"
    @classmethod
    async def build(cls, seed_root: Path | None = None) -> DockerSandboxImage
    async def close(self)                       # idempotent; docker rmi -f

class DockerSandboxContainer:                   # backend/jevloop/tools/sandbox.py
    @classmethod
    async def start(cls, image, lane_id: str,
                    workspace_key: str | None = None) -> DockerSandboxContainer
    async def list_files(self) -> list[str]     # ≤ 200 entries
    async def read_file(self, name) -> str | None   # None = missing
    async def write_file(self, name, content)
    async def run_bash(self, command, timeout: float = 30.0) -> {"exit": int, "output": str, "container_running": bool?}
    async def close(self)                       # idempotent; docker rm -f -v
```

- **Build**: `DockerSandboxImage.build(seed_root)` bakes the seed tree
  (default: backend/docker/sandbox/seed/, empty) into `/workspace` inside the
  image and tags it content-addressed (`jevloop-sandbox:<sha256[:16]>` of the
  seed content). Identical seeds ⇒ identical reference ⇒ the daemon reuses the
  same image; the comparison orchestration builds once and passes the **same
  `image_id`** to both lanes' `start`.
- **Start**: `DockerSandboxContainer.start(image, lane_id, workspace_key,
  network_enabled)` accepts an image, optional stable Session/lane key, and an
  explicit egress policy. Dashboard and normal CLI runs default to Docker
  `bridge` networking so package installs and public-service tests work;
  offline smoke and the UI/CLI offline switch use `--network none`. Named
  volumes preserve `/workspace` across turns without exposing a host path.
  Every mode keeps `--read-only --cap-drop ALL`, `no-new-privileges`,
  memory/CPU/PID limits, non-root execution and private `/tmp`; no host
  environment, home, API key or Lark credential is mounted.
- **Operations**: file ops execute the argv-driven in-image helper
  (`backend/docker/sandbox/sandboxfs.py`). Bash source is passed over stdin to
  `sandboxexec` (`backend/docker/sandbox/sandboxexec.py`), so source text never
  appears in `/proc` command lines. The helper starts `bash -s` in a dedicated
  process group and applies TERM/KILL only to that group at the stated timeout;
  output is capped at 4000 chars. Path validation — reject absolute paths,
  `..` traversal and empty names — happens host-side before Docker invocation.
- **Binding**: `SandboxTools` executes LIST/READ/WRITE_FILE and BASH inside the
  lane container regardless of `live`; `ToolContext.live` governs only Lark
  writes. Workspace mutations execute for real and are bounded by the step
  budget. Network-enabled Bash may still have external effects, so a nonzero
  exit remains `UNKNOWN`; once the command ended and Docker confirms the lane
  container is responsive, the runtime may continue to inspect or compensate
  without erasing that uncertainty. Cleanup-unconfirmed effects remain blocked.
  Sandbox-local mutations do not consume `max_writes`. The external Lark
  mutation cap is optional and defaults to unlimited (`0`/`None`); confidence
  and recipient gates remain mandatory.
  The base image preinstalls Bash, curl, FastAPI, Uvicorn, HTTPX and pytest so
  offline build/run experiments do not retry impossible network installs.
- **Teardown**: closing a container removes the process and anonymous volumes.
  Named session volumes remain for later turns; an explicit retention/garbage
  collection policy is target work and must never delete an active session.

### 3.7 Remote (Lark) execution: simulator and shadow — no paired live writes

The remote side of an experiment has three modes; the profile fixes one:

- **`simulator`** — no lark-cli at all. `LarkSimulator` (target,
  backend/jevloop/adapter/lark_simulator.py) implements the exact method
  surface `LarkTools` consumes from `LarkAdapter` (`list_chats`,
  `search_chats`, `open_chat`, `search_docs`, `open_doc`, `create_doc`,
  `write_doc`, `send_message`, `reply_message`) over an in-memory world seeded
  from a fixture file. Each lane gets its own instance seeded identically:
  identical starting worlds, independent writes. Deterministic, offline,
  CI-safe.
- **`shadow`** — real reads, isolated writes. `ShadowLarkAdapter` (target,
  subclass of `LarkAdapter`) performs read calls through the real lark-cli
  (user identity, read commands only) and forces every write method to
  `dry_run=True`, additionally capturing the exact write payload into the
  lane's own container (e.g. `lark-writes/SEND_MESSAGE-003.md` via
  `DockerSandboxContainer.write_file`) so later steps and the report can
  observe what *would* have been sent. `RunMetrics.lark` records every shadow
  write with `dry_run=True`.
- **`live`** — real reads and real writes, single lane only. This is today's
  `live=True` path (dry-run default, confirmation gate preserved: lark-cli
  exit code 10 raises `ConfirmationRequired`, never auto-bypassed).

The structural rule — **no paired live writes**: a profile with more than one
driver cannot select `live`; `Dashboard.start_run` rejects it (§3.5). Two
lanes racing on one real account could double-send or interleave
idempotency-keyed writes; the profile guard makes that state unreachable
rather than merely discouraged.

### 3.8 Fairness controls

The comparison is only meaningful if everything but the decider is identical.
Enforced controls:

1. **One image**: both lanes' containers are started from the exact same
   content-addressed `image_id`; the kernel asserts equality before the first
   step and records both ids in `meta`.
2. **No shared mutable state**: paired lanes use different named workspace
   volumes (`session--jev` and `session--baseline`) and separate session
   ledgers. Both persist across turns, but neither lane can observe the other's
   files or transcript.
3. **Identical budgets**: one `BudgetSpec` from the profile for all lanes
   (closes A1's 30/5-vs-15/3 asymmetry).
4. **Identical policy and catalog**: same `WritePolicy`, same provider
   composition and order (`CompositeProvider(SandboxTools(), LarkTools(…))` —
   mount order decides catalog priority, so it is fixed by the kernel, not the
   lane).
5. **Session parity**: a new paired session starts both lanes from the same
   image and empty branch. Later turns restore each lane's own transcript and
   named volume under the same public session id; divergence is caused by the
   drivers' earlier choices, not cross-lane contamination.
6. **Scheduler parity**: lanes run as coroutines on the single server event
   loop (server.py `serve()`), no lane priority; abort/step-pause gates apply
   to every lane through the kernel's `before_step` (closes A3).
7. **Measurement parity**: both lanes report through the same `RunMetrics`
   shape (`jev_calls` / `helper_calls` / `lark_calls`, p95 latencies, token
   cost), so the comparison table (frontend/src/components/CompareTable.tsx)
   compares like with like.
8. **Provider-cache isolation**: a paired session derives equal-length,
   lane-specific namespace hashes from `(session_id, lane)` and places the hash
   at the start of each lane's system prompt. The namespace remains stable
   across turns in that lane, preserving its own prefix-cache continuity, but
   Jev and baseline application prompts cannot warm each other's prefix.
   `meta.params` persists the requested policy and both hashes; every metrics
   snapshot records the effective policy and lane hash. A pre-migration
   transcript without its expected namespace is never rewritten and is
   reported as `legacy_shared`.

Measurement separates observed usage from interpretation:

- every LLM call is classified as `plain_decision`, `authoring`, or
  `arbitration`; these roles are aggregated independently;
- prompt input is split into provider-reported cache hits, provider-reported
  misses, and an explicit unknown remainder. When the provider omits miss
  tokens, the remainder is marked as derived rather than silently treated as a
  reported value;
- each accepted driver proposal records the LLM calls it caused. A Jev step
  with none is a `direct_jev_step`; `direct_jev_steps / jev_steps` is the
  observed LLM-avoidance rate;
- estimated cost prices cache hits, misses/unknown input, output, and Jev input
  separately using the rates emitted in the same metrics snapshot;
- no counterfactual "saved LLM cost" is fabricated from one lane. Cost
  advantage is established only by paired lanes under the controls above and
  interpreted together with their terminal outcomes.

### 3.9 Multi-turn comparison projection

Comparison is projected at two scopes from the same stored run events:

- **Turn scope**: each paired run renders its own metrics, answers, lane
  statuses and arbitration details. Historical turns remain independently
  inspectable after later turns complete.
- **Session scope**: all paired turns under the public `session_id` are folded
  continuously. Elapsed time, calls, tokens, cost, lark-cli calls, steps,
  denials and arbitration counts are summed; completion progress is reported
  as `completed/total`. Non-additive latency medians are intentionally omitted
  rather than averaged misleadingly.

Each comparison table exposes role-separated accounting before totals:
Jev tokens/cost, LLM tokens/cost/cache hits, combined totals, and Jev/LLM
cost share. LLM cached input is priced separately from cache misses. This makes
it explicit when the hybrid lane costs more because it uses both Jev decisions
and LLM authoring/arbitration; a plain-LLM lane being cheaper is then an
observable workload result rather than an unexplained aggregate.

Visually, one paired turn is one card: its turn comparison and both equal lane
panels stay together in the transcript. The Session aggregate is a compact
collapsed strip. There is no detached baseline sidebar or mobile drawer.

The active SSE stream participates in the fold, so the aggregate changes while
a turn is running. Refresh reloads every run in the newest session and applies
the same pure aggregation; no separate summary database is introduced.

### 3.10 Model-call observability

Every inference is attached to the step as a typed `model_calls` sequence.
Kinds are `jev_decision`, `plain_decision`, `arbitration`, and `authoring`.
Each record contains model id, latency, usage, the complete provider request
(`messages`, tools, instructions and limits), and the returned assistant
message or typed Jev decision. API credentials are transport arguments and are
never recorded. The dashboard renders request and response side by side under
each model call, so prompt/cache/context changes can be compared against the
resulting content or tool call.

### 3.11 Data layout


```text
backend/artifacts/
  sessions/<session_id>.json        # multi-turn ledgers (sessions.py) — unchanged
  runs/<run_id>.jsonl               # event stream (runstore.py), now including
                                    #   intent/effect/verification records
  experiments/<experiment_id>/
    profile.json                    # frozen profile + resolved image id + lane ids
    lanes/<lane_id>/ledger.jsonl    # per-lane durable ledger (incremental save)
    lanes/<lane_id>/verification/…  # per-effect check evidence
    report.json                     # verdicts, fairness assertions, metrics summary
backend/docker/sandbox/
  Dockerfile                        # image recipe
  sandboxfs.py                      # in-image file helper
  seed/                             # default seed tree
```

- The host sandbox directory (`backend/artifacts/sandbox/`) is no longer read
  or written. Dashboard workspaces live in hashed named Docker volumes so a
  session can continue across runs and browser refreshes; Jev/plain branches
  have different volumes. One-off CLI/smoke containers use anonymous volumes.
- Images are short-lived. Named workspace volumes and atomic session ledgers
  are the persistent session state; retention cleanup remains explicit target
  work.
- Replay: `runs/<run_id>.jsonl` remains the dashboard's replay source
  (frontend/src/components/RunHistory.tsx) — the added event types are
  additive, and old files replay unchanged.

---

## 4. Relation to the framework philosophy

The target sharpens the existing split of responsibilities, it does not change
it: Jev keeps choosing (Choice/Noul/Score are all Jev-side evidence); the LLM
keeps authoring (`Authored` slots stay LLM-only) and adjudicating
(`JevDriver` escalation is unchanged path A); engineering keeps owning the
ledger, guardrails, durability and the environment (kernel, containers,
verification clauses, profiles). One ledger, one kernel, one image, two
lanes — the comparison isolates exactly the variable it claims to isolate.

---

## 5. Migration plan

Phases land in order; each is a clean cutover. M1 and M2 are implemented.
M3 now has durable intent/dispatch/effect events and atomic incremental session
checkpoints; verification and automatic reconciliation remain open.

| Phase | Scope | Cuts over | Exit state |
| --- | --- | --- | --- |
| **M1 — implemented** Containerized sandbox | Dockerfile/sandboxfs/seed; DockerSandboxImage/DockerSandboxContainer; remove host sandbox runtime; paired path builds one image and starts two containers; fake-runtime tests | host-directory sandbox | Docker smoke and isolation criteria pass |
| **M2 — implemented** Kernel unification | `RuntimeKernel` owns the loop; `JevDriver` and `PlainLlmDriver` return one proposal; shared pause/cancel, budget, policy, dispatch and ledger-result handling; run termination is separate from recoverable step dispositions; repeat detection fingerprints materialized intents and observations; old Agent/BaselineAgent loops deleted | the two loops | Same event shapes; A1/A3 closed; all callers migrated |
| **M3 — partial** Durable lifecycle + recovery | Implemented: intent/dispatch/effect events, fsync event store, atomic incremental sessions, stable send/reply idempotency, explicit UNKNOWN. Target: `ToolSpec` verification and recovery reconciliation | save-at-run-end | Audit failure prevents execution; automatic restart recovery still open |
| **M4** Typed slots: Noul + Score | compiler emits bounded Noul/Score questions and typed slots; validators and calibration fields | target/text overloading | Correct wire semantics and independent completion verification |
| **M5 — partial** Profiles + Lark modes + report | Tagged profiles and no-paired-live guard are implemented; target adds frozen profile objects, `LarkSimulator`, captured shadow plans, experiments layout and outcome report | ad-hoc params | Remote fairness and outcome-oracle criteria pass |

Ordering rationale: M1 removes the shared mutable host state that would
otherwise contaminate every later measurement; M2 makes "identical machinery"
structural before durability (M3) and protocol extensions (M4) multiply the
surface; M5 depends on profiles existing to express the mode guards.

---

## 6. Acceptance criteria

Each criterion is observable behavior; where a test file anchors it today, it
is named.

**Sandbox isolation (M1)**

- In a paired run, `sandbox_ready` records one image id and both lane names;
  a file written in lane A never appears in lane B's `list_files`.
- Network-enabled mode can reach a public HTTPS endpoint and install a package;
  offline mode rejects the same request with `--network none`. Both modes keep
  a read-only rootfs, non-root UID, no host credentials/environment and no
  cross-lane workspace access.
- Host-side path validation rejects absolute, `..`-traversal and empty names
  before any docker invocation (anchored by the rewritten
  backend/tests/test_atomic.py).
- Sandbox ops execute with `live=False` (file exists afterwards in the
  container); Lark writes stay dry with `live=False` and execute only with
  `live=True` (single lane).

**Fairness (M2, M5)**

- Both lanes' `meta` shows identical `max_steps` / `max_writes` /
  `min_confidence` / image id; abort stops every lane; both lanes' metrics
  summaries share the same shape and cost model.
- A fresh compare run starts both lanes from an empty workspace (no files, no
  chats) unless the profile seeds an image.

**Durability and recovery (M3)**

- Implemented: every dispatched action records `intent → dispatch_started →
  effect`; guardrail denial records `intent → NOT_APPLIED`; a write transport
  exception records `UNKNOWN`.
- Intent persistence failure prevents provider execution. Run logs tolerate
  only a torn final line; corruption or sequence gaps fail closed.
- Sessions are atomically checkpointed before model work, after payload
  materialization, after each step and at finalization.
- Send/reply idempotency keys are derived once per intent and supplied by the
  kernel; the adapter refuses calls without one.
- Remaining acceptance: on restart, reconcile a durable `dispatch_started`
  without effect and add independent verification events. Until implemented,
  such runs stay visibly unfinished and must not be auto-replayed.

**Remote modes (M5)**

- `simulator` runs produce zero lark-cli invocations (`RunMetrics.lark_calls`
  empty) and are deterministic across repeated runs of the same profile.
- `shadow` runs perform only read lark-cli commands; every captured write
  exists in the lane container under `lark-writes/` and is recorded with
  `dry_run=True`.
- `start_run` rejects a profile pairing multiple drivers with `lark_mode=
  "live"` (and vice versa); no code path can issue paired live writes.

**Protocol (M4)**

- Noul responses contain only a finite `noul` probability in `[0,1]`; the
  abstention band routes to gather/escalate and no code reads a Noul
  `confidence`.
- Score responses match their 2–10 ordered levels, normalized distribution,
  legend and weighted expected index; out-of-range or inconsistent responses
  fail closed. One Jev request may still carry Choice, Noul and Score together.

**Global**

- The offline suite (backend/tests) passes without Docker, network, or API
  keys; lark-cli envelope behavior remains anchored by test_envelope.py;
  guardrail fail-closed behavior by test_guardrails.py; escalation by
  test_escalation.py.

---

## 7. Symbol map (current → target)

| Current | Target | Phase |
| --- | --- | --- |
| `SandboxTools` on host `SANDBOX_DIR` (tools/sandbox.py) | `SandboxTools` bound to an injected `DockerSandboxContainer` | M1 |
| `_confined` host path check | host-side path validation before docker invocation | M1 |
| Removed `Agent` + `BaselineAgent` loops | `RuntimeKernel` + `JevDriver` / `PlainLlmDriver`; `ReplayDriver` remains target work | M2 |
| Removed server `_run_jev_lane` / `_run_baseline_lane` | one `_run_lane` constructing the selected driver | M2 |
| Removed `Agent._refuse_repeats` | shared `RuntimeKernel._refuse_repeats`; future progress score augments it | M2/M4 |
| runstore `step` events, `sessions.save` at run end | intent/effect/verification records + incremental save + recovery replay | M3 |
| `_enrich` per-operation extraction (projection.py) | declarative `ToolSpec` verify clauses executed by the kernel | M3 |
| `Dashboard.start_run(params)` free-form dict | frozen `ExperimentProfile` registry + validity guards | M5 |
| `LarkAdapter` + per-call `dry_run` (compare hazard A4) | `LarkSimulator` / `ShadowLarkAdapter` / single-lane `live` | M5 |
| shared `artifacts/sandbox/` host dir | per-lane container volumes; `artifacts/experiments/<id>/` records | M1/M5 |
| `meta.params` (runstore) | frozen `profile.json` in meta + `experiments/<id>/profile.json` | M5 |

Unchanged by design: `Transcript` and its append-only invariants, `Workspace`
and pools, `compile_questions`' signature-deduplicated heads,
`validate_choice`, guardrails (`Budget`, `WritePolicy`, `review`),
`generate_text`, the lark-cli envelope contract, `RunState`/SSE replay, and
the frontend event contract.
