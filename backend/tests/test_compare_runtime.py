"""Paired-run isolation and shared baseline safety behavior."""

import asyncio
import json
from typing import ClassVar

import pytest

from jevloop import server
from jevloop.cli import build_parser
from jevloop.drivers import PlainLlmDriver
from jevloop.guardrails import WritePolicy
from jevloop.kernel import RuntimeKernel
from jevloop.server import Dashboard, RunState
from jevloop.state import Workspace
from jevloop.tools.base import ToolSpec


class FakeImage:
    image_id = "sha256:shared"
    built = 0
    closed = 0

    @classmethod
    async def build(cls):
        cls.built += 1
        return cls()

    async def close(self):
        type(self).closed += 1


class FakeContainer:
    started: ClassVar[list] = []
    closed: ClassVar[list] = []

    def __init__(self, lane_id, workspace_key, network_enabled):
        self.lane_id = lane_id
        self.workspace_key = workspace_key
        self.network_enabled = network_enabled

    @classmethod
    async def start(cls, image, lane_id, workspace_key=None, network_enabled=False):
        assert image.image_id == "sha256:shared"
        container = cls(lane_id, workspace_key, network_enabled)
        cls.started.append(container)
        return container

    async def close(self):
        type(self).closed.append(self)


class RecordingDashboard(Dashboard):
    def __init__(self):
        super().__init__()
        self.lanes = []

    async def _run_lane(self, state, sandbox, lane):
        self.lanes.append((lane, sandbox))


def test_paired_profile_uses_one_image_and_distinct_containers(monkeypatch):
    FakeImage.built = FakeImage.closed = 0
    FakeContainer.started = []
    FakeContainer.closed = []
    monkeypatch.setattr(server, "DockerSandboxImage", FakeImage)
    monkeypatch.setattr(server, "DockerSandboxContainer", FakeContainer)
    monkeypatch.setattr(server.runstore, "append", lambda *_args: None)

    dashboard = RecordingDashboard()
    first = RunState("r1", {"profile": "paired_shadow", "session_id": "session"})
    second = RunState("r2", {"profile": "paired_shadow", "session_id": "session-two"})
    asyncio.run(dashboard._execute_async(first))
    asyncio.run(dashboard._execute_async(second))

    assert FakeImage.built == 1 and FakeImage.closed == 0
    assert [container.lane_id for container in FakeContainer.started] == [
        "jev", "baseline", "jev", "baseline",
    ]
    assert [container.workspace_key for container in FakeContainer.started] == [
        "session--jev", "session--baseline",
        "session-two--jev", "session-two--baseline",
    ]
    assert all(container.network_enabled for container in FakeContainer.started)
    assert [lane for lane, _container in dashboard.lanes] == [
        "jev", "baseline", "jev", "baseline",
    ]
    assert len({id(container) for container in FakeContainer.started}) == 4
    assert FakeContainer.closed == FakeContainer.started
    ready = next(event for _seq, event in first.log if event["type"] == "sandbox_ready")
    assert ready == {"type": "sandbox_ready", "image_id": "sha256:shared",
                     "lanes": ["jev", "baseline"]}
    assert first.log[-1][1] == {"type": "done"}


def test_paired_start_persists_distinct_stable_cache_scopes(monkeypatch):
    class DeferredThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(server.threading, "Thread", DeferredThread)
    monkeypatch.setattr(server.runstore, "append", lambda *_args: None)

    dashboard = Dashboard()
    result = dashboard.start_run({
        "goal": "compare",
        "profile": "paired_shadow",
        "session_id": "session-alpha",
    })
    params = dashboard.runs[result["run_id"]].params

    assert params["cache_policy"] == server.CACHE_POLICY_ISOLATED
    assert set(params["cache_scopes"]) == {"jev", "baseline"}
    assert params["cache_scopes"]["jev"] != params["cache_scopes"]["baseline"]
    assert len(params["cache_scopes"]["jev"]) == len(params["cache_scopes"]["baseline"]) == 24
    assert params["cache_scopes"]["jev"] == server._cache_scope("session-alpha", "jev")
    assert params["escalate_threshold"] == 0.5
    assert params["ambiguity_gate"] == 0.4
    assert params["answer_progress_floor"] is None

    meta = dashboard.runs[result["run_id"]].log[0][1]
    assert meta["params"]["cache_scopes"] == params["cache_scopes"]


def test_single_start_uses_natural_shared_cache_policy(monkeypatch):
    class DeferredThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(server.threading, "Thread", DeferredThread)
    monkeypatch.setattr(server.runstore, "append", lambda *_args: None)

    dashboard = Dashboard()
    result = dashboard.start_run({
        "goal": "single",
        "profile": "single_shadow",
        "ambiguity_gate": 0.2,
        "answer_progress_floor": 1.5,
    })
    params = dashboard.runs[result["run_id"]].params

    assert params["cache_policy"] == server.CACHE_POLICY_NATURAL
    assert params["cache_scopes"] == {}
    assert params["ambiguity_gate"] == 0.2
    assert params["answer_progress_floor"] == 1.5




def test_cli_gate_defaults_match_selected_experiment():
    parser = build_parser()
    run = parser.parse_args(["run", "goal"])
    bench = parser.parse_args(["bench"])

    for args in (run, bench):
        assert args.escalate_threshold == 0.5
        assert args.ambiguity_gate == 0.4
        assert args.answer_progress_floor is None
    no_gate = parser.parse_args(["run", "goal", "--no-ambiguity-gate"])
    assert no_gate.ambiguity_gate is None


def test_old_compare_live_cross_product_is_rejected():
    with pytest.raises(ValueError, match="profile must be one of"):
        Dashboard().start_run({"goal": "x", "compare": True, "live": True})


class SendProvider:
    def __init__(self):
        self.executed = 0

    def specs(self):
        return [ToolSpec(name="SEND_MESSAGE", description="send", needs_target=True,
                         target_pool="recipients", needs_text=True,
                         text_instruction="message", write=True, recipient_gate=True)]

    def available(self, _workspace):
        return {"SEND_MESSAGE"}

    async def execute(self, _name, _ctx):
        self.executed += 1
        return {"status": "ready"}


async def fake_send_turn(_transcript, _schemas):
    message = {"role": "assistant", "content": None, "tool_calls": [{
        "id": "call-1", "type": "function", "function": {
            "name": "SEND_MESSAGE",
            "arguments": json.dumps({"target": "forbidden", "content": "hello"}),
        },
    }]}
    return message, {"model": "fake", "latency_ms": 1, "usage": {}}, {"fake": True}


def test_plain_lane_uses_recipient_policy_before_execution():
    provider = SendProvider()
    workspace = Workspace()
    workspace.recipients["forbidden"] = "forbidden"
    kernel = RuntimeKernel(
        PlainLlmDriver(llm=fake_send_turn),
        provider,
        WritePolicy(allowed_recipients={"allowed"}),
        max_steps=2,
        max_writes=2,
        workspace=workspace,
    )

    async def collect():
        return [step async for step in kernel.run("send a message")]

    steps = asyncio.run(collect())
    assert provider.executed == 0
    assert "not in the allowed set" in steps[-2]["denied"]
    assert kernel.budget.steps == 1
    assert kernel.budget.writes == 0
