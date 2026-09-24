"""Local Laya server on the same `/v1/systemone` contract the Jev client uses.

MLX (Apple Silicon, extra `laya-mlx`) and CUDA (extra `laya-cuda`) share this
process. Weights load in the project uv environment, not a global interpreter.
"""

import asyncio
import hmac
import importlib.util
import json
import logging
import os
import threading

from jevloop.decision.laya import (
    DEFAULT_HEAD_MAX_LEN,
    DEFAULT_MAX_LEN,
    MAX_BODY_BYTES,
    MAX_QUESTIONS,
    infer,
    parse_models,
)

log = logging.getLogger(__name__)


def mlx_importable():
    return importlib.util.find_spec("laya_mlx") is not None


def cuda_available():
    if importlib.util.find_spec("torch") is None:
        return False
    import torch

    return bool(torch.cuda.is_available())


def selected_runtime(requested):
    from jevloop.decision.laya import resolve_runtime

    return resolve_runtime(
        requested,
        system=os.uname().sysname.lower(),
        machine=os.uname().machine.lower(),
        mlx_importable=mlx_importable(),
        cuda_available=cuda_available(),
    )


def build_router(runtime, *, device, models, preload, auto_task):
    if runtime == "mlx":
        from laya_mlx import Router

        router = Router(
            device=device or "gpu",
            max_loaded=len(models),
            dtype=os.environ.get("LAYA_DTYPE", "float16"),
            auto_task_detection=auto_task,
        )
    else:
        from laya import Router

        router = Router(
            device=device or "cuda",
            max_loaded=len(models),
            auto_task_detection=auto_task,
        )
    if preload:
        router.preload(models)
    return router


def create_app(router, *, runtime, max_len, head_max_len, api_key=None):
    from fastapi import FastAPI, Header, HTTPException, Request

    app = FastAPI(title="jevloop-laya", summary="Local Laya /v1/systemone")
    lock = threading.Lock()
    expected = ("Bearer " + api_key).encode() if api_key else b""

    def _authorized(authorization):
        if not api_key:
            return
        supplied = (authorization or "").encode()
        if not hmac.compare_digest(supplied, expected):
            raise HTTPException(status_code=401, detail="invalid or missing bearer token")

    @app.get("/health")
    def health():
        loaded = list(getattr(router, "loaded", []) or [])
        return {"status": "ok", "runtime": runtime, "loaded": loaded}

    @app.post("/v1/systemone")
    async def systemone(request: Request, authorization: str | None = Header(default=None)):
        _authorized(authorization)
        declared = request.headers.get("content-length")
        if declared:
            try:
                if int(declared) > MAX_BODY_BYTES:
                    raise HTTPException(status_code=413, detail="request body too large")
            except ValueError:
                pass
        raw = await request.body()
        if len(raw) > MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="request body too large")
        try:
            body = json.loads(raw)
        except ValueError:
            raise HTTPException(status_code=400, detail="request body must be valid JSON") from None
        if not isinstance(body, dict) or not isinstance(body.get("questions"), dict):
            raise HTTPException(status_code=400, detail="questions must be an object")
        questions = body["questions"]
        if len(questions) > MAX_QUESTIONS:
            raise HTTPException(
                status_code=413,
                detail=f"too many questions ({len(questions)} > {MAX_QUESTIONS})",
            )

        def _predict():
            with lock:
                return infer(
                    router,
                    body.get("state"),
                    questions,
                    runtime=runtime,
                    model=body.get("model"),
                    max_len=max_len,
                    head_max_len=head_max_len,
                )

        try:
            return await asyncio.get_running_loop().run_in_executor(None, _predict)
        except HTTPException:
            raise
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from None
        except Exception:
            log.exception("Laya inference failed")
            raise HTTPException(status_code=500, detail="inference failed") from None

    return app


def serve(host, port, *, runtime="auto", device=None, models=None, preload=True,
          max_len=DEFAULT_MAX_LEN, head_max_len=DEFAULT_HEAD_MAX_LEN, api_key=None):
    import uvicorn

    chosen = selected_runtime(runtime)
    names = parse_models(models if models is not None else os.environ.get("LAYA_MODELS"))
    auto_task = os.environ.get("LAYA_AUTO_TASK", "").strip().lower() in {"1", "true", "yes", "on"}
    router = build_router(
        chosen, device=device, models=names, preload=preload, auto_task=auto_task)
    app = create_app(
        router,
        runtime=chosen,
        max_len=max_len,
        head_max_len=head_max_len,
        api_key=api_key if api_key is not None else os.environ.get("LAYA_API_KEY") or None,
    )
    uvicorn.run(app, host=host, port=port)
