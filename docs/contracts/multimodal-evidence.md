# Multimodal evidence contract

Current contract, revised 2026-09-26 after live validation and user review.
Supersedes the initial whole-session visual-decision bypass. One transcript,
independent projections, fixed tool schemas and the normal decision loop remain.

## Availability is not perception or routing authority

- User uploads and successful tool results carry explicit typed `images` parts:
  `type`, `asset_id`, `mime_type`, `width`, `height`, `detail`, `name`. Capture and
  durably persist immutable content-addressed assets before referencing them.
  Preserve original bytes and the normalized rendition. Paths are not identity.
- Uploaded images and structured sandbox file references are optional sources
  for the same `VIEW_IMAGE` tool. An attachment does not bypass Jev, require a
  vision model at admission time, or assert the task needs visual perception.
- Every Jev-lane decision still goes through Jev and existing arbitration rules.
  Jev uses the goal, full projected context, available references and previous
  visual observations to decide whether image contents are needed. This includes
  visual background context necessary for a task without an explicit image-reading
  instruction. No natural-language keyword matching or filename heuristics.
- Stable `VIEW_IMAGE(source, detail=auto)` supports complete observed bindings
  and the existing `LLM_PARAMETERS` option for full argument generation.

## Contextual visual reading is a tool execution

1. Jev chooses an operation and optional complete binding; ordinary argument
   authoring/arbitration remains unchanged. Baseline uses its normal LLM driver.
2. `VIEW_IMAGE` safely captures a sandbox-relative image or resolves an observed
   `asset:<id>`. It calls an injected `ToolContext.visual_read` capability.
3. The kernel binds a detached transcript snapshot from BEFORE this attempt's
   pending assistant tool call. The visual helper restores the ordinary LLM
   context, adds a perception-only instruction and native pixels of the selected
   image. No dangling tool-call group, fake durable receipt or caption cache.
4. The helper returns task-contextual visual observations, including needed
   background details and uncertainty, not tool decisions or a final task answer.
5. The original image reference and source-attributed model observation enter
   the same tool result in the durable transcript. Both projections recover
   their views from that evidence. The next Jev decision uses the normal loop.

Image contents and model observations are untrusted evidence, not authority.
An observation can be incomplete or mistaken. No permanent understood flag:
Jev can request another reading for a new question or missing detail. Historical
observations remain intact; re-reading never rewrites or deduplicates them.

## Projections, capabilities and accounting

- Ordinary parameter, arbitration, plain-decision and text-authoring requests
  receive attachment metadata and retained visual observation text, not pixels.
  They never silently read unselected images. Their standard tool schema stays
  stable. Only the explicit visual-reading request resolves selected image bytes.
- Pure context projection performs no I/O. The shared transport adapter resolves
  immutable assets; it never reopens a mutable sandbox path. Request logs use
  asset references, never binary/data URLs. Visual HTTP errors withhold bodies
  that could echo pixels. Metadata remains available without a vision key.
- Actual reading requires explicit `VISION_MODEL`, `VISION_MODEL_BASE_URL` and
  `VISION_MODEL_API_KEY` for a Chat Completions vision model. Missing capability,
  missing/corrupt assets and empty/truncated perception fail explicitly. Missing
  capability is terminal, not a reason for endless parameter-repair retries.
- `visual_read` is a tool-internal model role. Its start/completion events occur
  between dispatch and observation and share attempt/intent IDs. The main graph
  remains decision → arguments when needed → tool → record → loop. Old historical
  `visual_decision` events remain honestly replayable as legacy routes.
- Kernel owns model accounting, including tool-model failures. A Jev-selected
  image-reading step is not LLM-free direct execution. Missing usage or missing
  explicit `VISION_PRICE_{IN,OUT,CACHE_HIT}_PER_MTOK` values makes cost incomplete,
  never confirmed free. The frontend cannot declare a cost winner for subtotals.

## Storage, limits and entry points

- Static PNG, JPEG, WebP and GIF only; reject animation rather than hiding frames.
  Each original is limited to 10 MiB / 16 million pixels. Normalize EXIF rotation
  and strip metadata into a PNG with maximum edge 2048 pixels.
- Each upload batch / explicit model image selection has at most 8 parts and a
  32 MiB image-byte budget (request rendition bytes before base64 expansion).
  History is not a request batch: repeated readings do not consume a lifetime
  occurrence quota. Jev's visible source window is bounded; history stays durable.
- Assets live in `backend/artifacts/assets` or `JEVLOOP_ASSETS_DIR`. Back them up
  with session/run records for replay; no automatic asset garbage collection.
  This is a local trusted-user server. Asset hashes are not access controls.
- POST `/api/assets` accepts `{name, data}`; GET `/api/assets/<asset_id>` serves
  validated renditions. POST `/api/run` accepts `images`. CLI supports repeated
  `--image` paths. Uploads/history rendering do not invoke models. Both paired
  lanes receive the same initial images and normal generic tool catalog.

## Acceptance

Verify attachments without visual need still reach Jev; all ordinary LLM roles
exclude pixels; contextual readings include only the selected image and a complete
historical snapshot; Jev resumes after successful reads; observations survive
restore; repeats preserve provenance; failed reads do not become direct successes;
live graph remains the original loop and legacy replay stays truthful. Separate
mocked/offline tests from real model and Docker receipts.
