"""P1 context architecture: ledger as single truth, contexts recovered from it."""

import asyncio
import json

import pytest

from jevloop import sessions
from jevloop.guardrails import MalformedAuthoredValue
from jevloop.projection import rebuild_workspace, record_execution
from jevloop.state import ChatRef, Workspace
from jevloop.text_helper import _clean, generate_text
from jevloop.tools.sandbox import SandboxTools
from jevloop.transcript import Transcript, full_tool_schemas, system_prompt


def make_ledger():
    return Transcript(system_prompt(SandboxTools()), "test goal")


def test_rebuild_recovers_workspace_from_ledger():
    ledger = make_ledger()
    live = Workspace()
    live.chats["oc_1"] = ChatRef(id="oc_1", name="g1", p2p=True, via_user_id=True)
    live.recipients["oc_1"] = "g1"
    live.files["a.md"] = "a.md"
    live.messages = [{"sender": "u", "time": "t", "text": "hi"}]
    live.answer = "done"

    record_execution(ledger, "LIST_CHATS", {}, {"status": "ready", "action": "list_chats(1)"}, live)
    record_execution(ledger, "OPEN_CHAT", {"target": "oc_1"}, {"status": "ready", "action": "open"}, live)
    record_execution(ledger, "LIST_FILES", {}, {"status": "ready", "action": "list_files(1)"}, live)
    record_execution(ledger, "ANSWER", {}, {"status": "done", "action": "answer", "answer": "done"}, live)

    rebuilt = rebuild_workspace(ledger)
    assert set(rebuilt.chats) == {"oc_1"}
    assert rebuilt.chats["oc_1"].p2p is True
    assert rebuilt.chats["oc_1"].via_user_id is True   # routing metadata survives
    assert rebuilt.recipients == {"oc_1": "g1"}
    assert rebuilt.files == {"a.md": "a.md"}
    assert rebuilt.messages == live.messages
    assert rebuilt.answer == "done"


def test_rebuild_recovers_files_created_without_a_list_step():
    ledger = make_ledger()
    live = Workspace()
    live.files["created.md"] = "created.md"
    record_execution(
        ledger,
        "WRITE_FILE",
        {"target": "NEW", "content": "created.md\nbody"},
        {"status": "ready", "action": "write_file(created.md)"},
        live,
    )

    assert rebuild_workspace(ledger).files == {"created.md": "created.md"}


def test_rebuild_roundtrips_structured_bash_note():
    ledger = make_ledger()
    live = Workspace()
    live.notes.append({
        "kind": "bash",
        "command": "python app.py",
        "output": "server ready",
        "exit": 0,
    })
    record_execution(
        ledger,
        "BASH",
        {"command": "python app.py"},
        {"status": "ready", "action": "bash", "output": "server ready", "exit": 0},
        live,
    )

    assert rebuild_workspace(ledger).notes == live.notes


def test_rebuild_roundtrips_multi_file_read_evidence():
    ledger = make_ledger()
    outcome = {
        "status": "ready",
        "action": "read_file(2 files, 9 chars)",
        "read_files": ["a.md", "b.md"],
        "file_results": [
            {"target": "a.md", "status": "ready", "content": "alpha"},
            {"target": "b.md", "status": "ready", "content": "beta"},
        ],
    }
    record_execution(
        ledger,
        "READ_FILE",
        {"targets": ["a.md", "b.md"]},
        outcome,
        Workspace(),
    )

    rebuilt = rebuild_workspace(ledger)

    assert [note["target"] for note in rebuilt.notes] == ["a.md", "b.md"]
    assert rebuilt.history[-1]["target"] == ["a.md", "b.md"]
    assert rebuilt.history[-1]["read_files"] == ["a.md", "b.md"]


def test_record_execution_reuses_pending_call_id():
    ledger = make_ledger()
    call_id = ledger.append_action("OPEN_CHAT", {"target": "oc_1"})
    record_execution(ledger, "OPEN_CHAT", {"target": "oc_1"}, {"status": "ready"},
                     Workspace(), call_id=call_id)
    roles = [m["role"] for m in ledger.messages()]
    assert roles.count("assistant") == 1  # no ghost turn: the real call was answered
    rebuilt = rebuild_workspace(ledger)
    assert rebuilt.messages == []  # result carried no messages


def test_generate_text_records_author_turn():
    ledger = make_ledger()

    async def fake_post(url, key, body):
        return {"choices": [{"message": {"role": "assistant",
                                         "content": "``` \nfile body\n```"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3}}

    text, _info = asyncio.run(generate_text(ledger, "write the file body", post=fake_post))
    assert text == "file body"  # code fences stripped
    messages = ledger.messages()
    assert messages[-2]["role"] == "user"        # instruction turn
    assert messages[-1]["role"] == "assistant"   # genuine author turn
    assert messages[-1]["content"] == "``` \nfile body\n```"  # verbatim in history


def test_clean_extracts_provider_serialized_dsml_parameter():
    serialized = (
        '<｜｜DSML｜｜ calls><｜｜DSML｜｜ invoke name="ANSWER">'
        '<｜｜DSML｜｜ parameter name="content" string="true">'
        'final-smoke</｜｜DSML｜｜ parameter>'
        '</｜｜DSML｜｜ invoke></｜｜DSML｜｜ calls>'
    )
    assert _clean(serialized) == "final-smoke"


def test_clean_rejects_unterminated_dsml_fragment():
    # the exact 55-char fragment from the recorded run must not become content
    fragment = '<｜｜DSML｜｜ parameter name="target" string="true">main.py'
    with pytest.raises(MalformedAuthoredValue) as excinfo:
        _clean(fragment)
    assert excinfo.value.code == "MALFORMED_AUTHORED_VALUE"
    assert excinfo.value.recoverability == "recoverable"
    assert excinfo.value.stage == "authoring"


def test_clean_keeps_dsml_examples_inside_an_authored_document():
    document = (
        "The wire format looks like this:\n"
        '<｜｜DSML｜｜ parameter name="x" string="true">v</｜｜DSML｜｜ parameter>\n'
        "Use it as data only."
    )
    assert _clean(document) == document  # DSML inside framed content is data


def test_clean_rejects_truncated_code_fence():
    with pytest.raises(MalformedAuthoredValue):
        _clean("```\nthe file body continues past the end")


def test_clean_rejects_closed_parameter_of_the_wrong_name():
    # a closed serialization of `target` must not become the authored `content`
    serialized = (
        '<｜｜DSML｜｜ calls><｜｜DSML｜｜ invoke name="WRITE_FILE">'
        '<｜｜DSML｜｜ parameter name="target" string="true">'
        'main.py</｜｜DSML｜｜ parameter>'
        '</｜｜DSML｜｜ invoke></｜｜DSML｜｜ calls>'
    )
    with pytest.raises(MalformedAuthoredValue) as excinfo:
        _clean(serialized, field="content")
    assert "closed protocol parameter" in str(excinfo.value)
    # ...while the matching parameter name still unwraps
    content_serialized = serialized.replace('name="target"', 'name="content"')
    assert _clean(content_serialized, field="content") == "main.py"


def test_clean_rejects_protocol_call_for_wrong_operation():
    serialized = (
        '<｜｜DSML｜｜ calls><｜｜DSML｜｜ invoke name="BASH">'
        '<｜｜DSML｜｜ parameter name="content" string="true">'
        'echo unsafe</｜｜DSML｜｜ parameter>'
        '</｜｜DSML｜｜ invoke></｜｜DSML｜｜ calls>'
    )
    with pytest.raises(MalformedAuthoredValue, match="not the requested"):
        _clean(serialized, field="content", operation="WRITE_FILE")


def test_generate_text_rejects_unexpected_tool_calls_without_ledger_damage():
    ledger = make_ledger()

    async def tool_call_post(_url, _key, _body):
        return {"choices": [{"message": {
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "ghost-1", "type": "function",
                            "function": {"name": "BASH", "arguments": "{}"}}],
        }}], "usage": {"prompt_tokens": 5, "completion_tokens": 3}}

    with pytest.raises(MalformedAuthoredValue) as excinfo:
        asyncio.run(generate_text(ledger, "write the file", post=tool_call_post))
    assert excinfo.value.helper_info["usage"]["prompt_tokens"] == 5
    messages = ledger.messages()
    # nothing was committed: no dangling assistant tool call entered the ledger
    assert all(message.get("role") != "assistant" for message in messages)


def test_long_delivered_answer_roundtrips_for_the_next_turn():
    ledger = make_ledger()
    long_answer = "head-" + "x" * 5000 + "-tail"  # longer than any excerpt cap
    record_execution(
        ledger, "ANSWER", {"answer": long_answer},
        {"status": "done", "action": "answer", "answer": long_answer},
        Workspace())

    rebuilt = rebuild_workspace(ledger)
    # the FULL successful ANSWER argument is recovered, not a prefix excerpt
    assert rebuilt.answer == long_answer
    rebuilt.begin_turn("follow-up goal")
    assert rebuilt.prior_answer == long_answer
    # one explicit excerpt at render time: the most recent (tail) characters
    rendered = rebuilt.state()["previous_answer"]
    assert rendered == long_answer[-4000:]
    assert rendered.endswith("-tail") and not rendered.startswith("head-")


def test_append_result_keeps_large_envelopes_parseable():
    ledger = make_ledger()
    call_id = ledger.append_action("SEARCH_CHATS", {})
    payload = {"status": "ready", "action": "search",
               "observation_id": "o" * 32, "attempt_id": "a" * 32,
               "chats": [{"id": f"c{i}", "name": "n" * 3000} for i in range(6)]}
    ledger.append_result(call_id, payload)

    tool_message = next(message for message in ledger.messages()
                        if message.get("role") == "tool")
    assert len(tool_message["content"]) <= 12000
    parsed = json.loads(tool_message["content"])  # never a byte-sliced JSON
    assert parsed["status"] == "ready"
    # Recovery identities remain exact in the source, not in the LLM view.
    assert "observation_id" not in parsed and "attempt_id" not in parsed
    assert json.loads(ledger.dump()[-1]["content"]) == payload
    assert [chat["id"] for chat in parsed["chats"]] == [f"c{i}" for i in range(6)]


def test_result_omission_fallback_preserves_mandatory_envelope_fields():
    ledger = make_ledger()
    call_id = ledger.append_action("SEARCH_CHATS", {})
    # a payload whose structure alone exceeds the cap: 300 keys survive only
    # through the omission fallback
    payload = {"status": "failed", "action": "search",
               "effect_disposition": "UNKNOWN",
               "error": {"code": "EFFECT_UNKNOWN", "kind": "execution",
                         "stage": "dispatch", "recoverability": "unsafe",
                         "message": "uncertain"},
               "observation_id": "obs-1", "attempt_id": "att-1",
               "chats": [{"id": f"c{i}", "name": "n" * 300} for i in range(300)]}
    ledger.append_result(call_id, payload)

    tool_message = next(message for message in ledger.messages()
                        if message.get("role") == "tool")
    parsed = json.loads(tool_message["content"])
    assert parsed["result_omitted"] is True
    # the mandatory envelope survives even in the fallback
    assert parsed["status"] == "failed"
    assert parsed["effect_disposition"] == "UNKNOWN"
    assert parsed["error"]["code"] == "EFFECT_UNKNOWN"
    assert parsed["error"]["recoverability"] == "unsafe"
    assert "observation_id" not in parsed and "attempt_id" not in parsed
    assert json.loads(ledger.dump()[-1]["content"]) == payload
    assert "chats" not in parsed  # only bulky evidence drops


def test_validate_authored_value_keeps_native_dsml_as_data():
    from jevloop.text_helper import validate_authored_value
    serialized = (
        '<｜｜DSML｜｜ calls><｜｜DSML｜｜ invoke name="WRITE_FILE">'
        '<｜｜DSML｜｜ parameter name="content" string="true">'
        'the body</｜｜DSML｜｜ parameter>'
        '</｜｜DSML｜｜ invoke></｜｜DSML｜｜ calls>'
    )
    # typed-envelope content is data: never unwrapped, whatever the name
    assert validate_authored_value(serialized) == serialized
    # unterminated fragments still fail before dispatch
    with pytest.raises(MalformedAuthoredValue):
        validate_authored_value(
            '<｜｜DSML｜｜ parameter name="target" string="true">main.py')


def test_generate_text_rejects_token_limit_truncation():
    ledger = make_ledger()

    async def length_post(_url, _key, _body):
        return {"choices": [{"finish_reason": "length",
                             "message": {"role": "assistant",
                                         "content": "partial ans"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3}}

    with pytest.raises(MalformedAuthoredValue) as excinfo:
        asyncio.run(generate_text(ledger, "write the file", post=length_post))
    assert "truncated" in str(excinfo.value)
    assert excinfo.value.helper_info["usage"]["prompt_tokens"] == 5
    assert all(message.get("role") != "assistant"
               for message in ledger.messages())


def test_unresolved_scan_resets_order_on_resolution_note():
    ledger = make_ledger()
    call = ledger.append_action("EXEC", {})
    ledger.append_result(call, {"status": "stopped",
                                "effect_disposition": "UNKNOWN"})
    assert ledger.unresolved_unknown_calls() == [call]
    assert ledger.resolve_unknowns() == []           # order reset, not just membership
    assert ledger.unresolved_unknown_calls() == []   # durably unblocked


def test_v1_failed_recoverable_counts_as_unresolved_unknown():
    ledger = make_ledger()
    call = ledger.append_action("EXEC", {"content": "make build"})
    ledger.append_result(call, {"status": "failed", "exit": 3,
                                "effect_disposition": "FAILED_RECOVERABLE"})
    # consistent with projection's conservative read: v1 mutating failures gate
    assert ledger.unresolved_unknown_calls() == [call]


def test_clean_rejects_multi_parameter_serialization():
    serialized = (
        '<｜｜DSML｜｜ calls><｜｜DSML｜｜ invoke name="WRITE_FILE">'
        '<｜｜DSML｜｜ parameter name="content" string="true">'
        'body</｜｜DSML｜｜ parameter>'
        '<｜｜DSML｜｜ parameter name="target" string="true">'
        'main.py</｜｜DSML｜｜ parameter>'
        '</｜｜DSML｜｜ invoke></｜｜DSML｜｜ calls>'
    )
    with pytest.raises(MalformedAuthoredValue, match="multi-parameter"):
        _clean(serialized, field="content")


def test_generate_text_raises_malformed_for_fragment_without_author_turn():
    ledger = make_ledger()

    async def fragment_post(_url, _key, _body):
        return {"choices": [{"message": {
            "role": "assistant",
            "content": '<｜｜DSML｜｜ parameter name="target" string="true">main.py',
        }}], "usage": {"prompt_tokens": 5, "completion_tokens": 3}}

    with pytest.raises(MalformedAuthoredValue) as excinfo:
        asyncio.run(generate_text(ledger, "write the file", post=fragment_post))
    assert excinfo.value.helper_info["usage"]["prompt_tokens"] == 5
    messages = ledger.messages()
    assert all(message.get("role") != "assistant" for message in messages)


def test_rebuild_recovers_typed_error_history_from_ledger():
    ledger = make_ledger()
    call_id = ledger.append_action("EXEC", {"content": "python --version"})
    ledger.append_result(call_id, {
        "status": "rejected",
        "reason": "repeated twice",
        "effect_disposition": "NOT_APPLIED",
        "error": {"code": "DUPLICATE_NO_PROGRESS", "kind": "no_progress",
                  "stage": "preflight", "recoverability": "recoverable",
                  "message": "repeated twice"},
    })

    rebuilt = rebuild_workspace(ledger)
    entry = rebuilt.history[-1]
    assert entry["operation"] == "EXEC"
    assert entry["disposition"] == "NOT_APPLIED"
    assert entry["error"]["code"] == "DUPLICATE_NO_PROGRESS"
    assert entry["error"]["recoverability"] == "recoverable"
    assert entry["dispatched"] is False


def test_rebuild_reads_legacy_dispositions_conservatively():
    ledger = make_ledger()
    denied = ledger.append_action("READ_FILE", {"target": "a.md"})
    ledger.append_result(denied, {"status": "stopped", "reason": "policy",
                                  "effect_disposition": "DENIED"})
    failed = ledger.append_action("EXEC", {"content": "make build"})
    ledger.append_result(failed, {"status": "failed", "exit": 3,
                                  "effect_disposition": "FAILED_RECOVERABLE"})

    rebuilt = rebuild_workspace(ledger)
    assert rebuilt.history[0]["disposition"] == "NOT_APPLIED"  # v1 DENIED
    assert rebuilt.history[1]["disposition"] == "UNKNOWN"      # v1 mutating failure


def test_runtime_note_metadata_is_persisted_but_not_sent_to_providers():
    ledger = make_ledger()
    ledger.append_runtime_note("[runtime observation] decision was not applied", meta={
        "runtime": True, "operation": None, "status": "rejected",
        "disposition": "NOT_APPLIED",
        "error": {"code": "INVALID_PROPOSAL", "kind": "validation",
                  "stage": "decision", "recoverability": "recoverable",
                  "message": "no usable proposal"},
    })

    provider_view = ledger.messages()
    assert provider_view[-1]["role"] == "user"
    assert "_runtime_note" not in provider_view[-1]
    assert "_runtime_note" in ledger.dump()[-1]

    rebuilt = rebuild_workspace(ledger)
    entry = rebuilt.history[-1]
    assert entry["error"]["code"] == "INVALID_PROPOSAL"
    assert entry["dispatched"] is False


def test_baseline_and_main_share_system_and_schemas():
    provider = SandboxTools()
    schemas = full_tool_schemas(provider)
    names = {s["function"]["name"] for s in schemas}
    assert {"LIST_FILES", "READ_FILE", "WRITE_FILE", "BASH", "ANSWER", "DONE"} <= names
    write = next(s for s in schemas if s["function"]["name"] == "WRITE_FILE")
    assert "content" in write["function"]["parameters"]["properties"]
    read = next(s for s in schemas if s["function"]["name"] == "READ_FILE")
    parameters = read["function"]["parameters"]
    # Canonical path supports direct/open paths and a bounded coherent batch.
    path_options = parameters["properties"]["path"]["oneOf"]
    assert path_options[0]["type"] == "string"
    assert path_options[1]["maxItems"] == 4
    assert path_options[1]["uniqueItems"] is True
    assert "path" in parameters["required"]
    assert {"offset", "limit"} <= set(parameters["properties"])
    assert system_prompt(provider).startswith("You are an agent")


def test_cache_scope_is_first_and_lane_specific_without_changing_prompt_shape():
    provider = SandboxTools()
    jev = system_prompt(provider, cache_scope="a" * 24)
    baseline = system_prompt(provider, cache_scope="b" * 24)

    assert jev.startswith(f"[cache-scope:{'a' * 24}]\n")
    assert baseline.startswith(f"[cache-scope:{'b' * 24}]\n")
    assert len(jev) == len(baseline)
    assert jev.splitlines()[1:] == baseline.splitlines()[1:]
    assert system_prompt(provider).startswith("You are an agent")


def test_sessions_roundtrip_is_ledger_only(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "DIR", tmp_path)
    ledger = make_ledger()
    call_id = ledger.append_action("LIST_FILES", {})
    ledger.append_result(call_id, {"action": "list_files(2)"})
    sessions.save("s1", ledger)

    restored = sessions.load("s1")
    assert restored.dump() == ledger.dump()
    assert sessions.load("missing") is None
    # workspace is derivable end-to-end from the persisted ledger
    workspace = rebuild_workspace(restored)
    assert isinstance(workspace, Workspace)


def test_transcript_repair_restores_invariant():
    ledger = make_ledger()
    ledger.append_action("BASH", {})           # dangling — no tool response
    ledger.append_user("[content request] ...")  # orphan instruction turn
    inserted = ledger.repair()
    assert inserted == 1
    messages = ledger.dump()
    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    assert "interrupted" in tool_msgs[-1]["content"]
    # idempotent
    assert ledger.repair() == 0
