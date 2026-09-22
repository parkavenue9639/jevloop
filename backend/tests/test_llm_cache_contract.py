"""Stable request shapes and complete-call/full-generation routing; no network."""

import asyncio
import json
from copy import deepcopy

import pytest

from jevloop.context.state import Workspace
from jevloop.context.transcript import Transcript
from jevloop.contracts.policy import InvalidProposal, WritePolicy
from jevloop.contracts.schemas import llm_tool_schemas
from jevloop.contracts.tools import ToolSpec
from jevloop.decision.argument_helper import generate_arguments
from jevloop.decision.drivers import DriverContext, DriverProposal, JevDriver, PlainLlmDriver
from jevloop.decision.escalation import arbitrate
from jevloop.decision.model import _selected_arguments, compile_questions
from jevloop.runtime.kernel import RuntimeKernel
from jevloop.tools.sandbox import SandboxTools


def completion(operation, args):
    return {"choices": [{"finish_reason": "tool_calls", "message": {
        "role": "assistant", "content": None, "tool_calls": [{
            "id": "test-call", "type": "function", "function": {
                "name": operation, "arguments": json.dumps(args)}}]}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5}}


class Provider(SandboxTools):
    def __init__(self):
        super().__init__()
        self.allowed = {item.name for item in self.specs()}
        self.executed = []

    def available(self, _workspace):
        return set(self.allowed)

    async def execute(self, name, ctx):
        self.executed.append((name, deepcopy(ctx.arguments)))
        return {"status": "ready"}


class DecisionDriver:
    name = "jev"

    def __init__(self, decision):
        self.decision = decision

    async def decide(self, _context):
        return DriverProposal(decision=deepcopy(self.decision), base_decision=self.decision)


def collect(kernel, before_step=None):
    async def run():
        return [step async for step in kernel.run("inspect project", before_step=before_step)]
    return asyncio.run(run())


def test_all_llm_routes_share_tools_despite_operation_and_availability_changes(monkeypatch):
    provider = Provider()
    ledger = Transcript("system", "inspect and edit")
    requests = []
    payload = None

    async def post(_url, _key, request):
        requests.append(deepcopy(request))
        return payload

    async def run():
        nonlocal payload
        for operation, args in [
            ("READ_FILE", {"path": "a.py"}),
            ("WRITE_FILE", {"path": "unobserved.py", "content": "hello"}),
            ("BASH", {"command": "pwd"}),
            ("ANSWER", {"answer": "done"}),
        ]:
            payload = completion(operation, args)
            await generate_arguments(ledger, provider, operation, post=post)
            ledger.append_user("Additional execution evidence.")
        # Change actual availability, never the definitions sent to the LLM.
        provider.allowed = {"READ_FILE"}
        payload = completion("READ_FILE", {"path": "a.py"})
        verdict = await arbitrate(ledger, {"operation": "READ_FILE", "confidence": 0.2},
                                  provider, {"READ_FILE", "ANSWER"}, post=post)
        assert verdict["valid"]
        await PlainLlmDriver().decide(DriverContext("goal", Workspace(), ledger, provider))

    monkeypatch.setattr("jevloop.decision.drivers.post_json", post)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only")
    asyncio.run(run())
    assert len(requests) == 6
    assert all(request["tools"] == llm_tool_schemas(provider) for request in requests)
    assert [request["tool_choice"] for request in requests] == ["required"] * 4 + ["auto"] * 2
    assert "Currently permitted operations: ANSWER, READ_FILE." in requests[-1]["messages"][-1]["content"]
    assert '"const"' not in json.dumps(requests[0]["tools"])
    assert "unobserved.py" not in json.dumps(requests[1])
    names = [schema["function"]["name"] for schema in requests[0]["tools"]]
    assert names == sorted(names)
    assert "DONE" not in names and names.count("CANNOT_BIND") == 1
    requests[0]["tools"][0]["function"]["description"] = "changed"
    assert llm_tool_schemas(provider) == requests[1]["tools"]


def test_candidate_catalog_excludes_partial_and_invalid_invocations():
    provider = Provider()
    workspace = Workspace(files={"a.py": "a.py", "../escape": "bad path"})
    _, compiled = compile_questions(workspace, provider)
    for operation in ("WRITE_FILE", "SEARCH_FILES", "BASH"):
        for (phase, name), choices in compiled.target_candidates.items():
            if name == operation:
                assert set(choices) == {"LLM_PARAMETERS"}
    read = compiled.target_candidates[("INSPECT", "READ_FILE")]
    assert set(read) == {"a.py", "LLM_PARAMETERS"}
    assert json.loads(read["a.py"]["arguments"]) == {"path": "a.py", "offset": 0, "limit": 200}


@pytest.mark.parametrize("extra", [
    {"bound_arguments": {"path": "old.py"}},
    {"arguments": {"path": "old.py", "content": "old"}},
    {"target": "old.py"},
    {"ledger_content": "old content"},
])
def test_llm_route_rejects_inherited_payload_without_authoring(extra):
    async def forbidden(*_args):
        raise AssertionError("Do not silently inherit or discard an invalid route payload")
    provider = Provider()
    kernel = RuntimeKernel(None, provider, WritePolicy(), arguer=forbidden)
    spec = next(item for item in provider.specs() if item.name == "WRITE_FILE")
    with pytest.raises(InvalidProposal, match="cannot carry"):
        asyncio.run(kernel._materialize_arguments(
            {"operation": "WRITE_FILE", "binding_mode": "llm_parameters", **extra}, spec))


def test_weak_binding_switch_clears_all_arguments():
    async def chooser(*_args, **_kwargs):
        return {"operation": "READ_FILE", "phase": "INSPECT", "confidence": 0.2,
                "operation_path_confidence": 0.99, "binding_mode": "observed",
                "arguments": {"path": "old.py"}, "bound_arguments": {"path": "old.py"},
                "target": "old.py", "ledger_content": "old"}
    proposal = asyncio.run(JevDriver(chooser=chooser).decide(
        DriverContext("goal", Workspace(), Transcript("system", "goal"), Provider())))
    assert proposal.decision["binding_mode"] == "llm_parameters"
    assert proposal.decision["bound_arguments"] == {}
    assert proposal.decision["target"] is None
    assert "arguments" not in proposal.decision and "ledger_content" not in proposal.decision


def test_zero_parameter_tool_still_honors_explicit_llm_route():
    spec = ToolSpec(name="PING", description="probe")
    calls = []

    async def author(_ledger, _provider, operation):
        calls.append(operation)
        return {}, {"kind": "parameter_authoring", "usage": {}}

    kernel = RuntimeKernel(None, Provider(), WritePolicy(), arguer=author)
    intent = asyncio.run(kernel._materialize_arguments(
        {"operation": "PING", "binding_mode": "llm_parameters", "bound_arguments": {}}, spec))
    assert intent["arguments"] == {} and calls == ["PING"]


def test_explicit_operation_only_route_uses_full_argument_helper_not_legacy_texter():
    calls = []

    async def author(_ledger, _provider, operation):
        calls.append(operation)
        return {"path": "new.py", "content": "new"}, {"kind": "parameter_authoring"}

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("Explicit full authoring must not fall through to legacy texter")

    kernel = RuntimeKernel(None, Provider(), WritePolicy(), arguer=author, texter=forbidden)
    intent = asyncio.run(kernel._materialize({"operation": "WRITE_FILE", "binding_mode": "llm_parameters"}))
    assert intent["arguments"] == {"path": "new.py", "content": "new"}
    assert calls == ["WRITE_FILE"]


def test_malformed_default_candidate_does_not_hide_full_generation_option():
    class MalformedProvider(Provider):
        def specs(self):
            return [ToolSpec(name="WRITE", description="write", needs_text=True,
                             binding_defaults={"content": "```python\nunclosed"})]

    _, compiled = compile_questions(Workspace(), MalformedProvider())
    assert set(compiled.target_candidates[("INSPECT", "WRITE")]) == {"LLM_PARAMETERS"}


def test_plain_request_note_is_committed_without_rewriting_previous_prefix(monkeypatch):
    provider = Provider()
    provider.allowed = {"READ_FILE"}
    requests = []

    async def post(_url, _key, request):
        requests.append(deepcopy(request))
        return completion("READ_FILE", {"path": f"file-{len(requests)}.py"})

    monkeypatch.setattr("jevloop.decision.drivers.post_json", post)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only")
    kernel = RuntimeKernel(PlainLlmDriver(), provider, WritePolicy(), max_steps=2)
    collect(kernel)
    assert len(requests) == 2
    first = requests[0]["messages"]
    assert requests[1]["messages"][:len(first)] == first
    assert kernel.transcript.messages()[:len(first)] == first
    assert requests[0]["tools"] == requests[1]["tools"]


def test_batch_preserves_displayed_ranges_and_rejects_incompatible_ones():
    spec = next(item for item in Provider().specs() if item.name == "READ_FILE")
    candidates = {path: {"arguments": json.dumps({"path": path, "offset": 30, "limit": 10})}
                  for path in ("a.py", "b.py")}
    assert _selected_arguments(spec, candidates, ("a.py", "b.py"), Workspace()) == {
        "path": ["a.py", "b.py"], "offset": 30, "limit": 10}
    candidates["b.py"]["arguments"] = json.dumps({"path": "b.py", "offset": 50, "limit": 10})
    with pytest.raises(InvalidProposal, match="incompatible"):
        _selected_arguments(spec, candidates, ("a.py", "b.py"), Workspace())


def test_full_authoring_uses_new_path_and_commits_exact_execution(monkeypatch):
    provider = Provider()
    ledger = Transcript("system", "Create a new file")
    ledger.append_user("Previously observed old.py")

    async def post(_url, _key, request):
        assert request["tools"] == llm_tool_schemas(provider)
        assert "old.py" not in request["messages"][-1]["content"]
        return completion("WRITE_FILE", {"path": "new.py", "content": "new content"})

    monkeypatch.setattr("jevloop.decision.argument_helper.post_json", post)
    events = []
    kernel = RuntimeKernel(DecisionDriver({"operation": "WRITE_FILE", "confidence": 1.0,
        "binding_mode": "llm_parameters"}), provider,
        WritePolicy(), transcript=ledger, max_steps=1, event_sink=events.append)
    steps = collect(kernel)
    assert provider.executed == [("WRITE_FILE", {"path": "new.py", "content": "new content"})]
    assert len(kernel.metrics.helper_calls) == 1
    stored = next(message for message in ledger.dump() if message.get("tool_calls"))
    assert json.loads(stored["tool_calls"][0]["function"]["arguments"]) == provider.executed[0][1]
    assert steps[0]["outcome"]["status"] == "ready"
    assert next(event for event in events if event["type"] == "decision_ready")["needs_authoring"]


@pytest.mark.parametrize("operation,args", [
    ("CANNOT_BIND", {"reason": "insufficient evidence"}),
    ("BASH", {"command": "pwd"}),
])
def test_decline_or_operation_switch_is_billed_but_never_executed(monkeypatch, operation, args):
    provider = Provider()

    async def post(*_args):
        return completion(operation, args)

    monkeypatch.setattr("jevloop.decision.argument_helper.post_json", post)
    kernel = RuntimeKernel(DecisionDriver({"operation": "WRITE_FILE", "confidence": 1.0,
        "binding_mode": "llm_parameters", "bound_arguments": {}}), provider,
        WritePolicy(), max_steps=1)
    steps = collect(kernel)
    assert provider.executed == [] and len(kernel.metrics.helper_calls) == 1
    assert steps[0]["model_calls"][-1]["usage"]["prompt_tokens"] == 10
    assert steps[0]["outcome"]["effect_disposition"] == "NOT_APPLIED"
    assert steps[0]["outcome"]["error"]["recoverability"] == "recoverable"
    assert kernel.transcript.repair() == 0


def test_availability_revoked_after_proposal_blocks_dispatch():
    provider = Provider()
    kernel = RuntimeKernel(DecisionDriver({"operation": "READ_FILE", "confidence": 1.0,
        "bound_arguments": {"path": "a.py", "offset": 0, "limit": 200}}), provider,
        WritePolicy(), max_steps=1)

    def revoke(_decision):
        provider.allowed.clear()
        return "continue"

    steps = collect(kernel, revoke)
    assert provider.executed == []
    assert steps[0]["outcome"]["effect_disposition"] == "NOT_APPLIED"
    assert "no longer available" in steps[0]["outcome"]["error"]["message"]


def test_availability_expansion_cannot_authorize_inflight_proposal():
    provider = Provider()
    provider.allowed.clear()

    class ExpandingDriver(DecisionDriver):
        async def decide(self, context):
            provider.allowed.add("READ_FILE")
            return await super().decide(context)

    kernel = RuntimeKernel(ExpandingDriver({"operation": "READ_FILE", "confidence": 1.0,
        "bound_arguments": {"path": "a.py", "offset": 0, "limit": 200}}), provider,
        WritePolicy(), max_steps=1)
    steps = collect(kernel)
    assert provider.executed == []
    assert "request time" in steps[0]["outcome"]["error"]["message"]
