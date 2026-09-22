"""Path A escalation on the conversation ledger: threshold gating, adjudication
parsing/validation, pair recording, and transcript prefix stability."""

import asyncio
import json

from jevloop.escalation import arbitrate, should_escalate
from jevloop.metrics import RunMetrics
from jevloop.tools.sandbox import SandboxTools
from jevloop.transcript import Transcript, system_prompt


def decision(confidence=0.4):
    return {
        "operation": "OPEN_CHAT", "confidence": confidence,
        "operation_probabilities": {"OPEN_CHAT": 0.4, "DONE": 0.35, "LIST_CHATS": 0.25},
        "target": None,
    }


def make_transcript():
    return Transcript(system_prompt(SandboxTools()), "do something")


def _fake_post(message):
    async def post(url, key, body):
        return {"choices": [{"message": message}], "usage": {"prompt_tokens": 10,
                                                             "prompt_cache_hit_tokens": 8}}

    return post


def _tool_call_message(name, target=None, content=None, path=None):
    args = {}
    if path is not None:
        args["path"] = path
    if target:
        args["target"] = target
    if content is not None:
        args["content"] = content
    return {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call_1", "type": "function",
         "function": {"name": name, "arguments": json.dumps(args)}}]}


def test_should_escalate_gates_on_threshold():
    assert should_escalate(decision(0.4), 0.5)
    assert not should_escalate(decision(0.8), 0.5)
    assert not should_escalate(decision(0.4), None)  # escalation disabled
    assert not should_escalate(decision(None), 0.5)


def test_should_escalate_requires_ambiguity_when_gated():
    low = decision(0.4)
    assert should_escalate({**low, "ambiguity": 0.9}, 0.5, ambiguity_gate=0.5)
    assert not should_escalate({**low, "ambiguity": 0.2}, 0.5, ambiguity_gate=0.5)
    # without the signal in the decision the gate falls back to confidence-only
    assert should_escalate(low, 0.5, ambiguity_gate=0.5)
    # confident decisions never escalate regardless of the meta signal
    assert not should_escalate({**decision(0.9), "ambiguity": 0.9}, 0.5,
                               ambiguity_gate=0.5)
    # the relaxation covers read/verify phases only: mutations and terminal
    # delivery keep the full confidence net even when ambiguity looks low
    assert should_escalate({**low, "ambiguity": 0.2, "phase": "ACT"}, 0.5,
                           ambiguity_gate=0.5)
    assert should_escalate({**low, "ambiguity": 0.2, "phase": "RESPOND"}, 0.5,
                           ambiguity_gate=0.5)
    assert not should_escalate({**low, "ambiguity": 0.2, "phase": "INSPECT"}, 0.5,
                               ambiguity_gate=0.5)
    assert not should_escalate({**low, "ambiguity": 0.2, "phase": "VERIFY"}, 0.5,
                               ambiguity_gate=0.5)


def test_note_text_carries_meta_signals():
    from jevloop.escalation import _note_text

    note = _note_text({**decision(0.4), "ambiguity": 0.81,
                       "progress": {"score": 1.2, "confidence": 0.4}})
    assert "ambiguity 0.81" in note and "progress 1.2/3" in note
    assert "Meta signals" not in _note_text(decision(0.4))


def test_arbitrate_accepts_tool_call_pick_with_content():
    t = make_transcript()
    verdict = asyncio.run(arbitrate(
        t, decision(), SandboxTools(), {"WRITE_FILE", "DONE", "ANSWER"},
        post=_fake_post(_tool_call_message("WRITE_FILE", path="notes.md", content="# body"))))
    assert verdict["valid"] is True
    assert verdict["action"] == "WRITE_FILE"
    assert verdict["content"] == "# body"
    assert verdict["usage"]["prompt_cache_hit_tokens"] == 8
    assert verdict["message"]["tool_calls"][0]["id"] == "call_1"
    assert "fast-decision note" in verdict["note"]
    assert [m["role"] for m in t.messages()] == ["system", "user"]


def test_arbitration_schema_preserves_open_paths_beyond_jev_candidates():
    captured = {}
    routed = {
        **decision(),
        "operation": "READ_FILE",
        "request": {
            "state": {
                "decision_surface": {
                    "targets": {
                        "INSPECT/READ_FILE": ["main.py", "store.py"],
                    },
                },
            },
        },
    }

    async def post(_url, _key, body):
        captured.update(body)
        return {
            "choices": [{"message": _tool_call_message(
                "READ_FILE", path="not-yet-observed.py")}],
            "usage": {},
        }

    verdict = asyncio.run(arbitrate(
        make_transcript(),
        routed,
        SandboxTools(),
        {"READ_FILE", "ANSWER"},
        post=post,
    ))

    read_schema = next(
        tool for tool in captured["tools"]
        if tool["function"]["name"] == "READ_FILE"
    )
    properties = read_schema["function"]["parameters"]["properties"]
    assert "enum" not in properties["path"]["oneOf"][0]
    assert verdict["arguments"]["path"] == "not-yet-observed.py"
    assert verdict["valid"] is True


def test_arbitrate_rejects_text_bearing_pick_without_content():
    # a text-bearing adjudication must author its content in-turn, otherwise
    # the pending tool_call would dangle against the generation step
    verdict = asyncio.run(arbitrate(
        make_transcript(), decision(), SandboxTools(), {"WRITE_FILE"},
        post=_fake_post(_tool_call_message("WRITE_FILE"))))
    assert verdict["valid"] is False


def test_arbitrate_rejects_unknown_action():
    verdict = asyncio.run(arbitrate(
        make_transcript(), decision(), SandboxTools(), {"DONE"},
        post=_fake_post(_tool_call_message("NUKE_EVERYTHING"))))
    assert verdict["valid"] is False


def test_arbitrate_falls_back_to_json_content():
    message = {"role": "assistant", "content": '{"action": "DONE", "target": null}'}
    verdict = asyncio.run(arbitrate(
        make_transcript(), decision(), SandboxTools(), {"DONE"},
        post=_fake_post(message)))
    assert verdict["valid"] is True
    assert verdict["action"] == "DONE"


def test_metrics_records_escalation_pairs():
    metrics = RunMetrics()
    metrics.escalate(decision(), {"action": "DONE", "target": None})
    summary = metrics.summary()
    assert summary["escalations"]["count"] == 1
    assert summary["escalations"]["overridden"] == 1
    assert summary["escalations"]["detail"][0]["from"]["confidence"] == 0.4


def test_transcript_prefix_is_append_only():
    t = make_transcript()
    before = t.messages()
    call_id = t.append_action("LIST_CHATS", {})
    t.append_result(call_id, {"action": "list_chats(3)"})
    t.append_note("[fast-decision note] ...")
    after = t.messages()
    assert after[:len(before)] == before  # stable prefix: cache-friendly
    assert len(after) == len(before) + 3
