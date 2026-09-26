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

Local Laya uses an optional extra in this same uv environment. It is not
installed by a plain `uv sync`, and it should not be installed into another
interpreter.

```bash
uv sync --extra laya-mlx    # Apple Silicon, package laya-mlx
uv sync --extra laya-cuda   # NVIDIA, package laya[serve]
uv run jevloop laya-serve   # MLX or CUDA, POST /v1/systemone
```

`make dev` starts this server on `127.0.0.1:8791` and sets the dashboard
process's `LAYA_BASE_URL` to it. The first launch downloads the selected
checkpoints into the uv environment's Hugging Face cache.

Normal Dashboard and CLI runs allow sandbox egress so agents can install
packages and test public services. `jevloop smoke` remains offline;
`jevloop run --offline-sandbox ...` disables egress explicitly. Containers never
receive host environment variables, credentials, home directories, or sockets.

## Image inputs

Configure `VISION_MODEL`, `VISION_MODEL_BASE_URL` and `VISION_MODEL_API_KEY` in
`backend/.env` (or the root `.env`) for a vision-capable Chat Completions model.
The dashboard accepts image attachments, and CLI supports repeated paths:

```bash
uv run jevloop run "Describe this interface" --image ./screen.png
```

`VIEW_IMAGE(source, detail)` reads sandbox files or already observed asset
references, then invokes contextual visual perception. User uploads and tool captures become
immutable assets referenced by the same transcript; only the LLM transport
materializes image bytes. Both comparison lanes receive the same initial images.
History/replay needs the retained asset directory as well as session/run records.

Jev continues to select operations for image-bearing sessions. Ordinary LLM
requests see attachment metadata and prior visual observations, not unselected
pixels. Only executing `VIEW_IMAGE` requires visual capability; uploads alone do
not. The tool restores task context and returns an observation to the normal loop.
No permanent "understood" flag prevents re-reading for a new question.
Static PNG/JPEG/WebP/GIF only, 10 MiB per image, 8 images per upload/request batch.
Vision prices are optional but must be configured explicitly for cost comparison;
unknown prices are displayed as unknown, not free. See
[multimodal contract](../docs/contracts/multimodal-evidence.md) for all limits.

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
