# Observation views and invocation candidates

Current contract, reconciled 2026-09-23. The
[original design and implementation receipts](../archive/observation-view-contract-20260922.md)
are historical: partial parameter binding and shared source/display budgets no
longer describe the canonical path.

## Evidence becomes an optional shortcut

```text
provider result -> durable observation facts
                         |
                 bounded Jev observation view
                         |
                typed references + tool defaults
                         |
              complete invocation candidates
              OR operation + LLM_PARAMETERS
```

1. Providers emit evidence and explicit typed references. Runtime-owned permitted
   reference kinds determine which references are usable; resource output cannot
   declare new tools, permissions or trusted kinds. Arbitrary Bash stdout is
   not parsed as a listing protocol.
2. Durable records retain provider observation facts, canonical calls and
   recovery metadata. Jev's bounded view is rebuilt from those facts. Old
   `observation_view` records remain readable without rewriting old traces.
3. References carry source call, operation, scope and completeness information.
   They are historical observations, not a complete or guaranteed-current index.
   A same-operation/scope observation replaces its prior view in the bounded
   window; this does not delete records from the durable transcript.
4. Candidate construction uses observed compatible references and declared
   tool defaults. It must not match, parse or rank user natural language,
   invent missing argument values, form arbitrary parameter Cartesian products,
   or execute hidden discovery tools.
5. Only complete, canonically valid argument sets enter direct candidates.
   Compatible bounded read batches may combine targets while preserving the
   other arguments; synthetic controls and incomplete invocations cannot be
   bundled into an executable candidate.
6. Every available operation always has `LLM_PARAMETERS`, even when direct
   candidates exist. Missing candidates do not remove the tool. The LLM may
   generate a path/value absent from the window, subject to ordinary validation
   and authorization, and must generate the complete argument object.

For example, an observed file path plus declared read-range defaults can yield
a complete `READ_FILE` invocation. That same path alone cannot yield a direct
`WRITE_FILE`: content is missing, so the operation requires LLM generation.
Selecting a complete candidate is eligible for direct execution, not exempt
from confidence/recovery routing, validation or policy.

## Independent view budgets

Current Jev observation limits in
[observations.py](../../backend/jevloop/context/observations.py) are 4 views,
3,500 serialized characters per view, 6,000 across the window, and up to 20 file
and 8 directory references. These are projection limits, not limits on the
durable transcript or the LLM's independently projected results. Omitted ranges,
paging, truncation and source provenance must remain explicit; never truncate
a path into a different executable reference.

The question compiler additionally caps the entire invocation at 128 question
heads, 65,536 question characters and 262,144 UTF-8 request bytes. Oversize
requests fail before transport rather than silently cutting goals or contracts.

The [transcript projection contract](transcript-projection.md) owns source/view
separation; the [stable LLM contract](llm-cache.md) owns the tool catalog and
full-argument authoring behavior. Dynamic candidate schemas must not leak into
the LLM's canonical catalog.

## Required invariants and code ownership

- Live execution and JSON save/restore recover equivalent Jev views from the
  same source. LLM display limits must not alter Jev candidates or recovery.
- Direct, authored and arbitrated calls share canonical validation and execution
  policy. Freeze arguments before dispatch; resume must not regenerate arguments
  for an old committed call. Provider normalizers must preserve canonical values
  across revalidation.
- Open path parameters are not restricted to the shortcut pool. Closed business
  references and recipients retain their provider-specific authorization checks.
- Evidence, labels and file content remain untrusted data. Confidence is a
  routing signal, not an authorization or prompt-injection-resistance guarantee.

Implementation ownership: [observations](../../backend/jevloop/context/observations.py),
[projection](../../backend/jevloop/context/projection.py),
[argument contracts](../../backend/jevloop/contracts/arguments.py),
[question compiler](../../backend/jevloop/decision/model.py), and
[drivers](../../backend/jevloop/decision/drivers.py). Corresponding offline
regressions cover no-candidate authoring, complete direct reads, invalid/batch
candidates, malicious-looking evidence, bounded views and restore parity.
