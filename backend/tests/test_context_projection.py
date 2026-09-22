"""Independent durable/Jev/LLM boundaries, including actual mocked LLM routes."""

import asyncio
import json
from copy import deepcopy

import pytest

from jevloop import sessions
from jevloop.argument_helper import generate_arguments
from jevloop.drivers import DriverContext, PlainLlmDriver
from jevloop.escalation import arbitrate
from jevloop.observations import normalize_observation
from jevloop.projection import rebuild_workspace, record_execution
from jevloop.state import Workspace
from jevloop.text_helper import generate_text
from jevloop.tools.sandbox import SandboxTools
from jevloop.transcript import Transcript

INTERNAL = "INTERNAL_SENTINEL_9ad6"
OUTPUT = "Unique command output: violet telescope"
DOMAIN_DATA = {
    "message_id": "om_domain_123", "document_id": "doc_domain_456",
    "intent_fingerprint": "customer-defined-fingerprint",
    "provenance": {"observation_view": "literal user-supplied document field"},
    "references": [{"id": "user-resource-id", "label": "domain reference"}],
}


def tool_result(messages, call_id):
    message = next(item for item in messages
                   if item.get("role") == "tool" and item.get("tool_call_id") == call_id)
    return json.loads(message["content"])


def serialized(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def has_true(value, fragment):
    if isinstance(value, dict):
        return any(fragment in key and item is True for key, item in value.items()) or any(
            has_true(item, fragment) for item in value.values())
    if isinstance(value, list):
        return any(has_true(item, fragment) for item in value)
    return False


def mixed_ledger():
    ledger = Transcript("system instruction", "Inspect the current project.")
    call = ledger.append_action("BASH", {"command": "printf 'runtime-evidence'"})
    ledger.append_result(call, {
        "status": "ready", "action": "bash", "exit": 0,
        "effect_disposition": "SUCCEEDED", "output": OUTPUT,
        "bash_note": {"command": "printf 'runtime-evidence'", "output": OUTPUT, "exit": 0},
        "observation_id": INTERNAL + "-observation", "attempt_id": INTERNAL + "-attempt",
        "intent_id": INTERNAL + "-intent", "intent_fingerprint": INTERNAL + "-intent-hash",
        "observation_fingerprint": INTERNAL + "-result-hash",
        "provenance": {"decision_source": INTERNAL},
        "future_runtime_feature": {"private": INTERNAL},
        "observation_view": {
            "id": INTERNAL + "-view", "kind": "BASH", "scope": ".",
            "evidence": OUTPUT, "truncated": True, "references": [
                {"id": INTERNAL + "-candidate", "kind": "file", "value": "candidate.py",
                 "label": INTERNAL}], "invocation": {"path": INTERNAL}},
        "observation_kinds": [INTERNAL],
    })
    domain_call = ledger.append_action("CUSTOM_READ", {"id": "doc_domain_456"})
    ledger.append_result(domain_call, {
        "status": "ready", "effect_disposition": "SUCCEEDED", "data": deepcopy(DOMAIN_DATA),
        "receipt": {"message_id": "om_delivery_789"},
        "future_runtime_feature": INTERNAL,
    })
    ledger.append_runtime_note("An earlier attempt was refused; continue safely.", meta={
        "runtime": True, "observation_id": INTERNAL, "attempt_id": INTERNAL,
        "provenance": {"private": INTERNAL},
    })
    return ledger, call, domain_call


def completion():
    return {"choices": [{"finish_reason": "tool_calls", "message": {
        "role": "assistant", "content": None, "tool_calls": [{
            "id": "native-next-call", "type": "function", "function": {
                "name": "ANSWER", "arguments": '{"answer":"finished"}'}}]}}]}


@pytest.mark.parametrize("route", ["arguments", "arbitration", "plain", "text"])
def test_all_real_llm_routes_use_the_clean_projection(monkeypatch, route):
    ledger, _, _ = mixed_ledger()
    provider = SandboxTools()
    prefix = ledger.llm_messages()
    requests = []

    async def post(_url, _key, request):
        requests.append(deepcopy(request))
        if route == "text":
            return {"choices": [{"finish_reason": "stop", "message": {
                "role": "assistant", "content": "finished"}}]}
        return completion()

    monkeypatch.setattr("jevloop.drivers.post_json", post)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-test-only")

    async def run():
        if route == "arguments":
            await generate_arguments(ledger, provider, "ANSWER", post=post)
        elif route == "arbitration":
            result = await arbitrate(ledger, {"operation": "ANSWER", "confidence": 0.2},
                                     provider, {"ANSWER"}, post=post)
            assert result["valid"]
        elif route == "plain":
            await PlainLlmDriver().decide(DriverContext("goal", Workspace(), ledger, provider))
        else:
            await generate_text(ledger, "Write a brief final answer.", post=post)

    asyncio.run(run())
    assert len(requests) == 1
    assert INTERNAL not in serialized(requests[0])
    assert requests[0]["messages"][:len(prefix)] == prefix
    assert "om_domain_123" in serialized(requests[0])
    assert "customer-defined-fingerprint" in serialized(requests[0])
    assert serialized(requests[0]["messages"]).count(OUTPUT) == 1
    assert INTERNAL in serialized(ledger.dump())


def test_recovery_arbitration_note_excludes_internal_observation_identity():
    ledger, _, _ = mixed_ledger()
    requests = []

    async def post(_url, _key, request):
        requests.append(request)
        return completion()

    result = asyncio.run(arbitrate(
        ledger, {"operation": "ANSWER", "confidence": 0.2}, SandboxTools(), {"ANSWER"},
        post=post, recovery={"operation": "WRITE_FILE", "observation_id": INTERNAL,
                             "disposition": "NOT_APPLIED", "error": {
                                 "code": "INVALID_PROPOSAL", "stage": "decision"}}))
    assert result["valid"]
    assert INTERNAL not in serialized(requests)
    assert "INVALID_PROPOSAL" in serialized(requests)
    assert "NOT_APPLIED" in serialized(requests)


def test_projection_strips_only_owned_envelopes_not_nested_domain_data():
    ledger, bash_call, domain_call = mixed_ledger()
    durable = deepcopy(ledger.dump())
    messages = ledger.llm_messages()
    assert messages == ledger.messages()
    assert INTERNAL not in serialized(messages)
    assert tool_result(messages, domain_call)["data"] == DOMAIN_DATA
    assert "om_delivery_789" in serialized(tool_result(messages, domain_call))
    assert tool_result(messages, bash_call)["effect_disposition"] == "SUCCEEDED"
    assert ledger.dump() == durable
    assert serialized(messages).count("printf 'runtime-evidence'") == 1


def test_legacy_duplicate_bash_and_read_evidence_is_rendered_once_with_truncation():
    ledger, bash_call, _ = mixed_ledger()
    content = "Unique file body: the threshold is azure-17"
    call = ledger.append_action("READ_FILE", {"path": "notes.txt", "offset": 7, "limit": 2})
    ledger.append_result(call, {
        "status": "ready", "effect_disposition": "SUCCEEDED", "partial": True,
        "content_excerpt": content,
        "file_results": [{"target": "notes.txt", "status": "ready", "content": content,
                          "offset": 7, "limit": 2, "unit": "lines", "truncated": True},
                         {"target": "missing.txt", "status": "missing"}],
        "observation_view": {"id": INTERNAL, "kind": "READ_FILE", "scope": "notes.txt",
                             "evidence": content, "truncated": True, "references": []},
    })
    messages = ledger.llm_messages()
    assert serialized(messages).count(content) == 1
    assert serialized(messages).count(OUTPUT) == 1
    assert has_true(tool_result(messages, bash_call), "truncat")
    result = tool_result(messages, call)
    assert has_true(result, "truncat") and result["partial"] is True
    assert "missing.txt" in serialized(result) and "missing" in serialized(result)
    assert "notes.txt" in serialized(result) and "lines" in serialized(result)


def test_projection_prefix_stays_stable_when_later_records_are_appended():
    ledger, _, _ = mixed_ledger()
    original = deepcopy(ledger.llm_messages())
    ledger.append_user("A new goal must not rewrite prior evidence.")
    call = ledger.append_action("BASH", {"command": "echo later"})
    ledger.append_result(call, {"status": "ready", "effect_disposition": "SUCCEEDED",
                                "output": "later" * 10_000})
    assert ledger.llm_messages()[:len(original)] == original


def test_all_returned_snapshots_and_incoming_assistant_records_are_detached():
    ledger, _, _ = mixed_ledger()
    original = deepcopy(ledger.dump())
    for method in (ledger.dump, ledger.messages, ledger.llm_messages):
        snapshot = method()
        snapshot[0]["content"] = "mutated system"
        snapshot[2]["tool_calls"][0]["function"]["arguments"] = "mutated arguments"
        snapshot.append({"role": "user", "content": "mutated list"})
        assert ledger.dump() == original
    source = deepcopy(original)
    restored = Transcript.from_messages(source)
    source[2]["tool_calls"][0]["function"]["name"] = "MUTATED"
    assert restored.dump() == original
    message = {"role": "assistant", "content": None, "tool_calls": [{
        "id": "detached-call", "type": "function", "function": {
            "name": "BASH", "arguments": '{"command":"true"}'}}]}
    ledger.append_assistant(message)
    message["tool_calls"][0]["function"]["name"] = "MUTATED"
    assert ledger.dump()[-1]["tool_calls"][0]["function"]["name"] == "BASH"


def test_raw_observation_and_registered_kinds_roundtrip_in_both_views(tmp_path, monkeypatch):
    ledger, live = Transcript("system", "goal"), Workspace()
    raw = {"scope": "src", "evidence": "full-provider-evidence-" * 1200,
           "references": [{"kind": "file", "value": "src/a.py", "label": "a.py"},
                          {"kind": "directory", "value": "src/sub", "label": "sub"}],
           "truncated": False, "offset": 0, "next_offset": 2}
    canonical = {"observation_id": INTERNAL, "attempt_id": INTERNAL + "-attempt",
                 "phase": "INSPECT", "target": "src", "dispatched": True,
                 "fingerprints": {"intent": INTERNAL + "-i", "observation": INTERNAL + "-o"},
                 "provenance": {"decision_source": INTERNAL}}
    call = record_execution(ledger, "CUSTOM_INSPECT", {"path": "src"}, {
        "status": "ready", "effect_disposition": "SUCCEEDED", "observation": raw,
        "data": DOMAIN_DATA,
    }, live, observation=canonical, reference_kinds=("file",))
    result = tool_result(ledger.dump(), call)
    assert result["observation"] == raw
    assert result["observation_kinds"] == ["file"]
    assert "observation_view" not in result
    assert result["data"] == DOMAIN_DATA
    assert len(live.observation_views[0]["evidence"]) < len(raw["evidence"])
    assert set(live.pool_entries("files")) == {"src/a.py"}
    assert not live.pool_entries("directories")
    assert live.observation_views == rebuild_workspace(ledger).observation_views
    before = deepcopy(ledger.dump())
    llm = ledger.llm_messages()
    assert ledger.dump() == before and INTERNAL not in serialized(llm)
    monkeypatch.setattr(sessions, "DIR", tmp_path)
    sessions.save("projection-roundtrip", ledger)
    restored = sessions.load("projection-roundtrip")
    assert restored.dump() == before
    assert restored.llm_messages() == llm
    assert rebuild_workspace(restored).observation_views == live.observation_views
    assert rebuild_workspace(restored).history[-1]["observation_id"] == INTERNAL


@pytest.mark.parametrize("kinds", [None, ()])
def test_unregistered_and_explicit_no_reference_kinds_do_not_gain_authority(kinds):
    ledger, live = Transcript("system", "goal"), Workspace()
    raw = {"scope": "src", "evidence": "a.py", "truncated": False,
           "references": [{"kind": "file", "value": "src/a.py", "label": "a.py"}]}
    call = record_execution(ledger, "CUSTOM_READ", {}, {
        "status": "ready", "observation": raw,
        "observation_kinds": ["file"],  # untrusted provider cannot grant itself kinds
    }, live, reference_kinds=kinds)
    assert tool_result(ledger.dump(), call)["observation_kinds"] == (None if kinds is None else [])
    assert not live.pool_entries("files")
    assert not rebuild_workspace(ledger).pool_entries("files")


def test_long_results_survive_model_caps_without_changing_durable_or_jev(monkeypatch):
    from jevloop import llm_context

    ledger, live = Transcript("system", "goal"), Workspace()
    content = "long raw text with unicode 雪🙂 " * 2500
    raw = {"scope": "notes.txt", "evidence": content, "references": [
        {"kind": "file", "value": "notes.txt", "label": "notes"}], "truncated": False}
    call = record_execution(ledger, "READ_FILE", {"path": "notes.txt"}, {
        "status": "ready", "effect_disposition": "SUCCEEDED", "observation": raw,
        "file_results": [{"target": "notes.txt", "status": "ready", "content": content}],
    }, live, reference_kinds=("file",))
    durable = deepcopy(ledger.dump())
    view = deepcopy(live.observation_views)
    assert tool_result(durable, call)["file_results"][0]["content"] == content
    assert tool_result(durable, call)["observation"]["evidence"] == content
    monkeypatch.setattr(llm_context, "RESULT_CAP", 1600)
    small = tool_result(ledger.llm_messages(), call)
    assert (has_true(small, "truncat") or has_true(small, "omitt")
            or has_true(small, "evidence_bounded"))
    assert len(serialized(small)) < len(content)
    monkeypatch.setattr(llm_context, "RESULT_CAP", 12000)
    larger = tool_result(ledger.llm_messages(), call)
    assert len(serialized(larger)) >= len(serialized(small))
    assert ledger.dump() == durable
    assert live.observation_views == view == rebuild_workspace(ledger).observation_views
    # append_result itself has no dependency on the display cap, even for text bodies.
    monkeypatch.setattr(llm_context, "RESULT_CAP", 1000)
    raw_call = ledger.append_action("LEGACY_TOOL", {})
    ledger.append_result(raw_call, content)
    assert ledger.dump()[-1]["content"] == content


def test_native_multi_call_pairing_failures_unknown_and_superseded_are_preserved():
    ledger = Transcript("system", "goal")
    calls = [{"id": identity, "type": "function", "function": {
        "name": "BASH", "arguments": json.dumps({"command": "echo " + identity})}}
        for identity in ("refused", "unknown", "sibling", "interrupted")]
    ledger.append_assistant({"role": "assistant", "content": None, "tool_calls": calls})
    outcomes = {
        "refused": {"status": "rejected", "effect_disposition": "NOT_APPLIED", "dispatched": False,
                    "reason": "Invalid path", "error": {"code": "INVALID_PROPOSAL", "kind": "validation",
                    "stage": "decision", "recoverability": "recoverable", "message": "Invalid path"}},
        "unknown": {"status": "failed", "effect_disposition": "UNKNOWN", "exit": 1,
                    "dispatched": True, "reason": "Uncertain partial mutation", "error": {
                        "code": "EFFECT_UNKNOWN", "kind": "execution", "stage": "dispatch",
                        "recoverability": "unsafe", "message": "Uncertain partial mutation"}},
        "sibling": {"status": "superseded", "superseded": True, "dispatched": False,
                    "effect_disposition": "NOT_APPLIED", "reason": "Sibling call not executed"},
    }
    for call_id, outcome in outcomes.items():
        ledger.append_result(call_id, {**outcome, "attempt_id": INTERNAL})
    assert ledger.repair() == 1
    projected = ledger.llm_messages()
    assert next(item for item in projected if item.get("tool_calls"))["tool_calls"] == calls
    result_ids = [item["tool_call_id"] for item in projected if item["role"] == "tool"]
    assert len(result_ids) == 4
    assert set(result_ids) == {"refused", "unknown", "sibling", "interrupted"}
    assert result_ids == [item["tool_call_id"] for item in ledger.dump() if item["role"] == "tool"]
    for call_id, outcome in outcomes.items():
        result = tool_result(projected, call_id)
        assert result["effect_disposition"] == outcome["effect_disposition"]
        assert result["status"] == outcome["status"]
        if "error" in outcome:
            assert outcome["error"]["code"] in serialized(result)
    assert tool_result(projected, "sibling")["superseded"] is True
    assert tool_result(projected, "interrupted")["effect_disposition"] == "UNKNOWN"
    assert set(ledger.unresolved_unknown_calls()) == {"unknown", "interrupted"}
    assert INTERNAL not in serialized(projected)
    ledger.resolve_unknowns()
    assert ledger.unresolved_unknown_calls() == []


def test_malformed_legacy_result_stays_uncertain_and_keeps_diagnostic():
    ledger = Transcript("system", "goal")
    call = ledger.append_action("BASH", {"command": "perform operation"})
    ledger.append_result(call, '{"status":"ready","output":"legacy truncated')
    result = tool_result(ledger.llm_messages(), call)
    assert result["effect_disposition"] == "UNKNOWN"
    assert "legacy" in " ".join(strings(result)).lower()
    assert ledger.unresolved_unknown_calls() == [call]


def test_successful_answer_is_not_duplicated_in_its_tool_receipt():
    ledger, live = Transcript("system", "goal"), Workspace()
    answer = "Unique delivered answer: all three receipts matched."
    call = record_execution(ledger, "ANSWER", {"answer": answer}, {
        "status": "done", "effect_disposition": "SUCCEEDED", "answer": answer,
    }, live)
    messages = ledger.llm_messages()
    assert serialized(messages).count(answer) == 1
    assert tool_result(messages, call)["effect_disposition"] == "SUCCEEDED"
    assert rebuild_workspace(ledger).answer == answer


def test_old_bounded_view_and_new_raw_observation_restore_together():
    ledger = Transcript("system", "goal")
    old_call = ledger.append_action("READ_FILE", {"path": "old/a.py"})
    old_view = normalize_observation({
        "scope": "old/a.py", "evidence": "legacy body", "truncated": True,
        "references": [{"kind": "file", "value": "old/a.py", "label": "old"}],
    }, operation="READ_FILE", call_id=old_call, arguments={"path": "old/a.py"})
    ledger.append_result(old_call, {"status": "ready", "effect_disposition": "SUCCEEDED",
                                    "observation_view": old_view})
    live = rebuild_workspace(ledger)
    new_call = record_execution(ledger, "READ_FILE", {"path": "new/b.py"}, {
        "status": "ready", "effect_disposition": "SUCCEEDED", "observation": {
            "scope": "new/b.py", "evidence": "new body", "truncated": False,
            "references": [{"kind": "file", "value": "new/b.py", "label": "new"}]},
    }, live, reference_kinds=("file",))
    restored = Transcript.from_messages(json.loads(json.dumps(ledger.dump())))
    assert tool_result(restored.dump(), old_call)["observation_view"] == old_view
    assert "observation_view" not in tool_result(restored.dump(), new_call)
    assert restored.llm_messages() == ledger.llm_messages()
    assert rebuild_workspace(restored).observation_views == live.observation_views
    assert set(rebuild_workspace(restored).pool_entries("files")) == {"old/a.py", "new/b.py"}
    assert has_true(tool_result(restored.llm_messages(), old_call), "truncat")


def test_committed_arguments_and_recovery_metadata_are_not_llm_display_bounded(monkeypatch):
    from jevloop import llm_context

    ledger = Transcript("system", "goal")
    arguments = {"path": "large.txt", "content": "unabridged committed argument " * 2000}
    call = ledger.append_action("WRITE_FILE", arguments)
    ledger.append_result(call, {"status": "failed", "effect_disposition": "UNKNOWN",
                                "observation_id": INTERNAL, "reason": "dispatch interrupted"})
    metadata = {"runtime": True, "diagnostic": "full durable recovery metadata " * 2000}
    ledger.append_runtime_note("Waiting for explicit recovery.", meta=metadata)
    monkeypatch.setattr(llm_context, "RESULT_CAP", 800)
    projected = ledger.llm_messages()
    assert json.loads(projected[2]["tool_calls"][0]["function"]["arguments"]) == arguments
    assert ledger.dump()[-1]["_runtime_note"] == metadata
    assert metadata["diagnostic"] not in serialized(projected)
    assert ledger.unresolved_unknown_calls() == [call]


def test_malformed_legacy_bash_note_does_not_crash_the_llm_projection():
    ledger = Transcript("system", "goal")
    call = ledger.append_action("BASH", {"command": "inspect"})
    ledger.append_result(call, {"status": "failed", "effect_disposition": "UNKNOWN",
                                "bash_note": "malformed historical note", "output": "usable partial output"})
    result = tool_result(ledger.llm_messages(), call)
    assert result["effect_disposition"] == "UNKNOWN"
    assert "usable partial output" in serialized(result)
    assert ledger.unresolved_unknown_calls() == [call]
def test_new_bash_observation_takes_precedence_over_longer_legacy_note():
    from jevloop.llm_context import result_view

    result = result_view({
        "status": "ready", "observation": {"evidence": "current", "scope": "."},
        "bash_note": {"output": "old output that is much longer"},
    }, "BASH")
    assert result["output"] == "current"
    assert "old output" not in json.dumps(result)


def test_malformed_legacy_structure_cannot_overflow_display_budget():
    from jevloop.llm_context import RESULT_CAP, _serialize, result_view

    source = {"status": "failed", "effect_disposition": "UNKNOWN",
              "effect_proof": list(range(10000)),
              "error": {"code": "EFFECT_UNKNOWN", "recoverability": "unsafe"}}
    projected = _serialize(result_view(source, "BASH"))
    assert len(projected) <= RESULT_CAP
    assert json.loads(projected)["effect_disposition"] == "UNKNOWN"
    assert json.loads(projected)["error"]["recoverability"] == "unsafe"
