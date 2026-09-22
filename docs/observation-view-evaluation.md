# Observation-view refactor: implementation and paired evaluation

**Scope correction:** these procurement runs were not the user's intended
FastAPI baseline. Preserve them as supplemental evidence only; they do not
complete the requested FastAPI validation.

The subsequently confirmed and completed primary run is documented in
[README FastAPI six-turn replay](readme-fastapi-observation-replay.md).

Date: 2026-09-22. Branch: `feat/observation-view-bindings`.
Contract: [observation-view-contract.md](observation-view-contract.md).

## Scope and evidence

The implementation replaces exhaustive file enumeration/closed path choices
with bounded execution observations, optional grounded argument bindings and
locked-operation LLM parameter authoring. It does not match user text to build
candidates. Canonical schemas, validation, frozen arguments, execution policies
and ledger recovery are shared by both drivers.

Validation: 264 backend tests; Ruff; frontend TypeScript check; real Docker smoke
(original kernel flow plus six canonical provider/runtime checks). Independent
review covered frozen argument identity, recovery, typed errors, binding locks,
authoring accounting and normalizer idempotence. No tests establish resistance
to model-level prompt injection.

Both runs use the unchanged `procurement_10turn.json`, digest `1207b224441a`,
models `jev-latest` / `deepseek-chat`, escalation threshold 0.5, ambiguity gate
0.4, write minimum confidence 0.6, no answer-progress gate. Each turn injects the
same files into separate lane workspaces. Lanes execute sequentially per turn,
with distinct ledgers/cache namespaces and the same immutable Docker image.
The benchmark enables sandbox networking. Costs use the runtime's configured
price estimates, not a billing receipt. Mutable model aliases and service
availability limit exact reproducibility.

The checks are answer-keyword checks, not a full semantic procurement audit.
Passing 10 turns is not proof that every recommendation is correct. These are
two post-refactor paired runs, not a controlled before/after comparison against
the pre-refactor runtime. Do not select only the favorable run or change the
scoring, thresholds or user tasks to improve the outcome.

## First run: protocol integration defect retained

Runtime digest: `d856cdca6ee7`.
Local artifacts: `backend/artifacts/bench/observation-views-20260922/`.

| Measure | Jev | Plain LLM |
|---|---:|---:|
| Passed turns | 10/10 | 10/10 |
| Attempts/steps | 43 | 39 |
| Lane elapsed | 151.527 s | 88.262 s |
| Estimated cost | $0.112586 | $0.097603 |
| Parameter authoring calls | 4 | 0 |
| Content authoring calls | 22 | 0 |
| Arbitration calls | 16 | 0 |
| Plain decision calls | 0 | 39 |

Twelve ANSWER attempts were rejected as multi-parameter protocol serialization:
the old free-form author coexisted with the new native tool-call ledger. The
subsequent correction routes canonical ANSWER through the same locked schema
authoring mechanism. Refusals remain bounded ordinary recovery, not silent
retries. Legacy injected target/text drivers retain their compatibility path.

The first report's aggregate predates the separate parameter-authoring column;
its four calls are recovered from the preserved per-turn `helper.by_kind` data.
The reported one "direct" attempt was a transport-failed attempt without an LLM
call, not a successful direct tool execution. Routing counters count attempts;
they must not be presented as successful tool bypasses without checking effects.

## Corrected full rerun

Runtime digest: `a6e1c6ec8d2e`.
Implementation commit: `79ba34b` (the evaluation-document follow-up does not
change executable sources).
Local artifacts: `backend/artifacts/bench/observation-views-fixed-20260922/`.

| Measure | Jev | Plain LLM |
|---|---:|---:|
| Passed turns | 9/10 | 10/10 |
| Attempts/steps | 40 | 40 |
| Lane elapsed | 399.988 s | 86.126 s |
| Estimated cost | $0.094744 | $0.103030 |
| Parameter authoring calls | 5 | 0 |
| Content authoring calls | 15 | 0 |
| Arbitration calls | 15 | 0 |
| Plain decision calls | 0 | 40 |
| Jev input tokens | 351,989 | 0 |
| LLM input tokens | 725,760 | 1,122,828 |
| LLM output tokens | 8,470 | 13,319 |

| Turn | Jev steps | Plain steps | Jev seconds | Plain seconds | Checks |
|---|---:|---:|---:|---:|---|
| 1 | 3 | 4 | 13.357 | 6.027 | Both pass |
| 2 | 4 | 4 | 49.954 | 8.034 | Both pass |
| 3 | 6 | 4 | 75.543 | 6.585 | Both pass |
| 4 | 4 | 4 | 41.862 | 9.448 | Both pass |
| 5 | 5 | 4 | 58.045 | 8.173 | Both pass |
| 6 | 4 | 4 | 42.739 | 10.402 | Both pass |
| 7 | 3 | 4 | 19.057 | 9.581 | Both pass |
| 8 | 5 | 4 | 73.296 | 9.450 | Both pass |
| 9 | 5 | 4 | 19.868 | 9.909 | Both pass |
| 10 | 1 | 4 | 6.267 | 8.517 | Jev fails |

The old DSML serialization rejection disappeared in this run. There were still
eight rejected parameter proposals (seven ANSWER operation mismatches and one
READ_FILE proposal exceeding the four-path cap), plus four MODEL_UNAVAILABLE
attempts. Rejected proposals were not executed. All elapsed time is retained;
the successful Jev calls also show variable latency, with per-turn maxima up
to 22.515 seconds. These measurements do not isolate architectural overhead
from model-service latency and retry effects.

The routing summary reports five "direct" attempts, but four are transport
failures without an LLM call. Ledger effects confirm **one successful direct
READ_FILE call**, reading four observed files without LLM parameter generation.
Do not report the raw 12.5% attempt counter as a successful tool-bypass rate.

### The final-turn failure is a freshness failure, not just a keyword miss

The injected `update-10-final-offer.md` changes Atlas Cloud's annual fee to
**$98,000** while retaining its compliance qualifications. Jev's sole action
for turn 10 was ANSWER (confidence **0.79**); it performed no discovery or read.
The answer recommended **Cedar Stack at $105,000** and still priced Atlas at
$118,000. The scorer caught the absent `$98,000`, but the content also confirms
a substantively stale recommendation. Mentioning Atlas incidentally passed
the other keyword check, illustrating why these checks are not a semantic audit.

The estimated 8.0% cost reduction is **not a quality-matched efficiency gain**.
There was no step reduction, and elapsed time was 4.64 times the plain lane.
This result does not justify merging or advertising a performance improvement.

## Interpretation boundary

Generic argument support and demonstrated direct execution are implementation
properties; a higher bypass rate or better latency/cost is an empirical question.
The flexible `LLM_PARAMETERS` alternative competes with concrete bindings, and
safe operation/parameter refusals can add steps. Any proposed routing change
needs evidence distinguishing operation mistakes, parameter mistakes, missing
observations and service failures, without introducing user-text heuristics.

The next design iteration should distinguish reusable historical references
from evidence verified for the current turn, and represent task evidence
coverage/freshness explicitly in the decision state. Historical=true labels
alone did not prevent premature ANSWER here. Another research direction is
reducing speculative question expansion: independent membership heads for each
file can be costly and should be compared with coherent observation-grounded
batch bindings. These are follow-up hypotheses, not tested fixes; no blanket
"always read before answering" or task-specific filename rule was added.

The [issue #11 comment](https://github.com/typesafe-ai/skills/issues/11#issuecomment-5775252156)
reinforces the same distinction: model confidence is not a security or evidence
sufficiency guarantee. Its author has `author_association=NONE`; it is community
feedback, not a verified official TypeSafe security claim.
