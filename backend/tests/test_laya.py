"""Laya provider selection and the local MLX/CUDA server contract."""

import asyncio

import pytest

from jevloop.context.state import Workspace
from jevloop.contracts.tools import ToolSpec
from jevloop.decision import model
from jevloop.decision.laya import (
    LayaRuntimeError,
    apply_context_budget,
    client_target,
    fit_context_budget,
    infer,
    normalize_decision_provider,
    parse_models,
    reported_model,
    required_credentials,
    resolve_checkpoint,
    resolve_runtime,
)


class _Provider:
    def specs(self):
        return [ToolSpec(name="PING", description="Inspect a fixture", phases=("INSPECT",))]

    def available(self, _workspace):
        return {"PING"}


class _Agent:
    def __init__(self):
        self.cfg = {"max_len": 512, "head_max_len": 192}
        self.encoder_cfg = {"max_position_embeddings": 8192}
        self.calls = []

    def system_one(self, state, questions):
        self.calls.append((state, questions, dict(self.cfg)))
        return {"model": "laya-rl-agent", "answers": {}, "usage": {"input_tokens": 1, "output_tokens": 0}}


class _MlxRouter:
    def __init__(self):
        self.agent = _Agent()
        self.routed = None

    def route(self, state, _questions, model=None):
        self.routed = state
        return {"model": model or "multilingual", "reason": "test"}

    def load(self, _name):
        return self.agent


class _CudaRouter:
    def __init__(self):
        self.loaded = ["english"]
        self.calls = []

    def predict(self, state, questions, **kwargs):
        self.calls.append(kwargs)
        return {"answers": {}, "usage": {"input_tokens": 1, "output_tokens": 0}}


def test_context_budget_uses_the_encoder_limit_and_leaves_state_room():
    assert fit_context_budget(8192, 8192, 1024) == (8192, 1024)
    assert fit_context_budget(1024, 8192, 1024) == (1024, 768)
    assert fit_context_budget(None, 8192, 1024) == (8192, 1024)


def test_mlx_infer_raises_the_loaded_agent_budget():
    router = _MlxRouter()
    result = infer(router, {"goal": "你好"}, {"phase": {}}, runtime="mlx",
                   model="jev-latest", max_len=8192, head_max_len=1024)
    assert router.routed == "你好"
    assert router.agent.cfg == {"max_len": 8192, "head_max_len": 1024}
    assert result["routing"]["model"] == "multilingual"
    assert apply_context_budget(router.agent, 8192, 1024) == (8192, 1024)


def test_cuda_infer_forwards_the_budget_and_ignores_a_jev_model_id():
    router = _CudaRouter()
    infer(router, "state", {}, runtime="cuda", model="jev-latest", max_len=8192, head_max_len=1024)
    assert router.calls == [{"max_len": 8192, "head_max_len": 1024}]
    infer(router, "state", {}, runtime="cuda", model="multilingual", max_len=8192, head_max_len=1024)
    assert router.calls[-1]["model"] == "multilingual"


def test_checkpoint_names_and_runtime_selection():
    assert resolve_checkpoint("jev-latest") is None
    assert resolve_checkpoint("multi") == "multilingual"
    assert parse_models("english,multi,english") == ["english", "multilingual"]
    with pytest.raises(LayaRuntimeError):
        parse_models("jev-latest")
    assert resolve_runtime(
        "auto", system="darwin", machine="arm64", mlx_importable=True, cuda_available=True,
    ) == "mlx"
    assert resolve_runtime(
        "auto", system="linux", machine="x86_64", mlx_importable=False, cuda_available=True,
    ) == "cuda"
    with pytest.raises(LayaRuntimeError, match="laya-mlx"):
        resolve_runtime(
            "mlx", system="linux", machine="x86_64", mlx_importable=False, cuda_available=True,
        )
    with pytest.raises(LayaRuntimeError, match="laya-cuda"):
        resolve_runtime(
            "cuda", system="darwin", machine="arm64", mlx_importable=True, cuda_available=False,
        )


def test_laya_credentials_skip_the_typesafe_key(monkeypatch):
    monkeypatch.delenv("DECISION_PROVIDER", raising=False)
    monkeypatch.delenv("TYPESAFE_MODEL", raising=False)
    monkeypatch.delenv("LAYA_API_KEY", raising=False)
    monkeypatch.delenv("LAYA_BASE_URL", raising=False)
    assert required_credentials() == ("TYPESAFE_API_KEY", "DEEPSEEK_API_KEY")
    assert reported_model() == "jev-latest"
    monkeypatch.setenv("DECISION_PROVIDER", "laya")
    monkeypatch.delenv("LAYA_MODEL", raising=False)
    assert required_credentials() == ("DEEPSEEK_API_KEY",)
    assert reported_model() == "laya:router"
    monkeypatch.setenv("LAYA_MODEL", "multilingual")
    assert reported_model() == "laya:multilingual"
    target = client_target()
    assert target["url"] == "http://127.0.0.1:8000/v1/systemone"
    assert target["model"] == "multilingual"
    assert target["key"] == ""


def test_choose_posts_the_laya_server_without_a_typesafe_key(monkeypatch):
    captured = {}

    async def post(url, key, body):
        captured.update(url=url, key=key, model=body.get("model"), questions=body["questions"])
        return {"answers": {}}

    monkeypatch.setattr(model, "post_json", post)
    monkeypatch.setenv("DECISION_PROVIDER", "laya")
    monkeypatch.setenv("LAYA_BASE_URL", "http://127.0.0.1:8000/")
    monkeypatch.setenv("LAYA_API_KEY", "local-secret")
    monkeypatch.delenv("LAYA_MODEL", raising=False)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(model.InvalidModelResponse):
        asyncio.run(model.choose(Workspace(goal="生成一个接口"), "生成一个接口", [], provider=_Provider()))
    assert captured["url"] == "http://127.0.0.1:8000/v1/systemone"
    assert captured["key"] == "local-secret"
    assert captured["model"] is None
    assert "phase" in captured["questions"]


def test_explicit_backend_overrides_the_process_default(monkeypatch):
    captured = {}

    async def post(url, _key, body):
        captured["url"] = url
        captured["model"] = body.get("model")
        return {"answers": {}}

    monkeypatch.setattr(model, "post_json", post)
    monkeypatch.setenv("DECISION_PROVIDER", "jev")
    monkeypatch.setenv("TYPESAFE_API_KEY", "hosted-key")
    monkeypatch.delenv("LAYA_MODEL", raising=False)
    with pytest.raises(model.InvalidModelResponse):
        asyncio.run(model.choose(
            Workspace(goal="hi"), "hi", [], provider=_Provider(), decision_backend="laya"))
    assert captured["url"] == "http://127.0.0.1:8000/v1/systemone"
    assert captured["model"] is None
    assert normalize_decision_provider(None) == "jev"
    assert normalize_decision_provider("laya") == "laya"
    with pytest.raises(ValueError, match="decision_provider"):
        normalize_decision_provider("cuda")


def test_start_run_keeps_the_selected_decision_provider(monkeypatch):
    from jevloop.apps import server
    from jevloop.apps.server import Dashboard

    class DeferredThread:
        def __init__(self, *_args, **_kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(server.threading, "Thread", DeferredThread)
    monkeypatch.setattr(server.runstore, "append", lambda *_args: None)
    monkeypatch.delenv("DECISION_PROVIDER", raising=False)
    dashboard = Dashboard()
    selected = dashboard.start_run({
        "goal": "local", "profile": "single_shadow", "decision_provider": "laya"})
    assert dashboard.runs[selected["run_id"]].params["decision_provider"] == "laya"
    dashboard.runs[selected["run_id"]].finished = True
    omitted = dashboard.start_run({"goal": "hosted", "profile": "single_shadow"})
    assert dashboard.runs[omitted["run_id"]].params["decision_provider"] == "jev"
    dashboard.runs[omitted["run_id"]].finished = True
    with pytest.raises(ValueError, match="decision_provider"):
        dashboard.start_run({
            "goal": "nope", "profile": "single_shadow", "decision_provider": "cuda"})


def test_systemone_http_contract():
    fastapi = pytest.importorskip("fastapi")
    assert fastapi
    import httpx

    from jevloop.apps.laya_server import create_app

    router = _CudaRouter()
    app = create_app(router, runtime="cuda", max_len=8192, head_max_len=1024, api_key="local-secret")

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://laya") as client:
            health = await client.get("/health")
            denied = await client.post("/v1/systemone", json={"state": "s", "questions": {}})
            accepted = await client.post(
                "/v1/systemone",
                headers={"Authorization": "Bearer local-secret"},
                json={"model": "jev-latest", "state": {"goal": "hi"}, "questions": {"phase": {"type": "choice"}}},
            )
            too_many = await client.post(
                "/v1/systemone",
                headers={"Authorization": "Bearer local-secret"},
                json={"questions": {str(i): {"type": "noul"} for i in range(129)}},
            )
        return health, denied, accepted, too_many

    health, denied, accepted, too_many = asyncio.run(scenario())
    assert health.status_code == 200
    assert health.json()["runtime"] == "cuda"
    assert denied.status_code == 401
    assert accepted.status_code == 200
    assert router.calls[-1] == {"max_len": 8192, "head_max_len": 1024}
    assert too_many.status_code == 413
