"""RuntimeKernel owns execution semantics for every decision driver."""

import asyncio
import json

import pytest

from jevloop import model
from jevloop.drivers import DriverProposal, DriverRejected, JevDriver, PlainLlmDriver
from jevloop.guardrails import WritePolicy
from jevloop.kernel import RuntimeKernel
from jevloop.projection import rebuild_workspace
from jevloop.state import HISTORY_CAP, Workspace
from jevloop.tools.base import ToolSpec
from jevloop.transcript import Transcript, system_prompt


class SequenceDriver:
    name = "test"

    def __init__(self, *decisions):
        self.decisions = list(decisions)

    async def decide(self, _context):
        decision = self.decisions.pop(0)
        return DriverProposal(decision=decision, base_decision=decision)


class Provider:
    def __init__(self, specs=None):
        self._specs = specs or [ToolSpec(name="PING", description="ping")]
        self.executed = []

    def specs(self):
        return self._specs

    def available(self, _workspace):
        return {spec.name for spec in self._specs}

    async def execute(self, name, ctx):
        self.executed.append((name, ctx.target, ctx.text))
        return {"status": "ready", "action": name.lower()}


def decision(operation="PING", target=None, **extra):
    return {
        "operation": operation,
        "target": target,
        "confidence": 0.9,
        "operation_probabilities": {operation: 1.0},
        "target_probabilities": {},
        "latency_ms": 1,
        "usage": {},
        **extra,
    }


def collect(kernel, before_step=None):
    async def run():
        return [step async for step in kernel.run("goal", before_step=before_step)]

    return asyncio.run(run())


def test_budget_and_execution_are_owned_by_kernel():
    provider = Provider()
    kernel = RuntimeKernel(
        SequenceDriver(decision(), decision()), provider, WritePolicy(), max_steps=1)

    steps = collect(kernel)

    assert provider.executed == [("PING", None, None)]
    assert steps[0]["outcome"]["action"] == "ping"
    assert "Step budget exhausted" in steps[1]["denied"]
    assert steps[1]["termination"] == "STEP_BUDGET_EXHAUSTED"
    assert steps[-1]["final"] == "stopped"
    assert kernel.budget.steps == 1  # one reserved attempt; no max_steps+1


def test_before_step_aborts_every_driver_before_budget_or_execution():
    provider = Provider()
    kernel = RuntimeKernel(SequenceDriver(decision()), provider, WritePolicy())

    steps = collect(kernel, before_step=lambda _decision: "abort")

    assert provider.executed == []
    assert kernel.budget.steps == 1  # the aborted attempt consumed its reserved step
    assert steps[0]["aborted"] is True
    assert steps[0]["outcome"]["effect_disposition"] == "NOT_APPLIED"
    assert steps[-1]["final"] == "stopped"


def test_denied_native_tool_call_is_answered_in_transcript():
    spec = ToolSpec(name="SEND_MESSAGE", description="send", needs_target=True,
                    target_pool="recipients", needs_text=True,
                    text_instruction="message", write=True, recipient_gate=True)
    provider = Provider([spec])
    workspace = Workspace()
    workspace.recipients["forbidden"] = "forbidden"
    args = {"target": "forbidden", "content": "hello"}
    assistant = {"role": "assistant", "content": None, "tool_calls": [{
        "id": "call-1", "type": "function", "function": {
            "name": "SEND_MESSAGE", "arguments": json.dumps(args),
        },
    }]}
    proposed = decision(
        "SEND_MESSAGE", "forbidden", arbitrated=True,
        ledger_call_id="call-1", ledger_content="hello")

    class AssistantDriver(SequenceDriver):
        async def decide(self, context):
            proposal = await super().decide(context)
            proposal.assistant_message = assistant
            return proposal

    kernel = RuntimeKernel(
        AssistantDriver(proposed), provider,
        WritePolicy(allowed_recipients={"allowed"}), workspace=workspace)
    steps = collect(kernel)

    assert provider.executed == []
    assert "not in the allowed set" in steps[0]["denied"]
    denial = steps[0]["outcome"]
    assert denial["effect_disposition"] == "NOT_APPLIED"
    assert denial["error"]["code"] == "RECIPIENT_DENIED"
    assert denial["error"]["recoverability"] == "terminal"
    assert steps[-1]["final"] == "stopped"  # hard policy denial stays terminal
    messages = kernel.transcript.messages()
    assert messages[-2]["role"] == "assistant"
    assert messages[-1]["role"] == "tool"
    assert messages[-1]["tool_call_id"] == "call-1"
    assert "not in the allowed set" in messages[-1]["content"]


def test_plain_missing_required_content_is_answered_never_executed():
    provider = Provider([
        ToolSpec(name="WRITE", description="write", needs_text=True,
                 text_instruction="body", write=True),
    ])

    async def missing_content(_transcript, _schemas):
        message = {"role": "assistant", "content": None, "tool_calls": [{
            "id": "call-missing", "type": "function", "function": {
                "name": "WRITE",
                "arguments": json.dumps({"content": "```\n```"}),
            },
        }]}
        return message, {"model": "fake", "latency_ms": 1, "usage": {}}, {}

    kernel = RuntimeKernel(
        PlainLlmDriver(llm=missing_content), provider, WritePolicy(), max_steps=1)
    steps = collect(kernel)

    assert provider.executed == []
    assert "without content" in steps[0]["denied"]
    # the provider-valid call is committed and answered exactly once with the
    # typed rejection — no ghost-written execution, no dangling id
    messages = kernel.transcript.messages()
    assert [message["role"] for message in messages] == [
        "system", "user", "assistant", "tool",
    ]
    assert messages[-1]["tool_call_id"] == "call-missing"
    result = json.loads(messages[-1]["content"])
    assert result["error"]["code"] == "INVALID_PROPOSAL"
    assert result["effect_disposition"] == "NOT_APPLIED"
    assert len(kernel.metrics.helper_calls) == 1
    assert kernel.metrics.helper_calls[0]["model"] == "fake"


def test_plain_multiple_tool_calls_commit_only_first_per_step():
    provider = Provider([
        ToolSpec(name="PING", description="ping"),
    ])
    messages = [
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call-ping", "type": "function",
             "function": {"name": "PING", "arguments": "{}"}},
            {"id": "call-done-ignored", "type": "function",
             "function": {"name": "DONE", "arguments": "{}"}},
        ]},
        {"role": "assistant", "content": "finished", "tool_calls": []},
    ]

    async def llm(_transcript, _schemas):
        return messages.pop(0), {"model": "fake", "latency_ms": 1, "usage": {}}, {}

    kernel = RuntimeKernel(
        PlainLlmDriver(llm=llm), provider, WritePolicy(), max_steps=3)
    steps = collect(kernel)

    assert provider.executed == [("PING", None, None)]
    assert steps[-1]["final"] == "completed"
    assistant_calls = [
        message["tool_calls"] for message in kernel.transcript.messages()
        if message.get("role") == "assistant" and message.get("tool_calls")
    ]
    assert kernel.workspace.answer == "finished"
    assert [calls[0]["id"] for calls in assistant_calls[:1]] == ["call-ping"]
    assert len(assistant_calls) == 2
    assert all(len(calls) == 1 for calls in assistant_calls)
    assert steps[0]["model_calls"][0]["kind"] == "plain_decision"
    assert steps[0]["model_calls"][0]["response"]["tool_calls"][0]["id"] == "call-ping"


def test_answer_publishes_materialized_text_to_workspace():
    kernel = RuntimeKernel(
        SequenceDriver(decision("ANSWER", ledger_content="hello")),
        Provider(),
        WritePolicy(),
    )

    steps = collect(kernel)

    assert steps[-1]["final"] == "completed"
    assert kernel.workspace.answer == "hello"


def test_new_transcript_starts_with_cache_scope():
    kernel = RuntimeKernel(
        SequenceDriver(decision("DONE")),
        Provider(),
        WritePolicy(),
        cache_scope="a" * 24,
    )

    collect(kernel)

    system = kernel.transcript.messages()[0]["content"]
    assert system.startswith(f"[cache-scope:{'a' * 24}]\n")


def test_transient_model_unavailable_is_observed_then_retried():
    class FlakyDriver:
        name = "test"

        def __init__(self):
            self.calls = 0

        async def decide(self, _context):
            self.calls += 1
            if self.calls == 1:
                raise model.ModelUnavailable(
                    "Model connection failed; no action executed.")
            chosen = decision("DONE")
            return DriverProposal(decision=chosen, base_decision=chosen)

    driver = FlakyDriver()
    kernel = RuntimeKernel(driver, Provider(), WritePolicy(), max_steps=3)

    steps = collect(kernel)

    failure = steps[0]["outcome"]
    assert failure["effect_disposition"] == "NOT_APPLIED"
    assert failure["error"]["code"] == "MODEL_UNAVAILABLE"
    assert failure["error"]["recoverability"] == "recoverable"
    assert driver.calls == 2
    assert steps[-1]["final"] == "completed"


def test_recoverable_tool_failure_is_recorded_and_the_run_continues():
    class FailingProvider(Provider):
        async def execute(self, _name, _ctx):
            raise RuntimeError("boom")

    kernel = RuntimeKernel(
        SequenceDriver(decision(), decision("DONE")),
        FailingProvider(),
        WritePolicy(),
        max_steps=2,
    )
    steps = collect(kernel)
    assert steps[0]["outcome"]["status"] == "failed"
    assert steps[0]["outcome"]["effect_disposition"] == "NOT_APPLIED"
    assert steps[0]["outcome"]["error"]["code"] == "EXECUTION_FAILED"
    assert steps[0]["outcome"]["error"]["recoverability"] == "recoverable"
    tool_results = [
        message for message in kernel.transcript.messages() if message.get("role") == "tool"
    ]
    assert any("RuntimeError: boom" in message["content"] for message in tool_results)
    # the typed diagnostic is projected into the Jev view: the next decision's
    # recent_steps carry the failure even after the run later completed
    recent = kernel.workspace.state()["recent_steps"]
    assert any((step.get("error") or {}).get("code") == "EXECUTION_FAILED"
               for step in recent)
    assert kernel.workspace.history[0]["error"]["code"] == "EXECUTION_FAILED"


def test_materialized_intent_is_durable_before_dispatch():
    spec = ToolSpec(name="WRITE", description="write", needs_text=True,
                    text_instruction="body", write=True)
    events = []
    checkpoints = []

    class IntentProvider(Provider):
        async def execute(self, name, ctx):
            self.executed.append((name, ctx.text, ctx.intent_id, ctx.idempotency_key))
            return {"status": "ready", "action": "write"}
    provider = IntentProvider([spec])
    proposed = decision("WRITE", ledger_content="payload")
    kernel = RuntimeKernel(
        SequenceDriver(proposed, decision("DONE")),
        provider,
        WritePolicy(),
        max_steps=1,
        event_sink=events.append,
        checkpoint=lambda transcript: checkpoints.append(len(transcript.messages())),
    )

    steps = collect(kernel)

    assert [event["type"] for event in events[:5]] == [
        "attempt_started", "decision_ready", "intent",
        "dispatch_started", "observation",
    ]
    assert events[1] == {
        "type": "decision_ready",
        "attempt_id": events[0]["attempt_id"],
        "operation": "WRITE",
        "needs_authoring": False,
    }
    intent = events[2]
    assert intent["text_length"] == len("payload")
    observation = events[4]
    assert events[3]["intent_id"] == observation["intent_id"] == intent["intent_id"]
    executed = provider.executed[0]
    assert executed[:2] == ("WRITE", "payload")
    assert executed[2] == intent["intent_id"]
    assert len(executed[3]) == 50
    assert observation["disposition"] == "SUCCEEDED"
    assert observation["attempt_id"] == events[0]["attempt_id"]
    assert steps[0]["intent_id"] == intent["intent_id"]
    assert len(checkpoints) >= 4


def test_intent_persistence_failure_prevents_dispatch():
    provider = Provider()

    def broken_sink(_event):
        raise OSError("disk full")

    kernel = RuntimeKernel(
        SequenceDriver(decision()), provider, WritePolicy(), event_sink=broken_sink)

    try:
        collect(kernel)
    except OSError as error:
        assert "disk full" in str(error)
    else:
        raise AssertionError("durability failure must escape")
    assert provider.executed == []


def test_failed_write_effect_is_recorded_unknown():
    class FailingWriteProvider(Provider):
        async def execute(self, _name, _ctx):
            raise RuntimeError("transport lost")

    spec = ToolSpec(name="WRITE", description="write", write=True)
    events = []
    kernel = RuntimeKernel(
        SequenceDriver(decision("WRITE")),
        FailingWriteProvider([spec]),
        WritePolicy(),
        event_sink=events.append,
    )

    steps = collect(kernel)

    observation = next(event for event in events if event["type"] == "observation")
    assert observation["disposition"] == "UNKNOWN"
    assert observation["outcome"]["effect_disposition"] == "UNKNOWN"
    assert observation["outcome"]["error"]["code"] == "EFFECT_UNKNOWN"
    assert observation["outcome"]["error"]["recoverability"] == "unsafe"
    assert steps[-1]["final"] == "stopped"


def test_ambiguity_gate_skips_benign_low_confidence_escalation():
    arbitrated = []

    async def adjudicator(_transcript, decision_in, _provider, _actions, recovery=None):
        arbitrated.append(decision_in.get("ambiguity"))
        return {
            "valid": True, "action": "PING", "target": None, "call_id": "call-1",
            "content": None, "note": "[fast-decision note]", "latency_ms": 1,
            "usage": {}, "request": {}, "response": {}, "message": None,
        }

    def driver_with(ambiguity):
        async def chooser(_workspace, _goal, _history, provider=None):
            return decision("PING", confidence=0.4, ambiguity=ambiguity,
                            phase="INSPECT")
        return JevDriver(escalate_threshold=0.5, ambiguity_gate=0.5,
                         chooser=chooser, adjudicator=adjudicator)

    kernel = RuntimeKernel(driver_with(0.2), Provider(), WritePolicy(), max_steps=1)
    collect(kernel)
    assert arbitrated == []  # low confidence but uncontested: direct execution

    kernel = RuntimeKernel(driver_with(0.9), Provider(), WritePolicy(), max_steps=1)
    collect(kernel)
    assert arbitrated == [0.9]  # contested: the LLM adjudicates


def test_ambiguity_gate_never_relaxes_mutating_operation_confidence():
    arbitrated = []
    provider = Provider([
        ToolSpec(name="BASH", description="bash", mutates_workspace=True),
    ])

    async def chooser(_workspace, _goal, _history, provider=None):
        return decision("BASH", confidence=0.4, ambiguity=0.1, phase="VERIFY")

    async def adjudicator(_transcript, _decision, _provider, _actions, recovery=None):
        arbitrated.append(True)
        return {
            "valid": True, "action": "BASH", "target": None, "call_id": "call-b",
            "content": None, "note": "[fast-decision note]", "latency_ms": 1,
            "usage": {}, "request": {}, "response": {}, "message": None,
        }

    kernel = RuntimeKernel(
        JevDriver(
            escalate_threshold=0.5,
            ambiguity_gate=0.5,
            chooser=chooser,
            adjudicator=adjudicator,
        ),
        provider,
        WritePolicy(),
        max_steps=1,
    )
    collect(kernel)

    assert arbitrated == [True]
    assert provider.executed == [("BASH", None, None)]


def test_premature_answer_below_progress_floor_escalates():
    arbitrated = []

    async def chooser(_workspace, _goal, _history, provider=None):
        return decision("ANSWER", confidence=0.95,
                        progress={"score": 1.0}, phase="RESPOND")

    async def adjudicator(_transcript, _decision, _provider, _actions, recovery=None):
        arbitrated.append(True)
        return {
            "valid": True, "action": "ANSWER", "target": None, "call_id": "call-a",
            "content": "verified answer", "note": "[fast-decision note]",
            "latency_ms": 1, "usage": {}, "request": {}, "response": {},
            "message": None,
        }

    kernel = RuntimeKernel(
        JevDriver(escalate_threshold=0.5, answer_progress_floor=2.5,
                  chooser=chooser, adjudicator=adjudicator),
        Provider(), WritePolicy(), max_steps=2)
    steps = collect(kernel)

    assert arbitrated == [True]
    assert steps[0]["escalation"]["reason"] == "premature_answer"
    assert kernel.workspace.answer == "verified answer"

    # without the floor the same confident ANSWER is delivered directly
    async def texter(_transcript, _instruction):
        return "direct answer", None

    arbitrated.clear()
    kernel = RuntimeKernel(
        JevDriver(escalate_threshold=0.5, chooser=chooser, adjudicator=adjudicator),
        Provider(), WritePolicy(), max_steps=2, texter=texter)
    collect(kernel)
    assert arbitrated == []
    assert kernel.workspace.answer == "direct answer"


def test_progress_floor_trusts_answers_after_committed_work():
    """The floor guards only the turn's first effect: once work is committed,
    a later low-progress ANSWER goes straight through (bounded cost)."""

    async def chooser(_workspace, _goal, _history, provider=None):
        if not getattr(chooser, "calls", None):
            chooser.calls = 1
            return decision("PING", confidence=0.95, progress={"score": 1.0},
                            phase="INSPECT")
        return decision("ANSWER", confidence=0.95, progress={"score": 1.0},
                        phase="RESPOND")

    async def adjudicator(_transcript, _decision, _provider, _actions, recovery=None):
        raise AssertionError("no arbitration expected after committed work")

    async def texter(_transcript, _instruction):
        return "trusted answer", None

    kernel = RuntimeKernel(
        JevDriver(escalate_threshold=0.5, answer_progress_floor=2.5,
                  chooser=chooser, adjudicator=adjudicator),
        Provider(), WritePolicy(), max_steps=2, texter=texter)
    steps = collect(kernel)

    assert steps[-1]["final"] == "completed"
    assert kernel.workspace.answer == "trusted answer"


def test_operation_name_echo_is_rejected_as_malformed_authoring():
    """Fast-path authoring once echoed 'READ_FILE main.py' as a bash command;
    a catalog-name first line is now a typed recoverable failure, and a real
    command goes straight through."""
    provider = Provider([
        ToolSpec(name="BASH", description="bash", needs_text=True,
                 text_instruction="b", mutates_workspace=True),
        ToolSpec(name="READ_FILE", description="read", needs_target=True,
                 target_pool="files"),
    ])

    async def echo_texter(_transcript, _instruction):
        return "READ_FILE main.py", None

    kernel = RuntimeKernel(
        SequenceDriver(decision("BASH"), decision("BASH"), decision("BASH")),
        provider, WritePolicy(), texter=echo_texter, max_steps=3)
    steps = collect(kernel)

    assert provider.executed == []
    assert "operation name" in steps[0]["denied"]

    async def real_texter(_transcript, _instruction):
        return "bash script.sh", None

    kernel = RuntimeKernel(
        SequenceDriver(decision("BASH")),
        provider, WritePolicy(), texter=real_texter, max_steps=1)
    steps = collect(kernel)
    assert provider.executed == [("BASH", None, "bash script.sh")]
    assert steps[0]["outcome"]["action"].startswith("bash")


def test_auto_acknowledge_unknown_unfreezes_non_live_continuation():
    from jevloop.transcript import Transcript, system_prompt

    def frozen_session():
        transcript = Transcript(system_prompt(Provider()), "earlier goal")
        call_id = transcript.append_action("BASH", {"command": "restart"})
        transcript.append_result(call_id, {
            "status": "stopped", "effect_disposition": "UNKNOWN"})
        return transcript

    frozen = RuntimeKernel(
        SequenceDriver(decision("PING")), Provider(), WritePolicy(),
        transcript=frozen_session(), max_steps=1)
    steps = collect(frozen)
    assert steps[0]["decision"]["operation"] == "RESTORE"
    assert steps[-1]["final"] == "stopped"

    transcript = frozen_session()
    kernel = RuntimeKernel(
        SequenceDriver(decision("PING")), Provider(), WritePolicy(),
        transcript=transcript, auto_acknowledge_unknown=True, max_steps=1)
    steps = collect(kernel)

    assert steps[0]["decision"]["operation"] == "PING"  # continued, not frozen
    assert any("auto-acknowledged" in str(message.get("content", ""))
               for message in transcript.messages())


def test_auto_acknowledge_rebuilds_repaired_unknown_for_jev_state():
    from jevloop.transcript import Transcript, system_prompt

    transcript = Transcript(system_prompt(Provider()), "earlier goal")
    transcript.append_action("PING", {})
    observed = []

    async def chooser(workspace, _goal, _history, provider=None):
        observed.append(workspace.state())
        return decision("DONE")

    kernel = RuntimeKernel(
        JevDriver(chooser=chooser),
        Provider(),
        WritePolicy(),
        transcript=transcript,
        auto_acknowledge_unknown=True,
        max_steps=1,
    )
    collect(kernel)

    assert any(
        step.get("disposition") == "UNKNOWN"
        for step in observed[0]["recent_steps"]
    )


def test_write_below_policy_threshold_escalates_before_guardrail():
    async def chooser(_workspace, _goal, _history, provider=None):
        return decision(
            "WRITE",
            confidence=0.55,
            operation_probabilities={"WRITE": 1.0},
            phase="ACT",
        )

    async def adjudicator(_transcript, _decision, _provider, _actions):
        return {
            "valid": True,
            "action": "WRITE",
            "target": None,
            "call_id": "write-1",
            "content": None,
            "note": "[fast-decision note]",
            "latency_ms": 1,
            "usage": {},
            "request": {},
            "response": {},
            "message": None,
        }

    provider = Provider([
        ToolSpec(name="WRITE", description="write", write=True, phases=("ACT",)),
    ])
    kernel = RuntimeKernel(
        JevDriver(
            escalate_threshold=0.5,
            chooser=chooser,
            adjudicator=adjudicator,
        ),
        provider,
        WritePolicy(min_confidence=0.6),
        max_steps=1,
    )

    steps = collect(kernel)

    assert provider.executed == [("WRITE", None, None)]
    assert steps[0]["decision"]["escalated"] is True



def test_invalid_arbitration_rejects_without_executing_uncertain_action():
    async def chooser(_workspace, _goal, _history, provider=None):
        return {
            "operation": "BLOCKED",
            "target": None,
            "confidence": 0.2,
            "operation_probabilities": {"BLOCKED": 0.6, "ANSWER": 0.4},
            "target_probabilities": {},
            "latency_ms": 1,
            "usage": {},
            "request": {},
        }

    arbitration_calls = []

    async def invalid_adjudicator(_transcript, _decision, _provider, _actions,
                                  recovery=None):
        arbitration_calls.append(recovery)
        return {
            "valid": False,
            "note": "[fast-decision note] invalid arbitration",
            "latency_ms": 1,
            "usage": {},
            "request": {"messages": ["arbitration-input"]},
            "response": {"content": "invalid"},
        }

    async def texter(_transcript, _instruction):
        raise AssertionError("invalid arbitration must not reach authoring")

    kernel = RuntimeKernel(
        JevDriver(
            escalate_threshold=0.5,
            chooser=chooser,
            adjudicator=invalid_adjudicator,
        ),
        Provider(),
        WritePolicy(),
        texter=texter,
    )
    steps = collect(kernel)

    assert steps[0]["decision"]["operation"] == "BLOCKED"
    assert "Invalid arbitration" in steps[0]["denied"]
    assert steps[0]["outcome"]["error"]["code"] == "INVALID_PROPOSAL"
    assert steps[0]["outcome"]["error"]["recoverability"] == "recoverable"
    assert kernel.workspace.answer == ""
    assert [call["kind"] for call in steps[0]["model_calls"]] == [
        "jev_decision", "arbitration",
    ]
    assert steps[0]["model_calls"][1]["request"]["messages"] == [
        "arbitration-input",
    ]
    # recoverable, but bounded: the identical failed signature terminates at the
    # generic no-progress limit instead of looping (or falling back) forever
    rejected = [step for step in steps
                if (step.get("outcome", {}).get("error") or {}).get("code")
                == "INVALID_PROPOSAL"]
    assert len(rejected) == 3
    assert len(arbitration_calls) == 3
    assert steps[-2]["termination"] == "NO_PROGRESS_LIMIT"
    assert steps[-1]["final"] == "stopped"


def test_parallel_tool_calls_in_arbitration_leave_the_ledger_answer_complete():
    """Providers ignore parallel_tool_calls=False; only the first call runs, but
    every sibling id must hold a tool result before the next LLM request."""

    async def chooser(_workspace, _goal, _history, provider=None):
        if not getattr(chooser, "calls", None):
            chooser.calls = 1
            return {
                "operation": "PING", "target": None, "confidence": 0.2,
                "operation_probabilities": {"PING": 0.6, "DONE": 0.4},
                "target_probabilities": {}, "latency_ms": 1, "usage": {}, "request": {},
            }
        return {
            "operation": "DONE", "target": None, "confidence": 0.99,
            "operation_probabilities": {"DONE": 1.0},
            "target_probabilities": {}, "latency_ms": 1, "usage": {}, "request": {},
        }

    raw_message = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call-0", "type": "function",
         "function": {"name": "PING", "arguments": "{}"}},
        {"id": "call-1", "type": "function",
         "function": {"name": "PING", "arguments": "{}"}},
        {"id": "call-2", "type": "function",
         "function": {"name": "DONE", "arguments": "{}"}},
    ]}

    async def adjudicator(_transcript, _decision, _provider, _actions):
        return {
            "valid": True, "action": "PING", "target": None,
            "call_id": "call-0", "content": None, "note": "[fast-decision note]",
            "latency_ms": 1, "usage": {}, "request": {}, "response": {},
            "message": raw_message,
        }

    provider = Provider()
    kernel = RuntimeKernel(
        JevDriver(escalate_threshold=0.5, chooser=chooser, adjudicator=adjudicator),
        provider, WritePolicy(), max_steps=2)
    steps = collect(kernel)

    assert provider.executed == [("PING", None, None)]  # only the first call ran
    assert steps[-1]["final"] == "completed"
    messages = kernel.transcript.messages()
    assistant = next(m for m in messages if m.get("tool_calls"))
    assert [call["id"] for call in assistant["tool_calls"]] == [
        "call-0", "call-1", "call-2"]
    answered = set()
    for message in messages[messages.index(assistant) + 1:]:
        if message.get("role") != "tool":
            break
        answered.add(message.get("tool_call_id"))
    assert answered == {"call-0", "call-1", "call-2"}
    superseded = [m for m in messages if m.get("role") == "tool"
                  and "superseded" in (m.get("content") or "")]
    assert len(superseded) == 2


def test_repeat_guard_uses_materialized_arguments_not_only_operation():
    spec = ToolSpec(
        name="EXEC", description="execute", needs_text=True, text_instruction="command")
    provider = Provider([spec])
    kernel = RuntimeKernel(
        SequenceDriver(
            decision("EXEC", ledger_content="python --version"),
            decision("EXEC", ledger_content="python3 --version"),
            decision("EXEC", ledger_content="python -VV"),
            decision("DONE"),
        ),
        provider,
        WritePolicy(),
        max_steps=4,
    )

    steps = collect(kernel)

    assert [text for _name, _target, text in provider.executed] == [
        "python --version",
        "python3 --version",
        "python -VV",
    ]
    assert steps[-1]["final"] == "completed"


def test_repeat_guard_refuses_third_identical_intent_as_recoverable_observation():
    spec = ToolSpec(
        name="EXEC", description="execute", needs_text=True, text_instruction="command")
    provider = Provider([spec])
    same = lambda: decision("EXEC", ledger_content="python --version")
    kernel = RuntimeKernel(
        SequenceDriver(same(), same(), same(), decision("DONE")),
        provider,
        WritePolicy(),
        max_steps=4,
    )

    steps = collect(kernel)

    assert len(provider.executed) == 2  # the third identical intent never dispatches
    refusal = steps[2]
    assert "same arguments and observation" in refusal["denied"]
    assert refusal["outcome"]["effect_disposition"] == "NOT_APPLIED"
    assert refusal["outcome"]["error"]["code"] == "DUPLICATE_NO_PROGRESS"
    assert refusal["outcome"]["error"]["recoverability"] == "recoverable"
    # the refusal is durable evidence for both models, then the run continues
    recent = kernel.workspace.state()["recent_steps"]
    assert any((step.get("error") or {}).get("code") == "DUPLICATE_NO_PROGRESS"
               for step in recent)
    tool_messages = [message for message in kernel.transcript.messages()
                     if message.get("role") == "tool"]
    assert any("same arguments and observation" in message["content"]
               for message in tool_messages)
    assert steps[-1]["final"] == "completed"  # a different action still runs


def test_workspace_bash_does_not_consume_external_write_budget():
    spec = ToolSpec(
        name="BASH", description="bash", needs_text=True,
        text_instruction="command", mutates_workspace=True)
    provider = Provider([spec])
    kernel = RuntimeKernel(
        SequenceDriver(
            decision("BASH", ledger_content="python --version"),
            decision("DONE"),
        ),
        provider,
        WritePolicy(),
        max_steps=2,
        max_writes=0,
    )

    steps = collect(kernel)

    assert provider.executed == [("BASH", None, "python --version")]
    assert kernel.budget.writes == 0
    assert steps[-1]["final"] == "completed"


def test_duplicate_refusal_forces_recovery_arbitration_at_high_confidence():
    """The exact trace scenario: two identical executed intents, a third
    identical proposal refused pre-dispatch, and Jev still confident (0.99)."""
    spec = ToolSpec(
        name="EXEC", description="execute", needs_text=True, text_instruction="command")
    provider = Provider([spec])
    states = []
    arbitrations = []

    async def chooser(workspace, _goal, _history, provider=None):
        states.append(workspace.state())
        return decision("EXEC", ledger_content="python --version", confidence=0.99)

    async def adjudicator(_transcript, _decision, _provider, _actions, recovery=None):
        arbitrations.append(recovery)
        return {
            "valid": True, "action": "ANSWER", "target": None,
            "call_id": "call-rec-1", "content": "recovered answer",
            "note": "[recovery note] inspect the refusal",
            "latency_ms": 1, "usage": {}, "request": {}, "response": {},
            "message": {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call-rec-1", "type": "function",
                "function": {"name": "ANSWER",
                             "arguments": json.dumps({"answer": "recovered answer"})},
            }]},
        }

    kernel = RuntimeKernel(
        JevDriver(escalate_threshold=0.5, chooser=chooser, adjudicator=adjudicator),
        provider,
        WritePolicy(),
        max_steps=4,
    )
    steps = collect(kernel)

    assert len(provider.executed) == 2  # third identical dispatch prevented
    refusal = steps[2]["outcome"]
    assert refusal["error"]["code"] == "DUPLICATE_NO_PROGRESS"
    assert refusal["effect_disposition"] == "NOT_APPLIED"
    assert refusal["error"]["recoverability"] == "recoverable"
    # visible to the next Jev request and to the LLM transcript
    assert states[3]["last_result"]["error"]["code"] == "DUPLICATE_NO_PROGRESS"
    assert "same arguments and observation" in json.dumps(states[3]["recent_steps"])
    assert any("same arguments and observation" in message["content"]
               for message in kernel.transcript.messages() if message.get("role") == "tool")
    # the immediately following iteration arbitrates despite confidence 0.99
    assert len(arbitrations) == 1
    assert arbitrations[0]["error"]["code"] == "DUPLICATE_NO_PROGRESS"
    assert steps[3]["decision"]["confidence"] == 0.99
    assert steps[3]["decision"]["escalated"] is True
    assert steps[3]["escalation"]["reason"] == "recoverable_observation"
    # the arbitrated recovery action is fully re-checked and completes the run
    assert steps[-1]["final"] == "completed"
    assert kernel.workspace.answer == "recovered answer"


def test_provider_proven_pre_effect_mutation_failure_is_recoverable():
    class PreEffectProvider(Provider):
        async def execute(self, name, ctx):
            self.executed.append((name, ctx.target, ctx.text))
            return {"status": "failed", "reason": "validated before effect",
                    "effect_proof": "pre_effect"}

    spec = ToolSpec(name="WRITE", description="write", needs_text=True,
                    text_instruction="body", write=True)
    provider = PreEffectProvider([spec])
    kernel = RuntimeKernel(
        SequenceDriver(decision("WRITE", ledger_content="x"), decision("DONE")),
        provider,
        WritePolicy(),
        max_steps=2,
    )
    steps = collect(kernel)

    failure = steps[0]["outcome"]
    assert failure["effect_disposition"] == "NOT_APPLIED"
    assert failure["error"]["code"] == "EXECUTION_FAILED"
    assert failure["error"]["recoverability"] == "recoverable"
    assert steps[-1]["final"] == "completed"
    assert kernel.budget.writes == 1  # authorization consumed, never refunded


def test_malformed_authored_value_fails_before_dispatch_then_recovers():
    spec = ToolSpec(name="WRITE", description="write", needs_text=True,
                    text_instruction="body", write=True)
    provider = Provider([spec])
    authored = [
        '<｜｜DSML｜｜ parameter name="target" string="true">main.py',
        "good body",
    ]

    async def texter(_transcript, _instruction):
        return authored.pop(0), {"model": "fake", "latency_ms": 1, "usage": {}}

    kernel = RuntimeKernel(
        SequenceDriver(decision("WRITE"), decision("WRITE"), decision("DONE")),
        provider,
        WritePolicy(),
        max_steps=3,
        texter=texter,
    )
    steps = collect(kernel)

    malformed = steps[0]["outcome"]
    assert malformed["error"]["code"] == "MALFORMED_AUTHORED_VALUE"
    assert malformed["effect_disposition"] == "NOT_APPLIED"
    assert malformed["error"]["recoverability"] == "recoverable"
    assert provider.executed == [("WRITE", None, "good body")]  # never dispatched the fragment
    assert len(kernel.metrics.helper_calls) == 2  # one billed authoring per attempt
    assert steps[-1]["final"] == "completed"


def test_repeated_identical_malformed_recovery_terminates_at_generic_limit():
    spec = ToolSpec(name="WRITE", description="write", needs_text=True,
                    text_instruction="body", write=True)
    provider = Provider([spec])

    async def texter(_transcript, _instruction):
        return '<｜｜DSML｜｜ parameter name="target" string="true">main.py', \
            {"model": "fake", "latency_ms": 1, "usage": {}}

    kernel = RuntimeKernel(
        SequenceDriver(*[decision("WRITE") for _ in range(10)]),
        provider,
        WritePolicy(),
        max_steps=10,
        texter=texter,
    )
    steps = collect(kernel)

    malformed = [step for step in steps
                 if (step.get("outcome", {}).get("error") or {}).get("code")
                 == "MALFORMED_AUTHORED_VALUE"]
    assert len(malformed) == 3  # initial failure + at most two recovery attempts
    assert provider.executed == []
    assert kernel.budget.steps == 3
    assert steps[-2]["termination"] == "NO_PROGRESS_LIMIT"
    assert steps[-1]["final"] == "stopped"


def test_unknown_effect_is_terminal_and_preserved_by_replay():
    class FailingWriteProvider(Provider):
        def __init__(self, specs=None):
            super().__init__(specs)
            self.dispatched = 0

        async def execute(self, _name, _ctx):
            self.dispatched += 1  # the dispatch happened; the effect is uncertain
            raise RuntimeError("transport lost")

    spec = ToolSpec(name="WRITE", description="write", write=True)
    provider = FailingWriteProvider([spec])
    kernel = RuntimeKernel(
        SequenceDriver(decision("WRITE"), decision("DONE")),
        provider,
        WritePolicy(),
    )
    steps = collect(kernel)

    assert steps[-1]["final"] == "stopped"  # DONE never dispatches after UNKNOWN
    assert provider.dispatched == 1         # and the write is never replayed
    rebuilt = rebuild_workspace(kernel.transcript)
    live = kernel.workspace.history[-1]
    replayed = rebuilt.history[-1]
    for entry in (live, replayed):
        assert entry["disposition"] == "UNKNOWN"
        assert entry["error"]["code"] == "EFFECT_UNKNOWN"


def test_rejected_decision_attempts_are_charged_not_free():
    class RejectingDriver:
        name = "test"

        async def decide(self, _context):
            raise DriverRejected(
                "no usable proposal",
                helper_info={"model": "fake", "latency_ms": 1, "usage": {}})

    provider = Provider()
    kernel = RuntimeKernel(RejectingDriver(), provider, WritePolicy(), max_steps=2)
    steps = collect(kernel)

    rejected = [step for step in steps if step.get("denied") == "no usable proposal"]
    assert len(rejected) == 2
    assert kernel.budget.steps == 2           # every billed decision attempt cost a step
    assert len(kernel.metrics.helper_calls) == 2  # and its model call was accounted
    assert provider.executed == []
    assert steps[-2]["termination"] == "STEP_BUDGET_EXHAUSTED"
    assert steps[-1]["final"] == "stopped"


def test_replay_and_live_error_projection_agree():
    spec = ToolSpec(
        name="EXEC", description="execute", needs_text=True, text_instruction="command")
    provider = Provider([spec])
    same = lambda: decision("EXEC", ledger_content="python --version")
    kernel = RuntimeKernel(
        SequenceDriver(same(), same(), same(), decision("DONE")),
        provider,
        WritePolicy(),
        max_steps=4,
    )
    collect(kernel)

    rebuilt = rebuild_workspace(kernel.transcript)
    assert len(rebuilt.history) == len(kernel.workspace.history)
    for live, replayed in zip(kernel.workspace.history, rebuilt.history):
        assert live["operation"] == replayed["operation"]
        assert live["status"] == replayed["status"]
        assert live["disposition"] == replayed["disposition"]
        assert (live.get("error") or {}).get("code") == (replayed.get("error") or {}).get("code")
        assert live["intent_fingerprint"] == replayed["intent_fingerprint"]


def two_turns(kernel, first_goal, second_goal):
    async def run():
        one = [step async for step in kernel.run(first_goal)]
        two = [step async for step in kernel.run(second_goal)]
        return one, two

    return asyncio.run(run())


def test_restored_history_survives_begin_turn_and_feeds_the_next_run():
    ledger = Transcript(system_prompt(Provider()), "goal one")
    call_id = ledger.append_action("PING", {})
    ledger.append_result(call_id, {"status": "ready", "action": "ping"})
    ledger.append_runtime_note("[runtime observation] decision was not applied", meta={
        "runtime": True, "operation": "WRITE", "status": "rejected",
        "disposition": "NOT_APPLIED",
        "error": {"code": "MALFORMED_AUTHORED_VALUE", "kind": "validation",
                  "stage": "authoring", "recoverability": "recoverable",
                  "message": "fragment"},
    })
    workspace = rebuild_workspace(ledger)
    assert len(workspace.history) == 2  # restored evidence exists pre-run

    kernel = RuntimeKernel(
        SequenceDriver(decision("DONE")), Provider(), WritePolicy(),
        transcript=ledger, workspace=workspace)
    steps = collect(kernel)

    assert steps[-1]["final"] == "completed"
    assert [entry["operation"] for entry in kernel.workspace.history] == [
        "PING", "WRITE", "DONE",
    ]
    assert kernel.workspace.current_turn_history() == [
        kernel.workspace.history[-1]]
    recent = kernel.workspace.state()["recent_steps"]
    assert [step.get("operation") for step in recent] == ["PING", "WRITE", "DONE"]


def test_cross_turn_repeat_is_not_blocked():
    spec = ToolSpec(
        name="EXEC", description="execute", needs_text=True, text_instruction="command")
    provider = Provider([spec])
    same = lambda: decision("EXEC", ledger_content="python --version")
    kernel = RuntimeKernel(
        SequenceDriver(same(), same(), decision("DONE"),      # turn one
                       same(), decision("DONE")),              # turn two repeats it
        provider,
        WritePolicy(),
        max_steps=8,
    )

    first, second = two_turns(kernel, "goal one", "goal two")

    assert first[-1]["final"] == "completed"
    assert second[-1]["final"] == "completed"
    assert len(provider.executed) == 3  # the cross-turn repeat dispatched again
    assert [text for _n, _t, text in provider.executed] == [
        "python --version", "python --version", "python --version"]


def test_prior_turn_failure_does_not_force_arbitration_in_a_new_turn():
    spec = ToolSpec(
        name="EXEC", description="execute", needs_text=True, text_instruction="command")
    provider = Provider([spec])
    arbitrations = []
    chooser_calls = []

    async def chooser(_workspace, _goal, _history, provider=None):
        chooser_calls.append(len(chooser_calls) + 1)
        if len(chooser_calls) <= 6:  # turn one: confident repeats until bounded stop
            return decision("EXEC", ledger_content="python --version", confidence=0.99)
        return decision("DONE", confidence=0.99)

    async def adjudicator(_transcript, _decision, _provider, _actions, recovery=None):
        arbitrations.append(recovery)
        return {"valid": False, "note": "[recovery note] no",
                "latency_ms": 1, "usage": {}, "request": {}, "response": {}}

    kernel = RuntimeKernel(
        JevDriver(escalate_threshold=0.5, chooser=chooser, adjudicator=adjudicator),
        provider,
        WritePolicy(),
        max_steps=10,
    )
    first, second = two_turns(kernel, "goal one", "goal two")

    # turn one: recoverable refusals forced arbitration within the turn...
    assert len(arbitrations) == 3
    assert first[-2]["termination"] == "NO_PROGRESS_LIMIT"
    assert first[-1]["final"] == "stopped"
    # ...but the new turn's first confident decision is NOT arbitrated
    assert second[0]["decision"]["operation"] == "DONE"
    assert "escalation" not in second[0]
    assert second[-1]["final"] == "completed"


def test_new_turn_first_decision_sees_prior_turn_operations():
    class CapturingDriver:
        name = "test"

        def __init__(self, decisions):
            self._decisions = list(decisions)
            self.seen = []

        async def decide(self, context):
            self.seen.append(context.workspace.state()["recent_steps"])
            proposal = self._decisions.pop(0)
            return DriverProposal(decision=proposal, base_decision=proposal)

    driver = CapturingDriver([decision(), decision("DONE"), decision("DONE")])
    kernel = RuntimeKernel(driver, Provider(), WritePolicy())
    first, _second = two_turns(kernel, "goal one", "goal two")

    assert first[-1]["final"] == "completed"
    assert driver.seen[0] == []  # fresh workspace: nothing prior
    assert [step.get("operation") for step in driver.seen[2]] == ["PING", "DONE"]


def test_history_trim_shifts_the_boundary_and_keeps_the_current_turn():
    workspace = Workspace()
    for index in range(HISTORY_CAP + 10):
        workspace.append_history({"operation": "PRIOR", "summary": f"p{index}"})
    workspace.begin_turn("new goal")
    assert len(workspace.history) == HISTORY_CAP

    workspace.append_history({"operation": "NEW", "summary": "current"})
    assert [entry["operation"] for entry in workspace.current_turn_history()] == ["NEW"]
    for index in range(5):  # over-cap appends never eat the active turn
        workspace.append_history({"operation": "MORE", "summary": f"m{index}"})
    assert [entry["operation"] for entry in workspace.current_turn_history()] == \
        ["NEW"] + ["MORE"] * 5


def test_high_confidence_first_decision_without_recovery_never_arbitrates():
    arbitrations = []

    async def chooser(_workspace, _goal, _history, provider=None):
        return decision("DONE", confidence=0.99)

    async def adjudicator(_transcript, _decision, _provider, _actions, recovery=None):
        arbitrations.append(recovery)
        raise AssertionError("high confidence with no recoverable observation "
                             "must not arbitrate")

    kernel = RuntimeKernel(
        JevDriver(escalate_threshold=0.5, chooser=chooser, adjudicator=adjudicator),
        Provider(),
        WritePolicy(),
    )
    steps = collect(kernel)

    assert arbitrations == []
    assert steps[-1]["final"] == "completed"


def test_crash_after_dispatch_started_leaves_a_persisted_answerable_call():
    checkpoints = []

    def checkpoint(transcript):
        checkpoints.append([dict(message) for message in transcript.dump()])

    class CrashSink:
        def __call__(self, event):
            if event["type"] in {"dispatch_started", "observation"}:
                raise OSError("crash mid-dispatch")

    kernel = RuntimeKernel(
        SequenceDriver(decision("PING")), Provider(), WritePolicy(),
        event_sink=CrashSink(), checkpoint=checkpoint)
    with pytest.raises(OSError):
        collect(kernel)

    # the last durable checkpoint contains the committed (synthesized) call
    persisted = checkpoints[-1]
    assert any(message.get("role") == "assistant" and message.get("tool_calls")
               for message in persisted)

    # restoring that ledger fail-closes on the dangling dispatch as UNKNOWN
    restored = Transcript.from_messages(persisted)
    provider = Provider()
    kernel2 = RuntimeKernel(
        SequenceDriver(decision("DONE")), provider, WritePolicy(),
        transcript=restored, workspace=rebuild_workspace(restored))
    steps = collect(kernel2)
    assert steps[0]["termination"] == "EFFECT_UNKNOWN"
    assert steps[0]["outcome"]["error"]["recoverability"] == "unsafe"
    assert steps[-1]["final"] == "stopped"
    assert provider.executed == []  # no decision ran after the failed restore
    assert kernel2.workspace.history[-1]["disposition"] == "UNKNOWN"


def test_multi_call_turn_roundtrips_live_equals_replay():
    async def chooser(_workspace, _goal, _history, provider=None):
        if not getattr(chooser, "calls", None):
            chooser.calls = 1
            return {
                "operation": "PING", "target": None, "confidence": 0.2,
                "operation_probabilities": {"PING": 0.6, "DONE": 0.4},
                "target_probabilities": {}, "latency_ms": 1, "usage": {}, "request": {},
            }
        return {
            "operation": "DONE", "target": None, "confidence": 0.99,
            "operation_probabilities": {"DONE": 1.0},
            "target_probabilities": {}, "latency_ms": 1, "usage": {}, "request": {},
        }

    raw_message = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call-0", "type": "function",
         "function": {"name": "PING", "arguments": "{}"}},
        {"id": "call-1", "type": "function",
         "function": {"name": "PING", "arguments": "{}"}},
    ]}

    async def adjudicator(_transcript, _decision, _provider, _actions, recovery=None):
        return {
            "valid": True, "action": "PING", "target": None,
            "call_id": "call-0", "content": None, "note": "[fast-decision note]",
            "latency_ms": 1, "usage": {}, "request": {}, "response": {},
            "message": raw_message,
        }

    kernel = RuntimeKernel(
        JevDriver(escalate_threshold=0.5, chooser=chooser, adjudicator=adjudicator),
        Provider(), WritePolicy(), max_steps=2)
    steps = collect(kernel)
    assert steps[-1]["final"] == "completed"

    rebuilt = rebuild_workspace(kernel.transcript)
    # the superseded sibling is protocol bookkeeping, not an attempt
    assert len(rebuilt.history) == len(kernel.workspace.history) == 2
    live, replayed = kernel.workspace.history[0], rebuilt.history[0]
    for key in ("operation", "status", "disposition", "dispatched",
                "intent_fingerprint", "observation_fingerprint",
                "observation_id", "attempt_id"):
        assert live.get(key) == replayed.get(key), key


def test_invalid_jev_answer_object_is_billed_and_recoverable(monkeypatch):
    async def bad_post(_url, _key, _body):
        return {"answers": "not-an-object",
                "usage": {"input_tokens": 7, "output_tokens": 2}}

    monkeypatch.setattr(model, "post_json", bad_post)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")

    async def chooser(workspace, goal, history, provider=None):
        return await model.choose(workspace, goal, history, provider=provider)

    kernel = RuntimeKernel(
        JevDriver(escalate_threshold=0.5, chooser=chooser),
        Provider(),
        WritePolicy(),
        max_steps=1,
    )
    steps = collect(kernel)

    assert "Jev answers must be an object" in steps[0]["denied"]
    assert steps[0]["outcome"]["error"]["code"] == "INVALID_PROPOSAL"
    assert steps[0]["outcome"]["error"]["recoverability"] == "recoverable"
    # the billed call keeps its request/raw response and is counted exactly once
    assert [call["kind"] for call in steps[0]["model_calls"]] == ["jev_decision"]
    assert steps[0]["model_calls"][0]["response"]["answers"] == "not-an-object"
    assert len(kernel.metrics.jev_calls) == 1
    assert kernel.budget.steps == 1


def test_resolved_sandbox_unknown_continues_and_does_not_freeze_next_turn():
    class ResolvedBashProvider(Provider):
        def __init__(self):
            super().__init__([
                ToolSpec(name="BASH", description="bash", mutates_workspace=True),
            ])

        async def execute(self, _name, _ctx):
            return {
                "status": "failed",
                "reason": "command exited with status 143",
                "effect_disposition": "UNKNOWN",
                "resolved": True,
                "resolution": "sandbox_command_ended",
                "continuation": "allowed",
                "container_running": True,
            }

    kernel = RuntimeKernel(
        SequenceDriver(decision("BASH"), decision("DONE")),
        ResolvedBashProvider(),
        WritePolicy(),
        max_steps=2,
    )
    steps = collect(kernel)

    assert steps[0]["outcome"]["effect_disposition"] == "UNKNOWN"
    assert steps[0]["outcome"]["resolved"] is True
    assert steps[-1]["final"] == "completed"
    assert kernel.transcript.unresolved_unknown_calls() == []

    restored = RuntimeKernel(
        SequenceDriver(decision("DONE")),
        Provider(),
        WritePolicy(),
        transcript=kernel.transcript,
        workspace=rebuild_workspace(kernel.transcript),
    )
    reopened = collect(restored)
    assert reopened[0]["decision"]["operation"] == "DONE"
    assert reopened[-1]["final"] == "completed"


def test_restore_only_turn_never_republishes_prior_answer():
    transcript = Transcript(system_prompt(Provider()), "old goal")
    answer_call = transcript.append_action("ANSWER", {"answer": "old answer"})
    transcript.append_result(answer_call, {
        "status": "done",
        "answer": "old answer",
        "delivered_answer": "old answer",
        "effect_disposition": "SUCCEEDED",
    })
    unknown_call = transcript.append_action("BASH", {"command": "restart"})
    transcript.append_result(unknown_call, {
        "status": "failed",
        "effect_disposition": "UNKNOWN",
    })
    workspace = rebuild_workspace(transcript)
    assert workspace.answer == "old answer"

    kernel = RuntimeKernel(
        SequenceDriver(decision("DONE")),
        Provider(),
        WritePolicy(),
        transcript=transcript,
        workspace=workspace,
    )
    steps = collect(kernel)

    assert steps[0]["decision"]["operation"] == "RESTORE"
    assert kernel.workspace.answer == ""
    assert kernel.workspace.prior_answer == "old answer"


def test_unresolved_unknown_blocks_repeated_reopens_until_explicitly_resolved():
    # a session whose WRITE stopped on an UNKNOWN effect (recorded durably)
    class FailingWriteProvider(Provider):
        def __init__(self, specs=None):
            super().__init__(specs)
            self.dispatched = 0

        async def execute(self, _name, _ctx):
            self.dispatched += 1
            raise RuntimeError("transport lost")

    spec = ToolSpec(name="WRITE", description="write", write=True)
    provider = FailingWriteProvider([spec])
    kernel = RuntimeKernel(
        SequenceDriver(decision("WRITE"), decision("DONE")), provider,
        WritePolicy(), event_sink=lambda event: None)
    first = collect(kernel)
    assert first[-1]["final"] == "stopped"  # UNKNOWN stopped the run

    # every reopen fails closed: the persisted UNKNOWN is scanned independent
    # of repair activity, so this is not a one-shot gate
    for _ in range(2):
        kernel = RuntimeKernel(
            SequenceDriver(decision("DONE")), Provider(), WritePolicy(),
            transcript=kernel.transcript, workspace=rebuild_workspace(kernel.transcript))
        steps = collect(kernel)
        assert steps[0]["termination"] == "EFFECT_UNKNOWN"
        assert steps[-1]["final"] == "stopped"

    # the explicit resolution contract unblocks the next user turn
    kernel = RuntimeKernel(
        SequenceDriver(decision("DONE")), Provider(), WritePolicy(),
        transcript=kernel.transcript, workspace=rebuild_workspace(kernel.transcript))
    asyncio.run(kernel.accept_unknown_effects())
    steps = collect(kernel)
    assert steps[-1]["final"] == "completed"


def test_truncated_v1_mutating_result_gates_reopens_with_zero_calls():
    # a v1 ledger whose serialized result was byte-sliced mid-JSON: the
    # mutating call may have executed, but its effect is unreadable
    ledger = Transcript(system_prompt(Provider()), "old goal")
    call_id = ledger.append_action("EXEC", {"content": "make build"})
    truncated = json.dumps({"status": "ready", "action": "exec", "exit": 0,
                            "effect_disposition": "SUCCEEDED"})[:34]
    restored = Transcript.from_messages(
        ledger.dump() + [{"role": "tool", "tool_call_id": call_id,
                          "content": truncated}])

    for _ in range(2):  # both reopens stop before any model or provider call
        provider = Provider()
        driver = SequenceDriver(decision("DONE"))
        kernel = RuntimeKernel(driver, provider, WritePolicy(),
                               transcript=restored,
                               workspace=rebuild_workspace(restored))
        steps = collect(kernel)
        assert steps[0]["termination"] == "EFFECT_UNKNOWN"
        assert steps[0]["outcome"]["error"]["recoverability"] == "unsafe"
        assert steps[-1]["final"] == "stopped"
        assert provider.executed == []      # zero provider calls
        assert driver.decisions             # zero decision/model calls
        assert restored.dump()[-1]["content"] == truncated  # audit prefix intact

    kernel = RuntimeKernel(
        SequenceDriver(decision("DONE")), Provider(), WritePolicy(),
        transcript=restored, workspace=rebuild_workspace(restored))
    asyncio.run(kernel.accept_unknown_effects())
    steps = collect(kernel)
    assert steps[-1]["final"] == "completed"


def test_validate_choice_failure_keeps_billed_call_accounting(monkeypatch):
    async def invalid_choice_post(_url, _key, _body):
        return {"answers": {"phase": {"choice": "NOPE", "confidence": 0.9,
                                      "probabilities": {}}},
                "usage": {"input_tokens": 11, "output_tokens": 4}}

    monkeypatch.setattr(model, "post_json", invalid_choice_post)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")

    async def chooser(workspace, goal, history, provider=None):
        return await model.choose(workspace, goal, history, provider=provider)

    kernel = RuntimeKernel(
        JevDriver(escalate_threshold=0.5, chooser=chooser),
        Provider(),
        WritePolicy(),
        max_steps=1,
    )
    steps = collect(kernel)

    assert steps[0]["outcome"]["error"]["code"] == "INVALID_PROPOSAL"
    assert [call["kind"] for call in steps[0]["model_calls"]] == ["jev_decision"]
    assert steps[0]["model_calls"][0]["usage"]["input_tokens"] == 11
    assert len(kernel.metrics.jev_calls) == 1
    assert kernel.budget.steps == 1
