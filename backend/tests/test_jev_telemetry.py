"""Optional Jev presentation telemetry never changes decision semantics."""

import asyncio
from copy import deepcopy

import pytest

from jevloop.context.state import Workspace
from jevloop.context.transcript import Transcript
from jevloop.contracts.policy import EventSinkError, WritePolicy
from jevloop.contracts.tools import ToolSpec
from jevloop.decision import model
from jevloop.decision.drivers import DriverContext, JevDriver
from jevloop.runtime.kernel import RuntimeKernel


class Provider:
    def __init__(self):
        self.executed = []

    def specs(self):
        return [ToolSpec(name="PING", description="Inspect a fixture", phases=("INSPECT",))]

    def available(self, _workspace):
        return {"PING"}

    async def execute(self, name, _context):
        self.executed.append(name)
        return {"status": "ready", "action": "ping"}


def decision(**overrides):
    return {
        "operation": "PING", "phase": "INSPECT", "target": None,
        "binding_mode": "defaults", "bound_arguments": {},
        "confidence": 0.99, "operation_probabilities": {"PING": 1.0},
        "target_probabilities": {}, "latency_ms": 1, "usage": {},
        "request": {"model": "test-jev", "state": {"private": "not telemetry"}},
        **overrides,
    }


def answers_for(body):
    answers = {}
    for head, question in body["questions"].items():
        if question.get("type") != "choice":
            continue
        offered = question["criteria"]
        preferred = ("INSPECT" if head == "phase" else "PING"
                     if "PING" in offered else "DEFAULT_ARGUMENTS")
        chosen = preferred if preferred in offered else next(iter(offered))
        answers[head] = {
            "choice": chosen, "confidence": 0.99,
            "probabilities": {key: float(key == chosen) for key in offered},
        }
    return {"answers": answers}


def collect(kernel):
    async def run():
        return [step async for step in kernel.run("private task goal")]
    return asyncio.run(run())


@pytest.mark.parametrize("failed_event", ["jev_request", "jev_response", "llm_started", "llm_completed"])
def test_durable_telemetry_failure_stops_before_further_effects(monkeypatch, failed_event):
    events, calls = [], []

    async def post(_endpoint, _key, body):
        calls.append("jev")
        return answers_for(body)

    async def chooser(_workspace, _goal, _history, provider=None):
        return decision(binding_mode="llm_parameters")

    async def arguer(_transcript, _provider, _operation):
        calls.append("llm")
        return {}, {"kind": "parameter_authoring", "usage": {}}

    def sink(event):
        events.append(event["type"])
        if event["type"] == failed_event:
            raise OSError("journal fsync failed")

    monkeypatch.setattr(model, "post_json", post)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only")
    provider = Provider()
    driver = JevDriver(escalate_threshold=None, **(
        {"chooser": chooser} if failed_event.startswith("llm_") else {}))
    kernel = RuntimeKernel(driver, provider, WritePolicy(), max_steps=1,
                           event_sink=sink, arguer=arguer)
    with pytest.raises(EventSinkError, match="journal fsync failed"):
        collect(kernel)
    assert events[-1] == failed_event  # No fabricated observation or second append.
    assert provider.executed == []
    assert calls == {
        "jev_request": [], "jev_response": ["jev"],
        "llm_started": [], "llm_completed": ["llm"],
    }[failed_event]


def test_live_questions_precede_http_and_response_precedes_execution(monkeypatch):
    events = []
    sent = []

    async def post(_endpoint, _key, body):
        assert [event["type"] for event in events] == ["attempt_started", "jev_request"]
        sent.append(deepcopy(body))
        return answers_for(body)

    monkeypatch.setattr(model, "post_json", post)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only")
    provider = Provider()
    kernel = RuntimeKernel(
        JevDriver(escalate_threshold=None), provider, WritePolicy(),
        max_steps=1, event_sink=events.append)
    steps = collect(kernel)
    assert [event["type"] for event in events[:7]] == [
        "attempt_started", "jev_request", "jev_response", "decision_ready",
        "intent", "dispatch_started", "observation",
    ]
    attempt_id = events[0]["attempt_id"]
    assert attempt_id
    assert all(event["attempt_id"] == attempt_id for event in events[1:4])
    assert events[6]["attempt_id"] == attempt_id
    assert set(events[1]) == {"type", "attempt_id", "questions"}
    assert events[1]["questions"] == sent[0]["questions"]
    assert set(events[2]) == {"type", "attempt_id", "response"}
    response = events[2]["response"]
    assert response == steps[0]["model_calls"][0]["response"]
    assert not ({"request", "state", "compiled", "transcript"} & response.keys())
    assert events[3]["binding_mode"] == "defaults"
    assert events[3]["escalated"] is False
    assert provider.executed == ["PING"]
    # Telemetry is not appended to the canonical model-facing ledger.
    assert "jev_request" not in str(kernel.transcript.dump())
    assert "jev_response" not in str(kernel.transcript.dump())


@pytest.mark.parametrize("asynchronous", [False, True])
def test_request_observer_is_detached_and_failure_is_nonfatal(monkeypatch, asynchronous):
    original_questions = None

    def mutate_and_fail(questions):
        nonlocal original_questions
        original_questions = deepcopy(questions)
        questions.clear()
        raise RuntimeError("presentation observer unavailable")

    async def async_observer(questions):
        mutate_and_fail(questions)

    async def post(_endpoint, _key, body):
        assert body["questions"] == original_questions
        assert body["questions"]
        return answers_for(body)

    monkeypatch.setattr(model, "post_json", post)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only")
    result = asyncio.run(model.choose(
        Workspace(), "goal", [], provider=Provider(),
        on_request=async_observer if asynchronous else mutate_and_fail))
    assert result["operation"] == "PING"
    assert result["binding_mode"] == "defaults"


def test_rejected_request_budget_does_not_emit_or_send(monkeypatch):
    observed = []

    async def unexpected_post(*_args):
        pytest.fail("over-budget request must not be sent")

    monkeypatch.setattr(model, "MAX_REQUEST_BYTES", 1)
    monkeypatch.setattr(model, "post_json", unexpected_post)
    with pytest.raises(model.InvalidModelResponse, match="invocation budget"):
        asyncio.run(model.choose(Workspace(), "goal", [], provider=Provider(),
                                 on_request=observed.append))
    assert observed == []


def test_response_observer_cannot_change_proposal_or_legacy_chooser_signature():
    calls = []
    observed = []

    # Deliberately no **kwargs: existing injected chooser API stays compatible.
    async def chooser(workspace, goal, history, provider=None):
        calls.append((workspace, goal, history, provider))
        return decision()

    async def mutate_and_fail(event):
        observed.append(deepcopy(event))
        event["response"]["operation"] = "BROKEN"
        event["response"]["bound_arguments"]["injected"] = True
        raise RuntimeError("presentation observer unavailable")

    provider = Provider()
    ledger = Transcript("system", "goal")
    original = deepcopy(ledger.dump())
    workspace = Workspace()
    driver = JevDriver(chooser=chooser, escalate_threshold=None)
    without = asyncio.run(driver.decide(DriverContext("goal", workspace, ledger, provider)))
    with_observer = asyncio.run(driver.decide(DriverContext(
        "goal", workspace, ledger, provider,
        attempt_id="attempt-a", telemetry=mutate_and_fail)))
    assert with_observer == without
    assert len(calls) == 2
    assert ledger.dump() == original
    assert [event["type"] for event in observed] == ["jev_response"]
    assert observed[0]["attempt_id"] == "attempt-a"
    assert observed[0]["response"]["operation"] == "PING"
    assert "request" not in observed[0]["response"]


def test_response_precedes_arbitration_and_ready_reports_final_route():
    events = []

    async def chooser(_workspace, _goal, _history, provider=None):
        return decision(confidence=0.3)

    async def adjudicator(_transcript, _decision, _provider, _actions):
        assert [event["type"] for event in events] == [
            "attempt_started", "jev_response", "llm_started"]
        assert events[1]["response"]["binding_mode"] == "defaults"
        return {
            "valid": True, "action": "PING", "target": None, "arguments": {},
            "call_id": "call-review", "content": None, "note": None,
            "latency_ms": 1, "usage": {}, "request": {}, "response": {}, "message": None,
        }

    provider = Provider()
    kernel = RuntimeKernel(
        JevDriver(chooser=chooser, adjudicator=adjudicator), provider,
        WritePolicy(), max_steps=1, event_sink=events.append)
    collect(kernel)
    assert [event["type"] for event in events[:5]] == [
        "attempt_started", "jev_response", "llm_started", "llm_completed", "decision_ready"]
    assert events[2] == {
        "type": "llm_started", "attempt_id": events[0]["attempt_id"],
        "kind": "arbitration", "operation": "PING", "reason": "low_confidence",
    }
    assert events[3] == {**events[2], "type": "llm_completed", "status": "returned"}
    ready = next(event for event in events if event["type"] == "decision_ready")
    assert ready["binding_mode"] == "arbitrated"
    assert ready["escalated"] is True
    assert ready["attempt_id"] == events[1]["attempt_id"] == events[0]["attempt_id"]
    assert events[1]["response"]["binding_mode"] == "defaults"
    assert provider.executed == ["PING"]


def test_response_keeps_original_selection_before_weak_binding_fallback():
    events = []

    async def chooser(_workspace, _goal, _history, provider=None):
        return decision(confidence=0.3, operation_path_confidence=0.99)

    proposal = asyncio.run(JevDriver(chooser=chooser).decide(DriverContext(
        "goal", Workspace(), Transcript("system", "goal"), Provider(),
        attempt_id="attempt-weak", telemetry=events.append)))
    assert events[0]["response"]["binding_mode"] == "defaults"
    assert events[0]["response"]["confidence"] == 0.3
    assert proposal.decision["binding_mode"] == "llm_parameters"
    assert proposal.decision["confidence"] == 0.99
    assert proposal.escalation is None


def test_observer_does_not_swallow_cancellation():
    async def cancelled(_payload):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(model.observe(cancelled, {"type": "jev_request"}))


@pytest.mark.parametrize("operation,kind", [("PING", "parameter_authoring"), ("ANSWER", "authoring")])
def test_parameter_events_surround_real_helper_before_commit(monkeypatch, operation, kind):
    from jevloop.decision import argument_helper

    events = []

    async def chooser(_workspace, _goal, _history, provider=None):
        return decision(operation=operation, binding_mode="llm_parameters")

    async def post(_endpoint, _key, _body):
        assert [event["type"] for event in events] == [
            "attempt_started", "jev_response", "decision_ready", "llm_started"]
        return {
            "choices": [{"finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "parameters-1", "type": "function", "function": {
                    "name": operation, "arguments": '{}' if operation == "PING" else '{"answer":"Complete"}',
                }}],
            }}], "usage": {},
        }

    monkeypatch.setattr(argument_helper, "post_json", post)
    provider = Provider()
    kernel = RuntimeKernel(
        JevDriver(chooser=chooser, escalate_threshold=None), provider, WritePolicy(),
        max_steps=1, event_sink=events.append)
    steps = collect(kernel)
    llm_events = [event for event in events if event["type"].startswith("llm_")]
    assert llm_events == [
        {"type": "llm_started", "attempt_id": events[0]["attempt_id"], "kind": kind, "operation": operation},
        {"type": "llm_completed", "attempt_id": events[0]["attempt_id"], "kind": kind,
         "operation": operation, "status": "returned"},
    ]
    assert events.index(llm_events[-1]) < next(i for i, event in enumerate(events) if event["type"] == "intent")
    assert not steps[0].get("denied")
    assert "llm_started" not in str(kernel.transcript.dump())
    assert "llm_completed" not in str(kernel.transcript.dump())
    assert provider.executed == (["PING"] if operation == "PING" else [])


def test_legacy_authoring_events_surround_real_helper(monkeypatch):
    from jevloop.decision import text_helper

    events = []

    async def chooser(_workspace, _goal, _history, provider=None):
        legacy = decision(operation="ANSWER")
        legacy.pop("binding_mode")
        legacy.pop("bound_arguments")
        return legacy

    async def post(_endpoint, _key, body):
        assert events[-1]["type"] == "llm_started"
        assert events[-1]["kind"] == "authoring"
        assert "[content request]" in body["messages"][-1]["content"]
        return {"choices": [{"finish_reason": "stop", "message": {
            "role": "assistant", "content": "Complete",
        }}], "usage": {}}

    monkeypatch.setattr(text_helper, "post_json", post)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only")
    kernel = RuntimeKernel(
        JevDriver(chooser=chooser, escalate_threshold=None), Provider(), WritePolicy(),
        max_steps=1, event_sink=events.append)
    steps = collect(kernel)
    llm_events = [event for event in events if event["type"].startswith("llm_")]
    assert [event["type"] for event in llm_events] == ["llm_started", "llm_completed"]
    assert llm_events[-1]["status"] == "returned"
    assert all(event["attempt_id"] == events[0]["attempt_id"] for event in llm_events)
    assert steps[0]["outcome"]["answer"] == "Complete"


@pytest.mark.parametrize("kind", ["arbitration", "parameter_authoring", "authoring"])
def test_llm_wrapper_preserves_failure_and_does_not_emit_contents(kind):
    events = []
    failure = ValueError("private model payload must not be emitted")

    async def helper(legacy_argument):
        assert legacy_argument == "input"
        assert events[-1]["type"] == "llm_started"
        raise failure

    with pytest.raises(ValueError) as caught:
        asyncio.run(model.observe_llm_call(
            events.append, {"attempt_id": "failed-1", "kind": kind, "operation": "PING"}, helper, "input"))
    assert caught.value is failure
    assert events[-1] == {
        "type": "llm_completed", "attempt_id": "failed-1", "kind": kind,
        "operation": "PING", "status": "failed",
    }
    assert "private model payload" not in str(events)


def test_llm_observer_failures_do_not_change_helper_return_or_signature():
    calls = []
    returned = object()

    async def legacy_helper(first, second):
        calls.append((first, second))
        return returned

    def broken_observer(event):
        event.clear()
        raise OSError("display disconnected")

    result = asyncio.run(model.observe_llm_call(
        broken_observer, {"attempt_id": "observer-1", "kind": "authoring", "operation": "ANSWER"},
        legacy_helper, "one", "two"))
    assert result is returned
    assert calls == [("one", "two")]


def test_llm_cancellation_propagates_without_a_returned_event():
    events = []

    async def cancelled():
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(model.observe_llm_call(
            events.append, {"attempt_id": "cancelled-1", "kind": "authoring", "operation": "ANSWER"},
            cancelled))
    assert [event["type"] for event in events] == ["llm_started"]


def test_no_llm_events_when_no_helper_is_invoked():
    events = []

    async def chooser(_workspace, _goal, _history, provider=None):
        return decision()

    kernel = RuntimeKernel(
        JevDriver(chooser=chooser, escalate_threshold=None), Provider(), WritePolicy(),
        max_steps=1, event_sink=events.append)
    collect(kernel)
    assert not any(event["type"].startswith("llm_") for event in events)


def test_llm_returned_does_not_claim_kernel_validation_or_commit():
    events = []

    async def chooser(_workspace, _goal, _history, provider=None):
        return decision(binding_mode="llm_parameters")

    # Legacy injected helper signature stays unchanged. The kernel still owns
    # canonical validation even when an injected helper returns bad arguments.
    async def arguer(_transcript, _provider, operation):
        assert operation == "PING"
        return "not-an-argument-object", {"kind": "parameter_authoring", "usage": {}}

    provider = Provider()
    kernel = RuntimeKernel(
        JevDriver(chooser=chooser, escalate_threshold=None), provider, WritePolicy(),
        arguer=arguer, max_steps=1, event_sink=events.append)
    steps = collect(kernel)
    completed = next(event for event in events if event["type"] == "llm_completed")
    assert completed["status"] == "returned"
    assert steps[0]["denied"]
    assert not any(event["type"] == "intent" for event in events)
    assert provider.executed == []


def test_predispatch_rejection_never_fabricates_an_llm_call():
    events = []

    async def chooser(_workspace, _goal, _history, provider=None):
        return decision(binding_mode="llm_parameters", bound_arguments={"forbidden": "partial"})

    async def arguer(_transcript, _provider, _operation):
        pytest.fail("invalid partial binding must be rejected before authoring")

    kernel = RuntimeKernel(
        JevDriver(chooser=chooser, escalate_threshold=None), Provider(), WritePolicy(),
        arguer=arguer, max_steps=1, event_sink=events.append)
    steps = collect(kernel)
    ready = next(event for event in events if event["type"] == "decision_ready")
    assert ready["needs_authoring"] is True
    assert steps[0]["denied"]
    assert not any(event["type"].startswith("llm_") for event in events)


def test_live_subscriber_observes_each_handoff_while_models_are_still_blocked(monkeypatch):
    """Assert observable intermediate states, not merely final event ordering.

    Exercise the real RunState fan-out used by SSE, replacing only disk
    persistence and model helpers. No server, network, or run artifacts.
    """
    from jevloop.apps.server import RunState
    from jevloop.storage import runstore

    persisted = []
    monkeypatch.setattr(runstore, "append", lambda run_id, seq, event:
                        persisted.append((run_id, seq, deepcopy(event))))
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-only")

    async def scenario():
        http_entered, release_http = asyncio.Event(), asyncio.Event()
        arbitration_entered, release_arbitration = asyncio.Event(), asyncio.Event()
        state = RunState("gated-telemetry", {})
        snapshot, subscriber = state.subscribe()
        assert snapshot == []
        yielded_steps = []
        sent_questions = []

        async def post(_endpoint, _key, body):
            sent_questions.append(deepcopy(body["questions"]))
            http_entered.set()
            await release_http.wait()
            result = answers_for(body)
            # Force review without altering the valid argmax distributions.
            result["answers"]["phase"]["confidence"] = 0.3
            return result

        async def adjudicator(_transcript, _decision, _provider, _actions):
            arbitration_entered.set()
            await release_arbitration.wait()
            return {
                "valid": True, "action": "PING", "target": None, "arguments": {},
                "call_id": "gated-review", "content": None, "note": None,
                "latency_ms": 1, "usage": {}, "request": {}, "response": {}, "message": None,
            }

        monkeypatch.setattr(model, "post_json", post)
        provider = Provider()
        kernel = RuntimeKernel(
            JevDriver(adjudicator=adjudicator), provider, WritePolicy(), max_steps=1,
            event_sink=lambda event: state.emit({**event, "lane": "jev"}))

        async def consume():
            async for step in kernel.run("offline gated fixture"):
                yielded_steps.append(step)
                # Same publication boundary as Dashboard._run_lane.
                state.emit({"type": "step", "lane": "jev", "step": step})

        task = asyncio.create_task(consume())
        try:
            await asyncio.wait_for(http_entered.wait(), timeout=1)
            # The actual Jev post is still suspended: no response or step exists.
            assert not release_http.is_set()
            assert not task.done()
            assert yielded_steps == []
            assert provider.executed == []
            first_entries = [subscriber.get_nowait(), subscriber.get_nowait()]
            assert [seq for seq, _ in first_entries] == [1, 2]
            assert [event["type"] for _, event in first_entries] == ["attempt_started", "jev_request"]
            attempt_id = first_entries[0][1]["attempt_id"]
            assert first_entries[1][1]["attempt_id"] == attempt_id
            assert first_entries[1][1]["questions"] == sent_questions[0]
            assert subscriber.empty()

            release_http.set()
            await asyncio.wait_for(arbitration_entered.wait(), timeout=1)
            # Jev returned, but the LLM is still suspended. The live subscriber
            # already has both the original selection and the handoff event.
            assert not release_arbitration.is_set()
            assert not task.done()
            assert yielded_steps == []
            assert provider.executed == []
            handoff_entries = [subscriber.get_nowait(), subscriber.get_nowait()]
            assert [seq for seq, _ in handoff_entries] == [3, 4]
            assert [event["type"] for _, event in handoff_entries] == ["jev_response", "llm_started"]
            assert all(event["attempt_id"] == attempt_id for _, event in handoff_entries)
            assert handoff_entries[0][1]["response"]["operation"] == "PING"
            assert handoff_entries[1][1]["kind"] == "arbitration"
            assert handoff_entries[1][1]["reason"] == "low_confidence"
            assert subscriber.empty()
            assert not any(event["type"] in {"decision_ready", "intent", "step", "llm_completed"}
                           for _, event in state.log)
            assert [event["type"] for _, _, event in persisted] == [
                "attempt_started", "jev_request", "jev_response", "llm_started"]

            release_arbitration.set()
            await asyncio.wait_for(task, timeout=1)
            remaining = []
            while not subscriber.empty():
                remaining.append(subscriber.get_nowait())
            types = [event["type"] for _, event in remaining]
            assert types[:5] == [
                "llm_completed", "decision_ready", "intent", "dispatch_started", "observation"]
            assert types.index("intent") < types.index("step")
            assert remaining[0][1]["status"] == "returned"
            assert remaining[0][1]["attempt_id"] == attempt_id
            assert provider.executed == ["PING"]
            assert yielded_steps
            delivered = first_entries + handoff_entries + remaining
            assert delivered == state.log
            assert [seq for seq, _ in delivered] == list(range(1, len(delivered) + 1))
        finally:
            state.unsubscribe(subscriber)
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
