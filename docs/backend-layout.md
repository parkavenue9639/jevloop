# Backend package boundaries

Directory migration after the independent-context fix `ed74843`. This change
organizes responsibilities and imports; it does not change model routing,
thresholds, prompts, tool behavior or evaluation criteria.

```text
jevloop/
  cli.py                 stable console / python -m entry point
  config.py, paths.py    defaults and location anchors
  contracts/             tools, arguments, schemas, authored values, policy
  context/               durable transcript and independent Jev / LLM views
  decision/              Jev selection, LLM helpers, arbitration, drivers
  runtime/               execution kernel and accounting
  tools/                 sandbox / Lark providers and Lark transport adapter
  storage/               session and run persistence
  apps/                  dashboard / HTTP application
  evaluation/            benchmark runner and deterministic sandbox smoke
```

## Dependency rules

- `contracts` is model/runtime/provider independent: typed tool interfaces,
  parameter schemas, pure authored-value validation, failures and write policy.
- `context` may depend on contracts, never on model clients, execution, concrete
  providers, storage or applications. Durable records and both projections live
  together; their separate source/view boundaries remain mandatory.
- `decision` may use contracts/context/config, not the execution kernel,
  concrete tools, persistence or application entry points.
- `runtime` orchestrates decisions and context through contracts. Concrete
  providers are injected; it does not import Docker/Lark/application code.
- `tools` implements contracts and may update context. Provider-specific
  transport lives alongside providers, not in a generic root adapter package.
- `storage` persists source records, not model prompts, and does not invoke
  decisions or applications.
- `apps` assembles runtime, providers and storage. `evaluation` may reuse this
  application machinery (including the existing dashboard cache-scope helper).
  Applications do not depend on evaluation. The CLI composes these public
  workflows, not the reverse.
- Package `__init__.py` files remain inert. No eager wildcard exports or global
  module alias registry disguises obsolete dependencies.

AST import checks enforce these rules, including local imports inside functions.
The checks also reject flat legacy module imports and enforce a small root.
New features must choose an owning layer and cannot expand dependencies merely
to make an import work. Changes to these rules require architecture review.

## Compatibility and paths

- `jevloop`, `python -m jevloop.cli`, existing subcommands and dashboard endpoints
  remain unchanged. Repository scripts/tests use canonical package imports.
- Old flat Python internals are intentionally moved, not retained as dozens of
  root forwarding modules; this pre-alpha project does not promise those paths
  as a public Python API. Callers importing internals must use the new locations.
- Docker context, frontend dist, default benchmarks, artifacts, sessions and
  runtime-digest inputs retain their previous resolved locations via `paths.py`.
- Historical run receipts are not rewritten to pretend they used the new layout.
  Active source documentation is updated; old experiment commands may refer to
  old internal import paths and are interpreted at their recorded revisions.

## Validation receipt

- 357 offline tests passed, 9 skipped for unavailable optional host FastAPI
  dependencies; no dependencies installed. Ruff and whitespace checks passed.
- 32 architecture/path tests cover forbidden and allowed import forms,
  including lazy imports and the application/evaluation dependency direction.
- All 29 implementation modules import. Extracted schema and authored-value
  functions match the previous commit's AST after ignoring import locations.
- Installed CLI and `python -m jevloop.cli` entry points, benchmark listing and
  both FastAPI replay script help entry points work with the new imports.
- Real Docker smoke passed, including the two independent context projections
  and restore parity. Immutable sandbox image is unchanged:
  `sha256:b19bfc61a4f4d9c9f60b611655f8c8bf6a3db08d552c94e65ed39d156c468587`.
  Model responses were mocked; no paid benchmark or performance claim is made.
- Independent read-only review found no behavioral changes in the moves or
  extractions. Historical artifacts, credentials and machine configuration were
  not moved or included in the change.
