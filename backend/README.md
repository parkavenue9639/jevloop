# JevLoop backend

Python 3.12 runtime for JevLoop. A shared `RuntimeKernel` runs Jev and plain-LLM
decision drivers, an OpenAI-compatible text model authors content, mounted tool
providers execute actions, and Docker isolates file/Bash work.

## Setup

```bash
uv sync
uv run jevloop smoke       # offline kernel + Docker, no model keys
cp env.example .env        # TYPESAFE_API_KEY and DEEPSEEK_API_KEY
uv run pytest
uv run jevloop serve --port 8790
```

Normal Dashboard and CLI runs allow sandbox egress so agents can install
packages and test public services. `jevloop smoke` remains offline;
`jevloop run --offline-sandbox ...` disables egress explicitly. Containers never
receive host environment variables, credentials, home directories, or sockets.

## Paired benchmark

```bash
uv run jevloop bench
# or from the repository root:
make bench
```

The fixed multi-turn suite in
[`benchmarks/scenarios.json`](benchmarks/scenarios.json) runs both decision lanes
with dashboard session semantics: independent ledgers, prompt-cache namespaces,
and Docker workspace volumes. Machine-checked expectations cover answers,
files, and Bash effects.

Reports land in `artifacts/bench/<stamp>/report.{json,md}` with routing, model
roles, cache-aware cost, wall-clock time, verification, and observed advantage.
Useful options:

```bash
uv run jevloop bench --list
uv run jevloop bench --only <scenario-id>
uv run jevloop bench --keep-volumes
uv run jevloop bench --no-journal
```

Each run reuses a content-addressed immutable sandbox image. Dashboard sessions
use named hashed volumes so files survive across turns; paired lanes remain
isolated. One-off CLI and smoke runs use disposable anonymous volumes. Docker is
required; there is no host-directory fallback.

See the [repository README](../README.md) and
[architecture](../docs/architecture.md) for the full design.
