# FastAPI derived task-family contract

Current workload contract for `fastapi-task-families-v1`. Runtime revisions and
measurements belong to individual [run reports](README.md), not the workload ID.

This is a new fixed workload derived from the README FastAPI workflow, **not**
the original six user messages or `fastapi_case.json`, and not a reproduction of
their scores. Source: `backend/benchmarks/fastapi_families.py`, suite ID
`fastapi-task-families-v1`. `suite_payload()` returns a fresh JSON-serializable
`suite` / `description` / `scenarios` object compatible with
`jevloop.evaluation.bench.load_scenarios`. It performs no execution on import.

## Workload

All three scenarios have `favor=either` and `max_steps=20` per turn. Each scenario
starts independently; each lane retains its own workspace and conversation
across the scenario's turns. Total: **6 + 6 + 8 = 20 turns per lane**.

| Family | Turns | Sequence |
| --- | --- | --- |
| `observation_reuse` | 6 | Explore project → README facts → configuration → compare full inventory → route rules → runbook |
| `generation` | 6 | Empty directory inventory → Hello World → pytest/TestClient verification → query → JSON persistence → PATCH |
| `mixed` | 8 | Explore → read baseline → add query → verify → external configuration/data change → live snapshot endpoint → runbook → current handoff document |

Observation and mixed start with the same small runnable project: `README.md`,
`app.py`, `routes/`, `config/settings.json`, `data/items.json`, `docs/runbook.md`.
Generation starts with the normal sandbox `.gitkeep` only. Seeds are injected
into both lanes before their first turn. Mixed turn 5 replaces exactly the
configuration and inventory files, and its goal explicitly announces the
operator's changes and asks for current values. Seeds and replacements are
relative paths with fixed small contents; checks and expected answers are not
injected into the agent workspace.

Goals do not select tools or insist on a fresh read when unchanged observations
would suffice. “Observation reuse” describes an opportunity, **not a guaranteed
direct-tool or zero-LLM outcome**. It is valid to reuse facts from earlier
observations where they remain current. No goal-text matching is added to the
runtime, binding compiler or parameter authoring path.

## Minimum generated interface

The derived tasks deliberately specify `app.py` exporting `app` so independent
behavior probes can import it without guessing the generated layout.

- `GET /`: status 200 and `{"message": "hello world"}`.
- `GET /items`: JSON array of unique integer `id`, nonempty `name`, nonnegative
  integer `stock`; optional case-insensitive name substring `q`, empty on no hit.
- JSON stage: default `data/items.json`, `DATA_FILE` path override, read current
  data on every query rather than retain a stale in-memory copy.
- `PATCH /items/{id}`: update stock, return the full item, persist to the selected
  data file; an unknown ID is 404 and leaves that file unchanged. Negative stock
  must produce a 4xx response (normally 422) without changing the data file.
- Mixed preserves the seeded positive `limit` / configured default limit
  semantics, applies filtering before limit, and adds `GET /snapshot` with
  `revision`, `warehouse`, `item_count`, `total_stock`. Snapshot uses all items
  regardless of pagination and current `SETTINGS_FILE` / `DATA_FILE` values.

These are endpoint contracts, not complete target source files or tool plans.
Generation's concrete output contract is necessarily disclosed; observation
answer facts are not embedded in their corresponding goals.

## Checks and freshness

Every turn includes host-defined shell probes containing Python assertions.
Commands use `shlex.quote`; a unique `FAMILY_*_OK` marker is printed only after
assertions complete. The runner **must require both exit code 0 and the marker**.
The generic bench implementation historically matched only output substrings;
using that alone does not meet this suite's acceptance contract.

API probes import `app` into a temporary project copy and use FastAPI TestClient:
no port binding or long-running server. Temporary `DATA_FILE` / `SETTINGS_FILE`
paths are installed before import. PATCH and refresh probes modify these files,
not the lane's persisted data. The temporary copy also isolates pytest caches
and ordinary relative writes made by generated tests. This is correctness-test
isolation, not a claim to contain deliberately hostile generated Python.

Checks assert returned JSON, status codes, query semantics, actual JSON disk
updates, missing-ID nonmutation and same-process refresh after external changes.
Generation's pytest turn requires actual successful test execution, not merely
source containing the word `TestClient`. Original observation seed files are
SHA256-checked after every reuse turn. Mixed protects designated configuration,
inventory and documentation at specified turns while allowing code changes.

Mixed freshness has two independent checks: turn 5 answer facts must reflect
newly injected configuration/data; turn 6 probes change temporary configuration
and data between requests to reject stale endpoint caches. Turn 8 checks the
written handoff for the new facts. Answer/document checks use literal fact
coverage, not a complete natural-language semantic judge: they do not prove
that all prose is correct or free of contradictory old statements. Likewise,
seed hashes protect those files, not a full filesystem immutability guarantee.

## Validation and reporting

`backend/tests/test_fastapi_families.py` tests JSON loading, counts, independent
payloads, injection paths/bounds, goal separation, probe compilation and unique
markers. Local positive controls execute every probe against a reference app;
negative controls include altered seed evidence, incorrect hello output,
missing search filtering, stale JSON/settings, nonpersistent PATCH, negative
stock accepted, absent
collected tests and a stale handoff. Controls verify persisted project files
are unchanged by the probes. They need installed FastAPI/httpx/pytest but no
Docker or model credentials. API controls are explicitly skipped if FastAPI or
httpx is unavailable; missing dependencies must not count as a successful
negative control. No automatic installation is performed. In an environment
with these dependencies, the API/reference controls can run independently of
the full runtime imports:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend python -m pytest \
  backend/tests/test_fastapi_families.py -q -p no:cacheprovider \
  -k 'every_probe or wrong_behavior or seed_checks or handoff or test_creation'
```

`REFERENCE_APP` and `REFERENCE_TEST` in that test module are control fixtures,
not files given to a model lane. A Docker preflight may use this same entry
point with the source mounted read-only and pytest's temporary directory
outside the source tree.

Report both lanes' turn-level completion, hard-check pass/fail, cost, elapsed
time, runtime steps, model-call kinds, direct calls and recovered failures.
Do not drop failed turns or label these new families as historical README
results. A single paired run is an observation, not a general performance claim;
do not tune the suite after observing one lane's output without versioning it.

## Execution protocol

Runner: `backend/scripts/run_fastapi_families.py`. Freeze and record the source
revision and executable digest before each experiment. Do not change prompts,
tools, thresholds, drivers or scoring during a run. The initial run used
`a22f61c` / `cdf87e8f5b6b`; that is historical provenance, not a requirement
to run every later experiment on the old runtime.

Both lanes use the same existing image, tool surface and 20-step/600-second
per-turn limits, confidence threshold 0.5, ambiguity gate 0.4, write confidence
0.6, no progress floor, and existing model configuration. Each family starts
with separate fresh volumes, ledgers and prompt-cache namespaces. Lanes run
sequentially within each turn (Jev first), and their separate containers persist
through the family. This differs from the historical README dashboard replay's
parallel lanes/per-turn container restarts; do not compare wall times as an
interchangeable baseline. TestClient tasks avoid requiring service-process
lifecycle management; that dimension is not tested here.

The runner saves a frozen JSON suite before execution and full local journals.
Failed turns are retained with no rerun or UNKNOWN-state bypass. Strict probes
run outside the agent transcript and measured agent time. Recorded costs from
interrupted turns are retained where available; any unrecorded in-flight costs
remain unknown, not zero. Volume deletion exit codes are recorded.

Trace analysis counts successful no-helper effects separately from direct
attempts. Candidate coverage is retrospective: scalar criteria from the Jev
request are matched to provider-normalized proposal arguments of successful
effects. It is not a frozen-ledger audit, a semantic necessity judge or the
theoretical maximum direct rate. It does not synthesize multi-target candidate
combinations. Raw counters must be interpreted alongside per-turn checks.

## Historical first-run preflight (2026-09-22)

Preflight: 288 host tests passed, 9 API controls explicitly skipped for missing
host FastAPI/httpx dependencies; Ruff passed. In the existing sandbox image,
all 16 non-loader suite tests passed (the loader was tested on host), including
positive and negative behavioral controls and probe nonmutation checks. No
dependencies were installed. The preflight container and its volume were removed
successfully. Image:
`sha256:b19bfc61a4f4d9c9f60b611655f8c8bf6a3db08d552c94e65ed39d156c468587`.
