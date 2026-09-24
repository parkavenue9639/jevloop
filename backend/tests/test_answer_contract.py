"""Answer semantics and recovery wiring; model replies are fixtures, not evaluations."""

import asyncio
import json
from copy import deepcopy

from jevloop.context.projection import rebuild_workspace
from jevloop.context.transcript import Transcript, system_prompt
from jevloop.contracts.arguments import function_schema
from jevloop.contracts.policy import WritePolicy
from jevloop.contracts.schemas import CORE_TOOL_SCHEMAS, llm_tool_schemas
from jevloop.decision.drivers import JevDriver
from jevloop.runtime.kernel import RuntimeKernel
from jevloop.tools.sandbox import SandboxTools


def test_answer_semantics_are_in_the_canonical_schema():
    schema = function_schema(operation="ANSWER")
    assert schema == CORE_TOOL_SCHEMAS[0]
    assert schema == next(item for item in llm_tool_schemas(SandboxTools())
                          if item["function"]["name"] == "ANSWER")
    description = schema["function"]["description"]
    for requirement in (
        "ends the current turn", "necessary clarification", "evidenced limitation",
        "return CANNOT_BIND", "not missing user authorization", "actual runtime restriction",
    ):
        assert requirement in description
    answer = schema["function"]["parameters"]["properties"]["answer"]
    assert "actual tool outcomes" in answer["description"]
    assert "unverified claims" in answer["description"]
    assert answer["minLength"] == 1 and answer["maxLength"] == 20000


def test_cannot_bind_covers_wrong_operations_not_just_missing_evidence():
    function = next(item["function"] for item in llm_tool_schemas(SandboxTools())
                    if item["function"]["name"] == "CANNOT_BIND")
    for requirement in (
        "evidence is insufficient", "selected operation is inappropriate",
        "internal recovery signal", "Use only in response to a parameter request",
    ):
        assert requirement in function["description"]


def test_locked_answer_refusal_recovers_within_the_same_confirmation_turn(monkeypatch):
    """The observed .51 ANSWER route can recover without asking the user again.

    This proves execution/ledger wiring given a refusal, NOT that an actual LLM
    will choose CANNOT_BIND under the updated schema.
    """
    class Provider(SandboxTools):
        def __init__(self):
            super().__init__()
            self.executed = []

        async def execute(self, name, context):
            self.executed.append((name, deepcopy(context.arguments)))
            return {"status": "ready", "action": "write_file(main.py)",
                    "changed_files": ["main.py"]}

    provider = Provider()
    ledger = Transcript(system_prompt(provider), "请新增一个数据查询接口")
    call_id = ledger.append_action("ANSWER", {
        "answer": "方案 A 已明确，但当前只允许 ANSWER，请再次确认后允许 WRITE_FILE。"})
    ledger.append_result(call_id, {"status": "done", "effect_disposition": "SUCCEEDED"})
    requests = []
    replies = [
        ("CANNOT_BIND", {"reason": "The user confirmed the requested edit; writing main.py "
                        "is still required before a final answer. ANSWER is premature."}),
        ("WRITE_FILE", {"path": "main.py", "content": "# fixture query endpoint\n"}),
        ("ANSWER", {"answer": "已写入 main.py；尚未运行验证。"}),
    ]

    async def post(_url, _key, body):
        requests.append(deepcopy(body))
        operation, arguments = replies[len(requests) - 1]
        return {"choices": [{"finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None, "tool_calls": [{
                "id": f"reply-{len(requests)}", "type": "function", "function": {
                    "name": operation, "arguments": json.dumps(arguments)},
            }],
        }}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    async def chooser(*_args, **_kwargs):
        return {"operation": "ANSWER", "phase": "RESPOND", "confidence": 0.51,
                "operation_path_confidence": 0.51, "ambiguity": 0.33,
                "binding_mode": "llm_parameters", "bound_arguments": {},
                "operation_probabilities": {"ANSWER": 1.0}, "target": None,
                "latency_ms": 1, "usage": {}}

    monkeypatch.setattr("jevloop.decision.argument_helper.post_json", post)
    monkeypatch.setattr("jevloop.decision.escalation.post_json", post)
    kernel = RuntimeKernel(
        JevDriver(chooser=chooser, escalate_threshold=0.5, answer_progress_floor=None),
        provider, WritePolicy(), max_steps=3, transcript=ledger,
        workspace=rebuild_workspace(ledger),
    )

    async def run():
        return [step async for step in kernel.run("确认")]

    steps = asyncio.run(run())
    assert steps[0]["outcome"]["effect_disposition"] == "NOT_APPLIED"
    assert steps[0]["outcome"]["error"]["recoverability"] == "recoverable"
    assert steps[1]["escalation"]["reason"] == "recoverable_observation"
    assert steps[1]["decision"]["operation"] == "WRITE_FILE"
    assert provider.executed == [("WRITE_FILE", replies[1][1])]
    assert steps[-1]["final"] == "completed"
    assert steps[2]["outcome"]["answer"] == replies[2][1]["answer"]
    assert len(requests) == 3
    assert all(request["tools"] == llm_tool_schemas(provider) for request in requests)
    assert "[recovery note]" in requests[1]["messages"][-1]["content"]
    calls = [call["function"]["name"] for record in ledger.dump()
             for call in record.get("tool_calls") or []]
    assert calls == ["ANSWER", "WRITE_FILE", "ANSWER"]  # refusal is not executed
    assert ledger.repair() == 0
