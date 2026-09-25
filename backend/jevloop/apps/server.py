"""Local dashboard server: static frontend + SSE run stream + run control.

Bound to loopback by default. Serves frontend/dist when it exists (pnpm build:frontend);
during frontend development use the Vite dev server, which proxies /api here.
"""

import asyncio
import hashlib
import json
import queue
import threading
import time
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from jevloop.config import DEFAULT_AMBIGUITY_GATE, DEFAULT_ANSWER_PROGRESS_FLOOR, DEFAULT_ESCALATE_THRESHOLD
from jevloop.contracts.policy import WritePolicy
from jevloop.decision.drivers import JevDriver, PlainLlmDriver
from jevloop.decision.laya import normalize_decision_provider
from jevloop.paths import REPO_ROOT
from jevloop.runtime.kernel import RuntimeKernel
from jevloop.runtime.metrics import RunMetrics
from jevloop.storage import runstore, sessions
from jevloop.tools.sandbox import DockerSandboxContainer, DockerSandboxImage, SandboxTools

WEB_DIST = REPO_ROOT / "frontend" / "dist"
MIME = {".html": "text/html", ".js": "text/javascript", ".css": "text/css",
        ".svg": "image/svg+xml", ".json": "application/json", ".png": "image/png"}
RUN_PROFILES = {"single_live", "single_shadow", "paired_shadow"}
CACHE_POLICY_NATURAL = "natural_shared"
CACHE_POLICY_ISOLATED = "isolated_session_lane"


def _cache_scope(session_id, lane):
    material = f"jevloop-cache-v1:{session_id}:{lane}".encode()
    return hashlib.sha256(material).hexdigest()[:24]


def _configured_cache_scope(params, lane):
    return (params.get("cache_scopes") or {}).get(lane)

def _sandbox_network_enabled(params):
    return bool(params.get("sandbox_network", True))


def _is_paired(params):
    return params["profile"] == "paired_shadow"


def _remote_live(params):
    return params["profile"] == "single_live"


def _session_storage_id(params, lane):
    session_id = params["session_id"]
    return f"{session_id}--{lane}" if _is_paired(params) else session_id


def _optional_float(value):
    if value is None or value == "":
        return None
    return float(value)



class RunState:
    def __init__(self, run_id, params):
        self.run_id = run_id
        self.params = params
        self.log = []  # every event, replayed for late subscribers
        self.seq = 0
        self._append_error = None
        self.lock = threading.Lock()
        self.clients = []  # one queue per SSE connection
        self.resume = asyncio.Event()
        self.abort = False
        self.step_pause = params.get("step_pause", False)
        self.awaiting = False
        self.finished = False

    def emit(self, event):
        with self.lock:
            if self._append_error is not None:
                raise self._append_error
            next_seq = self.seq + 1
            try:
                runstore.append(self.run_id, next_seq, event)
            except Exception as error:
                self._append_error = error
                raise
            self.seq = next_seq
            entry = (next_seq, event)
            self.log.append(entry)
            clients = list(self.clients)
        for client in clients:
            client.put(entry)

    def subscribe(self):
        q = queue.Queue()
        with self.lock:
            snapshot = list(self.log)
            if not self.finished:
                self.clients.append(q)
        return snapshot, q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.clients:
                self.clients.remove(q)


class Dashboard:
    def __init__(self, image=None):
        self.runs = {}
        self.lock = threading.Lock()
        self.loop = None  # set by serve(); runs execute as coroutines on it
        self.image = image  # immutable and shared for the dashboard process

    async def prepare_image(self):
        """Build the immutable sandbox once; every run mounts its own volume."""
        if self.image is None:
            self.image = await DockerSandboxImage.build()
        return self.image

    def start_run(self, params):
        profile = params.get("profile")
        if profile not in RUN_PROFILES:
            raise ValueError(f"profile must be one of {sorted(RUN_PROFILES)}")
        run_id = uuid.uuid4().hex[:12]
        session_id = params.get("session_id") or sessions.new_session_id()
        paired = profile == "paired_shadow"
        cache_policy = CACHE_POLICY_ISOLATED if paired else CACHE_POLICY_NATURAL
        cache_scopes = (
            {lane: _cache_scope(session_id, lane) for lane in ("jev", "baseline")}
            if paired else {}
        )
        params = {
            **params,
            "session_id": session_id,
            "cache_policy": cache_policy,
            "cache_scopes": cache_scopes,
            "escalate_threshold": params.get(
                "escalate_threshold", DEFAULT_ESCALATE_THRESHOLD),
            "ambiguity_gate": params.get(
                "ambiguity_gate", DEFAULT_AMBIGUITY_GATE),
            "answer_progress_floor": params.get(
                "answer_progress_floor", DEFAULT_ANSWER_PROGRESS_FLOOR),
            "decision_provider": normalize_decision_provider(
                params.get("decision_provider")),
        }
        state = RunState(run_id, params)
        with self.lock:
            active = [s for s in self.runs.values() if not s.finished]
            if active:
                raise RuntimeError("a run is already active; abort it first")
            self.runs[run_id] = state
        state.emit({"type": "meta", "params": params, "created_at": datetime.now().astimezone().isoformat(timespec="seconds")})
        if self.loop:
            asyncio.run_coroutine_threadsafe(self._execute_async(state), self.loop)
        else:  # no server loop (tests / library use): fresh loop per run
            threading.Thread(target=lambda: asyncio.run(self._execute_async(state)),
                             daemon=True).start()
        return {"run_id": run_id, "session_id": session_id}

    def get_run(self, run_id):
        """In-memory run, or a finished one reloaded from disk for replay."""
        state = self.runs.get(run_id)
        if state:
            return state
        stored = runstore.load(run_id)
        if not stored:
            return None
        params = {}
        for _seq, event in stored:
            if event.get("type") == "meta":
                params = event.get("params", {})
        revived = RunState(run_id, params)
        revived.log = stored
        revived.seq = stored[-1][0] if stored else 0
        revived.finished = True
        with self.lock:
            self.runs[run_id] = revived
        return revived

    async def _execute_async(self, state):
        params = state.params
        containers = []
        try:
            image = await self.prepare_image()
            jev_container = await DockerSandboxContainer.start(
                image,
                "jev",
                workspace_key=_session_storage_id(params, "jev"),
                network_enabled=_sandbox_network_enabled(params),
            )
            containers.append(jev_container)
            lanes = [self._run_lane(state, jev_container, "jev")]
            if _is_paired(params):
                plain_container = await DockerSandboxContainer.start(
                    image,
                    "baseline",
                    workspace_key=_session_storage_id(params, "baseline"),
                    network_enabled=_sandbox_network_enabled(params),
                )
                containers.append(plain_container)
                lanes.append(self._run_lane(state, plain_container, "baseline"))
            state.emit({
                "type": "sandbox_ready",
                "image_id": image.image_id,
                "lanes": ["jev", "baseline"] if _is_paired(params) else ["jev"],
            })
            results = await asyncio.gather(*lanes, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    state.emit({"type": "error", "message": f"{type(result).__name__}: {result}"})
        except Exception as error:  # noqa: BLE001 - setup errors are run events
            state.emit({"type": "error", "message": f"{type(error).__name__}: {error}"})
        finally:
            if containers:
                await asyncio.gather(*(container.close() for container in containers),
                                     return_exceptions=True)
            # The immutable image belongs to the Dashboard process, not this
            # message/run. Per-run containers are removed; Session volumes persist.
            state.finished = True
            state.emit({"type": "done"})

    def _policy(self, params):
        return WritePolicy(
            allowed_recipients=set(params.get("allow_recipients", [])),
            min_confidence=float(params.get("min_confidence", 0.6)),
        )

    async def _run_lane(self, state, sandbox, lane):
        params = state.params
        cache_scope = _configured_cache_scope(params, lane)
        cache_policy = params.get("cache_policy", CACHE_POLICY_NATURAL)
        tools = SandboxTools(sandbox)
        session_storage_id = _session_storage_id(params, lane)
        workspace = None
        transcript = sessions.load(session_storage_id)
        if transcript:
            from jevloop.context.projection import rebuild_workspace
            workspace = rebuild_workspace(transcript)
            if cache_scope:
                system = transcript.messages()[0].get("content", "")
                if not system.startswith(f"[cache-scope:{cache_scope}]"):
                    cache_policy = "legacy_shared"
                    cache_scope = None
        metrics = RunMetrics(
            cache_policy=cache_policy,
            cache_scope_hash=cache_scope,
        )
        if lane == "jev":
            driver = JevDriver(
                escalate_threshold=float(
                    params.get("escalate_threshold", DEFAULT_ESCALATE_THRESHOLD)),
                ambiguity_gate=_optional_float(
                    params.get("ambiguity_gate", DEFAULT_AMBIGUITY_GATE)),
                answer_progress_floor=_optional_float(
                    params.get("answer_progress_floor", DEFAULT_ANSWER_PROGRESS_FLOOR)),
                decision_backend=params.get("decision_provider"),
            )
        else:
            driver = PlainLlmDriver()

        checkpoint = lambda current: sessions.save(session_storage_id, current)

        def emit_kernel_event(event):
            state.emit({**event, "lane": lane})

        kernel = RuntimeKernel(
            driver,
            tools,
            self._policy(params),
            live=_remote_live(params),
            max_steps=int(params.get("max_steps", 30)),
            max_writes=int(params.get("max_writes", 0)),
            metrics=metrics,
            transcript=transcript,
            workspace=workspace,
            event_sink=emit_kernel_event,
            checkpoint=checkpoint,
            cache_scope=cache_scope,
            auto_acknowledge_unknown=(
                not _remote_live(params) and not _sandbox_network_enabled(params)
            ),
        )

        async def before_step(_decision):
            if state.abort:
                return "abort"
            if state.step_pause:
                state.awaiting = True
                state.emit({
                    "type": "awaiting_continue",
                    "lane": lane,
                    "step_index": len(kernel.trace),
                })
                await state.resume.wait()
                state.resume.clear()
                state.awaiting = False
                if state.abort:
                    return "abort"
            return "continue"

        try:
            async for step in kernel.run(params["goal"], before_step=before_step):
                state.emit({"type": "step", "lane": lane, "step": step})
                state.emit({"type": "metrics", "lane": lane, "metrics": metrics.summary()})
                if step.get("final"):
                    state.emit({
                        "type": "final",
                        "lane": lane,
                        "final": {"answer": kernel.workspace.answer},
                        "metrics": metrics.summary(),
                    })
        except Exception as error:  # noqa: BLE001 - lane failures stay isolated
            state.emit({
                "type": "error",
                "lane": lane,
                "message": f"{type(error).__name__}: {error}",
            })

    def control(self, run_id, action):
        """Runs on an HTTP thread; flip async state on the server loop, thread-safely."""
        state = self.runs.get(run_id)
        if not state:
            raise KeyError("unknown run")

        def apply():
            if action == "abort":
                state.abort = True
                state.resume.set()  # release a paused step, if any
            elif action == "continue":
                state.resume.set()
            elif action == "pause_on":
                state.step_pause = True
            elif action == "pause_off":
                state.step_pause = False
                state.resume.set()
            else:
                raise ValueError(f"unknown action {action!r}")

        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(apply)
        else:
            apply()


DASHBOARD = Dashboard()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        pass  # keep the console quiet; the dashboard shows the activity

    # -- helpers ------------------------------------------------------------
    def _json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length)) if length else {}

    # -- routes -------------------------------------------------------------
    def do_POST(self):
        try:
            if self.path == "/api/run":
                params = self._body()
                if not str(params.get("goal", "")).strip():
                    return self._json({"error": "goal is required"}, 400)
                return self._json(DASHBOARD.start_run(params))
            parts = self.path.strip("/").split("/")
            if len(parts) == 3 and parts[0] == "api" and parts[1] == "run":
                DASHBOARD.control(parts[2], self._body().get("action"))
                return self._json({"ok": True})
            return self._json({"error": "not found"}, 404)
        except Exception as error:  # noqa: BLE001 - API boundary returns errors as JSON
            return self._json({"error": str(error)}, 400)

    def do_GET(self):
        parts = self.path.strip("/").split("/")
        url = urlsplit(self.path)
        if url.path == "/api/config":
            return self._json({
                "decision_provider": normalize_decision_provider(None),
            })
        if url.path == "/api/runs":
            query = parse_qs(url.query, keep_blank_values=True)
            if not query:
                return self._json({"runs": runstore.list_runs()})
            if set(query) != {"session_id"} or len(query["session_id"]) != 1:
                return self._json({"error": "expected one session_id query parameter"}, 400)
            try:
                session_id = runstore.validate_session_id(query["session_id"][0])
            except ValueError as error:
                return self._json({"error": str(error)}, 400)
            return self._json({
                "runs": runstore.list_runs(session_id=session_id),
                "session_id": session_id,
                "complete": True,
            })
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "run":
            state = DASHBOARD.get_run(parts[2])
            if not state:
                return self._json({"error": "unknown run"}, 404)
            snapshot, replay_queue = state.subscribe()
            state.unsubscribe(replay_queue)
            return self._json({
                "events": [
                    {"seq": seq, "event": event} for seq, event in snapshot
                ],
            })
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "run" and parts[3] == "events":
            return self._stream_events(parts[2])
        return self._static()

    # -- SSE ----------------------------------------------------------------
    def _stream_events(self, run_id):
        state = DASHBOARD.get_run(run_id)
        if not state:
            return self._json({"error": "unknown run"}, 404)
        snapshot, live = state.subscribe()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            sent = 0
            deadline = time.monotonic() + 3600
            while time.monotonic() < deadline:
                entry = None
                for seq, event in snapshot:
                    if seq > sent:
                        entry = (seq, event)
                        break
                if entry is None and not state.finished:
                    try:
                        entry = live.get(timeout=1.0)
                    except queue.Empty:
                        continue
                if entry is None:  # finished and drained
                    break
                seq, event = entry
                sent = seq
                payload = json.dumps(event, ensure_ascii=False, default=str)
                self.wfile.write(f"id: {seq}\ndata: {payload}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            state.unsubscribe(live)

    # -- static -------------------------------------------------------------
    def _static(self):
        if self.path == "/":
            target = WEB_DIST / "index.html"
        else:
            candidate = (WEB_DIST / self.path.lstrip("/")).resolve()
            if not str(candidate).startswith(str(WEB_DIST.resolve())) or not candidate.is_file():
                candidate = WEB_DIST / "index.html"  # SPA fallback
            target = candidate
        if not target.is_file():
            return self._json({
                "error": "frontend not built",
                "hint": "run `pnpm install && pnpm build:web` at the repo root, "
                        "or use `pnpm dev` during development",
            }, 404)
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(target.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(host="127.0.0.1", port=8790):
    # Build the immutable sandbox at `make dev`/serve startup. Individual
    # messages only start disposable containers and mount Session/lane volumes.
    loop = asyncio.new_event_loop()
    print("preparing shared sandbox image...", flush=True)
    loop.run_until_complete(DASHBOARD.prepare_image())
    DASHBOARD.loop = loop
    threading.Thread(target=loop.run_forever, daemon=True, name="jevloop-loop").start()
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"JevLoop dashboard on http://{host}:{port} (web dist: "
          f"{'built' if (WEB_DIST / 'index.html').is_file() else 'NOT built'})")
    server.serve_forever()
