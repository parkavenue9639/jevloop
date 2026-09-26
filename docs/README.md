# Documentation

Start with the **current architecture and contracts**, not an old migration plan.
Documents are organized by purpose; experiment receipts are tied to their
recorded revision and are not a rolling statement about the latest code.

## Current design

| Document | Owns |
|---|---|
| [Architecture](architecture.md) | Runtime flow, implemented boundaries and limitations |
| [Backend layout](backend-layout.md) | Package ownership and enforced dependency directions |
| [Transcript projections](contracts/transcript-projection.md) | One durable source; independent Jev and LLM contexts |
| [Observation views](contracts/observation-views.md) | Evidence-derived references and complete invocation candidates |
| [Stable LLM tools](contracts/llm-cache.md) | Complete arguments or operation-only LLM generation; stable schemas |
| [Multimodal evidence](contracts/multimodal-evidence.md) | Immutable images and contextual VIEW_IMAGE execution inside the normal loop |

The architecture overview is a map; the focused contracts own their detailed
invariants. If a contract and implementation disagree, investigate and update
them together rather than treating an archived experiment as authority.

## Evaluation and public evidence

- [Evaluation index](evaluation/README.md): workload distinctions, latest
  recorded branch run and earlier comparison points.
- [Published FastAPI case study](evaluation/fastapi-case-study.md): the historical
  six-turn sample shown in both repository READMEs, not latest-runtime results.
- [Visuals and regeneration](assets/README.md): deterministic SVGs, provenance
  and offline checks.
- [Public evidence bundle](evidence/fastapi-6turn-20260922/): redacted trace,
  manifest and summary. Raw private run artifacts remain Git-ignored.

## History and maintenance

[Archive](archive/README.md) preserves superseded designs and earlier evaluation
receipts. Their proposed features, commands and test counts must be interpreted
at their recorded revision, not as current API or setup instructions.

When updating documentation:

- Update an existing owning contract instead of appending a competing "current"
  design. Label implementation-time test receipts with their date/revision.
- Keep workload definitions separate from immutable run observations. Link new
  results from the evaluation index; do not overwrite historical metrics.
- Archive superseded designs with a replacement link. Update incoming links in
  both READMEs and other docs; do not silently discard historical evidence.
- Keep the public evidence and asset paths stable. The support scripts
  `render_readme_panel.py`, `render_paired_loop.py` and `test_render_paired_loop.py`
  remain at the docs root so existing regeneration commands continue to work.
- Documentation follows the same [PR workflow](../CONTRIBUTING.md) as code.
