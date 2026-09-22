"""Canonical bindings and locked-operation authoring, with no model/network calls."""

import asyncio
import json
from copy import deepcopy

import pytest

from jevloop import model
from jevloop.argument_helper import generate_arguments
from jevloop.arguments import (
    LLM_PARAMETERS,
    argument_target,
    arguments_complete,
    bound_arguments,
    function_schema,
    parameter_schema,
    validate_arguments,
)
from jevloop.drivers import JevDriver, PlainLlmDriver
from jevloop.guardrails import GuardrailDenied, InvalidProposal, WritePolicy
from jevloop.kernel import RuntimeKernel
from jevloop.observations import normalize_observation
from jevloop.projection import intent_fingerprint, rebuild_workspace
from jevloop.state import Workspace
from jevloop.tools.base import ToolSpec
from jevloop.tools.sandbox import SandboxTools
from jevloop.transcript import Transcript, full_tool_schemas, llm_tool_schemas


def spec(name):
    return next(item for item in SandboxTools().specs() if item.name == name)


def call(operation, args, call_id="call1"):
    return {"id": call_id, "type": "function",
            "function": {"name": operation, "arguments": json.dumps(args)}}


def response(calls, finish_reason="tool_calls"):
    return {"choices": [{"finish_reason": finish_reason,
                         "message": {"role": "assistant", "content": None, "tool_calls": calls}}],
            "usage": {"prompt_tokens": 31, "completion_tokens": 7}}


def run_helper(payload, operation="READ_FILE"):
    ledger = Transcript("System", "inspect the project")
    before = deepcopy(ledger.dump())
    calls = []

    async def post(endpoint, _key, body):
        calls.append((endpoint, body))
        return payload

    try:
        result = asyncio.run(generate_arguments(ledger, SandboxTools(), operation, post=post))
    finally:
        # Authoring is not execution and must not create dangling tool calls.
        assert ledger.dump() == before
        assert len(calls) == 1
    return result, calls[0][1]


def test_observed_read_is_complete_but_observed_write_is_partial():
    read = spec("READ_FILE")
    write = spec("WRITE_FILE")
    read_args = bound_arguments(read, "src/main.py")
    write_args = bound_arguments(write, "src/main.py")
    assert read_args["path"] == write_args["path"] == "src/main.py"
    assert arguments_complete(read, read_args)
    assert validate_arguments(read, read_args, Workspace())["path"] == "src/main.py"
    assert not arguments_complete(write, write_args)
    assert arguments_complete(write, {**write_args, "content": "print('hello')"})


def test_direct_bindings_copy_defaults_and_preserve_correlated_batch():
    read = spec("READ_FILE")
    before = deepcopy(read.binding_defaults)
    args = bound_arguments(read, ("src/a.py", "src/b.py"))
    assert args["path"] == ["src/a.py", "src/b.py"]
    assert argument_target(read, args) == ("src/a.py", "src/b.py")
    args["limit"] = 1
    assert read.binding_defaults == before
    assert "path" not in bound_arguments(read, LLM_PARAMETERS)


def test_canonical_tool_schema_is_shared_by_binding_and_plain_llm():
    for item in SandboxTools().specs():
        rendered = next(s for s in full_tool_schemas(SandboxTools())
                        if s["function"]["name"] == item.name)
        assert rendered == function_schema(item)
        assert rendered["function"]["parameters"] == parameter_schema(item)
    # The caller cannot mutate a registered tool schema through its copy.
    copied = parameter_schema(spec("READ_FILE"))
    copied["properties"]["path"] = {"const": "wrong"}
    assert "oneOf" in parameter_schema(spec("READ_FILE"))["properties"]["path"]


def test_empty_observation_does_not_remove_operations_or_llm_binding(monkeypatch):
    ws = Workspace()
    provider = SandboxTools()
    _questions, compiled = model.compile_questions(ws, provider)
    assert "READ_FILE" in compiled.valid_actions
    for criteria in compiled.target_candidates.values():
        assert LLM_PARAMETERS in criteria
    branch = ("INSPECT", "READ_FILE")
    assert LLM_PARAMETERS in compiled.target_candidates[branch]

    async def post(_endpoint, _key, body):
        answers = {}
        for head, question in body["questions"].items():
            if question.get("type") != "choice":
                continue
            offered = question["criteria"]
            preferred = ("INSPECT" if head == "phase" else "READ_FILE"
                         if head == compiled.action_heads.get("INSPECT") else LLM_PARAMETERS)
            chosen = preferred if preferred in offered else next(iter(offered))
            answers[head] = {"choice": chosen, "confidence": 0.99,
                             "probabilities": {key: float(key == chosen) for key in offered}}
        return {"answers": answers}

    monkeypatch.setattr(model, "post_json", post)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only")
    result = asyncio.run(model.choose(ws, "read src/unlisted.py", [], provider=provider))
    assert result["operation"] == "READ_FILE"
    assert result["binding_mode"] == "llm_parameters"
    assert result["bound_arguments"] == {}
    assert not result.get("arbitrated")


def test_candidate_projection_does_not_rank_or_parse_goal_text():
    raw = {"scope": "src", "evidence": "Observed file names.", "references": [
        {"kind": "file", "value": "src/apple.py", "label": "apple"},
        {"kind": "file", "value": "src/orange.py", "label": "orange"},
    ]}
    view = normalize_observation(raw, operation="LIST_FILES", call_id="observed1")
    a = Workspace(goal="Read apple, ignore orange", observation_views=[view])
    b = Workspace(goal="Read orange, ignore apple", observation_views=[deepcopy(view)])
    qa, ca = model.compile_questions(a, SandboxTools())
    qb, cb = model.compile_questions(b, SandboxTools())
    assert ca.target_candidates == cb.target_candidates
    assert qa == qb
    assert a.state()["goal"] != b.state()["goal"]  # Models still see the actual request.


def test_parameter_helper_is_one_call_locks_only_operation_with_full_catalog():
    bound = {"path": "src/observed.py"}
    payload = response([call("READ_FILE", {**bound, "offset": 10, "limit": 5})])
    (args, helper), request = run_helper(payload)
    assert args == {"path": "src/observed.py", "offset": 10, "limit": 5}
    assert helper["kind"] == "parameter_authoring"
    assert helper["usage"] == payload["usage"]
    assert request["parallel_tool_calls"] is False
    assert request["tool_choice"] == "required"
    assert request["tools"] == llm_tool_schemas(SandboxTools())
    assert "src/observed.py" not in request["messages"][-1]["content"]
    assert "const" not in json.dumps(request["tools"])
    assert "const" not in parameter_schema(spec("READ_FILE"))["properties"]["path"]


@pytest.mark.parametrize("payload", [
    response([call("BASH", {"command": "ls"})]),
    response([call("READ_FILE", {"offset": 0})]),
    response([call("READ_FILE", {"path": "src/observed.py"})], "length"),
    response([call("READ_FILE", {"path": "src/observed.py"}), call("READ_FILE", {"path": "other.py"}, "call2")]),
    response([]),
    response([call("CANNOT_BIND", {"reason": "The requested resource is ambiguous."})]),
    response([call("CANNOT_BIND", [])]),
    response([call("READ_FILE", ["src/observed.py"])]),
    response([{"id": "bad", "function": {"name": "READ_FILE", "arguments": "{not-json"}}]),
])
def test_invalid_parameter_response_is_pre_dispatch_with_billed_receipt(payload):
    with pytest.raises(InvalidProposal) as error:
        run_helper(payload)
    assert error.value.helper_info["kind"] == "parameter_authoring"
    assert error.value.helper_info["usage"] == payload["usage"]


def test_write_authors_all_parameters_without_an_inherited_binding():
    (args, helper), _ = run_helper(response([call("WRITE_FILE", {
        "path": "src/new.py", "content": "print('ready')\n",
    })]), operation="WRITE_FILE")
    assert validate_arguments(spec("WRITE_FILE"), args, Workspace()) == args
    assert helper["kind"] == "parameter_authoring"


@pytest.mark.parametrize("path", ["/etc/passwd", "../outside", "src/../../outside", "a\x00b"])
def test_generated_paths_cannot_escape_provider_validator(path):
    with pytest.raises(InvalidProposal):
        validate_arguments(spec("READ_FILE"), {"path": path}, Workspace())


def test_open_path_is_not_restricted_to_observed_shortcut_pool():
    ws = Workspace(files={"known.py": "known.py"})
    normalized = validate_arguments(spec("READ_FILE"), {"path": "src/unobserved.py"}, ws)
    assert normalized["path"] == "src/unobserved.py"


@pytest.mark.parametrize("args", [
    {"path": "x.py", "offset": -1},
    {"path": "x.py", "offset": True},
    {"path": "x.py", "limit": 0},
    {"path": "x.py", "unexpected": "permission granted"},
    {"path": ["a.py", "a.py"]},
])
def test_schema_invalid_arguments_are_rejected(args):
    with pytest.raises(InvalidProposal):
        validate_arguments(spec("READ_FILE"), args, Workspace())


def test_closed_recipient_references_and_policy_are_distinct_gates():
    send = ToolSpec(name="SEND", description="Send to a known recipient", needs_target=True,
                    target_pool="recipients", needs_text=True, write=True, recipient_gate=True)
    ws = Workspace(recipients={"approved": "Approved", "observed_only": "Not authorized"})
    with pytest.raises(InvalidProposal, match="observed compatible reference"):
        validate_arguments(send, {"target": "invented", "content": "hello"}, ws)
    args = validate_arguments(send, {"target": "observed_only", "content": "hello"}, ws)
    policy = WritePolicy(allowed_recipients={"approved"}, write_actions={"SEND"}, recipient_gated={"SEND"})
    with pytest.raises(GuardrailDenied):
        policy.check({"operation": "SEND", "target": args["target"], "confidence": 0.99}, ws.recipients)
    # Parameter authoring is not arbitration and cannot waive low operation confidence.
    with pytest.raises(GuardrailDenied, match="confidence"):
        policy.check({"operation": "SEND", "target": "approved", "confidence": 0.1,
                      "binding_mode": "llm_parameters"}, ws.recipients)


def test_canonical_fingerprint_distinguishes_read_ranges():
    base = {"operation": "READ_FILE", "arguments": {"path": "src/a.py", "offset": 0, "limit": 10}}
    other = deepcopy(base)
    other["arguments"]["offset"] = 10
    same = {"operation": "READ_FILE", "arguments": {"limit": 10, "offset": 0, "path": "src/a.py"}}
    assert intent_fingerprint(base) != intent_fingerprint(other)
    assert intent_fingerprint(base) == intent_fingerprint(same)


def test_complete_direct_binding_materializes_without_any_authoring():
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("A complete direct binding must not call an LLM")

    kernel = RuntimeKernel(None, SandboxTools(), WritePolicy(), arguer=forbidden, texter=forbidden,
                           transcript=Transcript("system", "goal"))
    read = spec("READ_FILE")
    decision = {"operation": "READ_FILE", "binding_mode": "observed",
                "bound_arguments": bound_arguments(read, "src/observed.py")}
    intent = asyncio.run(kernel._materialize_arguments(decision, read))
    assert intent["arguments"]["path"] == "src/observed.py"
    assert intent["helper"] is None
    assert kernel.transcript.dump()[-1]["role"] == "user"


def test_explicit_llm_parameters_is_honored_even_if_empty_args_match_schema():
    requests = []

    async def arguer(_ledger, provider, operation):
        assert isinstance(provider, SandboxTools)
        requests.append(operation)
        return {"path": "src", "offset": 100, "limit": 20}, {
            "kind": "parameter_authoring", "usage": {}, "latency_ms": 0, "model": "test"}

    kernel = RuntimeKernel(None, SandboxTools(), WritePolicy(), arguer=arguer,
                           transcript=Transcript("system", "goal"))
    listing = spec("LIST_FILES")
    assert arguments_complete(listing, {})
    decision = {"operation": "LIST_FILES", "binding_mode": "llm_parameters", "bound_arguments": {}}
    intent = asyncio.run(kernel._materialize_arguments(decision, listing))
    assert requests == ["LIST_FILES"]
    assert intent["arguments"] == {"path": "src", "offset": 100, "limit": 20}
    assert not decision.get("arbitrated")


def test_materialized_arguments_do_not_alias_decision_or_defaults():
    kernel = RuntimeKernel(None, SandboxTools(), WritePolicy())
    read = spec("READ_FILE")
    decision = {"operation": "READ_FILE", "bound_arguments": bound_arguments(read, "safe.py")}
    intent = asyncio.run(kernel._materialize_arguments(decision, read))
    decision["arguments"]["path"] = "different.py"
    decision["bound_arguments"]["limit"] = 999
    assert intent["arguments"] == {"path": "safe.py", "offset": 0, "limit": 200}


def test_normalized_native_call_and_frozen_dispatch_have_identical_arguments():
    class Provider:
        def __init__(self):
            self.received = []

        def specs(self):
            return [spec("READ_FILE")]

        def available(self, _workspace):
            return {"READ_FILE"}

        async def execute(self, _operation, ctx):
            self.received.append(deepcopy(ctx.arguments))
            return {"status": "ready"}

    async def llm(_transcript, _schemas):
        return response([call("READ_FILE", {"path": "safe.py"})])["choices"][0]["message"], {}, {}

    provider = Provider()
    kernel = RuntimeKernel(PlainLlmDriver(llm=llm), provider, WritePolicy(), max_steps=1)

    def callback(decision):
        decision["operation"] = "BASH"
        decision["arguments"]["path"] = "different.py"
        decision["target"] = "different.py"
        return "continue"

    async def run():
        return [step async for step in kernel.run("read the file", before_step=callback)]

    asyncio.run(run())
    assert provider.received == [{"path": "safe.py", "offset": 0, "limit": 200}]
    stored = next(message for message in kernel.transcript.dump() if message.get("tool_calls"))
    assert json.loads(stored["tool_calls"][0]["function"]["arguments"]) == provider.received[0]
    assert rebuild_workspace(kernel.transcript).history[0]["target"] == "safe.py"


def test_kernel_rejects_partial_direct_invocation_without_calling_author():
    async def invalid(*_args):
        raise AssertionError("Partial direct invocations must never call the LLM")

    kernel = RuntimeKernel(None, SandboxTools(), WritePolicy(), arguer=invalid)
    with pytest.raises(InvalidProposal, match="Direct invocation must have complete arguments"):
        asyncio.run(kernel._materialize_arguments(
            {"operation": "WRITE_FILE", "bound_arguments": {"path": "safe.py"}}, spec("WRITE_FILE")))


def test_global_question_budget_rejects_before_network(monkeypatch):
    monkeypatch.setattr(model, "MAX_QUESTION_HEADS", 1)
    with pytest.raises(model.InvalidModelResponse, match="invocation budget"):
        model.compile_questions(Workspace(), SandboxTools())


def test_legacy_string_error_normalizes_as_recoverable_observation():
    kernel = RuntimeKernel(None, SandboxTools(), WritePolicy())
    outcome = kernel._normalize_outcome(None, {"status": "failed", "error": "FileNotFoundError"})
    assert outcome["error"]["recoverability"] == "recoverable"
    assert outcome["provider_error"] == "FileNotFoundError"


def test_non_idempotent_normalizer_cannot_change_committed_arguments():
    tool = ToolSpec(name="CURSOR", description="cursor", parameters={
        "type": "object", "properties": {"cursor": {"type": "integer"}},
        "required": ["cursor"], "additionalProperties": False},
        argument_validator=lambda args: {"cursor": args["cursor"] + 1})
    kernel = RuntimeKernel(None, SandboxTools(), WritePolicy())
    with pytest.raises(InvalidProposal, match="normalizers must be idempotent"):
        asyncio.run(kernel._materialize_arguments(
            {"operation": "CURSOR", "arguments": {"cursor": 1}}, tool))


def test_canonical_answer_uses_locked_schema_not_legacy_freeform():
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("canonical ANSWER must not use the legacy free-form helper")

    async def arguer(_ledger, provider, operation):
        assert isinstance(provider, SandboxTools) and operation == "ANSWER"
        assert function_schema(None, operation)["function"]["parameters"]["required"] == ["answer"]
        return {"answer": "Grounded answer"}, {"kind": "authoring"}

    kernel = RuntimeKernel(None, SandboxTools(), WritePolicy(), arguer=arguer, texter=forbidden,
                           transcript=Transcript("system", "goal"))
    intent = asyncio.run(kernel._materialize_arguments(
        {"operation": "ANSWER", "bound_arguments": {}, "binding_mode": "llm_parameters"}, None))
    assert intent["arguments"] == {"answer": "Grounded answer"}
    assert intent["text"] == "Grounded answer"


def test_observed_reference_runs_through_jev_kernel_without_llm():
    class Runtime:
        def __init__(self):
            self.calls = []

        async def read_range(self, path, offset, limit):
            self.calls.append((path, offset, limit))
            return {"content": "observed content", "truncated": False, "next_offset": None}

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("Direct observed binding must not invoke the LLM")

    async def choose(workspace, *_args, **_kwargs):
        assert "src/a.py" in workspace.pool_entries("files")
        return {"operation": "READ_FILE", "phase": "INSPECT", "confidence": 1.0,
                "operation_path_confidence": 1.0, "binding_mode": "observed",
                "bound_arguments": bound_arguments(spec("READ_FILE"), "src/a.py")}

    runtime = Runtime()
    workspace = Workspace()
    workspace.file_observation_mode = True
    workspace.observation_views = [normalize_observation({"scope": "src", "references": [
        {"kind": "file", "value": "src/a.py"}]}, operation="LIST_FILES", call_id="prior")]
    kernel = RuntimeKernel(JevDriver(chooser=choose, adjudicator=forbidden), SandboxTools(runtime),
                           WritePolicy(), workspace=workspace, arguer=forbidden, texter=forbidden,
                           max_steps=1)

    async def run():
        return [step async for step in kernel.run("Inspect the observed file")]

    steps = asyncio.run(run())
    assert steps[0]["outcome"]["status"] == "ready"
    assert runtime.calls == [("src/a.py", 0, 200)]
    assert kernel.metrics.helper_calls == []
    assert "src/a.py" in rebuild_workspace(kernel.transcript).pool_entries("files")
