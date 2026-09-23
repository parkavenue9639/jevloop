# Documentation archive

Archived on 2026-09-23 to separate current contracts from superseded proposals
and historical run receipts. No original experiment metrics or public evidence
were deleted. Archive status does not mean the recorded experiment is invalid;
it means the document is not today's implementation authority.

## Superseded design documents

| Historical document | Why archived | Current replacement |
|---|---|---|
| [Original architecture / M1–M5 plan](architecture-20260921.md) | Mixes the original 2026-09-21 design, incremental annotations and unimplemented targets | [Current architecture](../architecture.md), [backend layout](../backend-layout.md) |
| [Original observation-binding contract](observation-view-contract-20260922.md) | Partial binding and shared source/display budgets were superseded | [Observation views](../contracts/observation-views.md), [stable LLM tools](../contracts/llm-cache.md), [independent projections](../contracts/transcript-projection.md) |

Bodies are retained as historical records, with archive notices and repaired
navigation links. Old symbol paths, future APIs, authority notes and test counts
describe their original context and are not current development instructions.

## Earlier evaluations

`evaluations/` holds six earlier reports. The [evaluation index](../evaluation/README.md#earlier-experiments)
orders them by change and distinguishes the procurement supplement, README
session and derived task families. Use the revision recorded inside each report
when interpreting its commands or measurements.

The [published README case study](../evaluation/fastapi-case-study.md) remains
outside this archive because it is the live documentation source for published
visuals. It is explicitly labeled as historical evidence, not latest-runtime
performance. The [latest recorded branch report](../evaluation/fastapi-projection-results.md)
is separate from it.
