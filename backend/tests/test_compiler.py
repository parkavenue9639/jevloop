"""Conditional question compiler behavior and routing-confidence invariants."""

import asyncio

import pytest

from jevloop import model
from jevloop.model import compile_questions
from jevloop.state import ChatRef, Workspace
from jevloop.tools.base import CompositeProvider, ToolSpec
from jevloop.tools.lark import LarkTools
from jevloop.tools.sandbox import SandboxTools


def mounted():
    return CompositeProvider(SandboxTools(), LarkTools(None))


def test_blank_workspace_compiles_branch_specific_write_target():
    _questions, compiled = compile_questions(Workspace(), mounted())
    assert compiled.target_constants[("ACT", "WRITE_FILE")] == "LLM_PARAMETERS"
    assert compiled.target_constants[("INSPECT", "READ_FILE")] == "LLM_PARAMETERS"
    assert "READ_FILE" in compiled.phase_actions["INSPECT"]


def test_same_pool_gets_distinct_branch_heads():
    ws = Workspace()
    ws.chats["oc_1"] = ChatRef(id="oc_1", name="g1")
    ws.chats["oc_2"] = ChatRef(id="oc_2", name="g2", p2p=True)
    ws.recipients["oc_2"] = "g2"
    _questions, compiled = compile_questions(ws, mounted())
    inspect = compiled.target_heads[("INSPECT", "OPEN_CHAT")]
    verify = compiled.target_heads[("VERIFY", "OPEN_CHAT")]
    reply = compiled.target_heads[("ACT", "REPLY_MESSAGE")]
    assert len({inspect, verify, reply}) == 3
    assert compiled.target_heads[("ACT", "SEND_MESSAGE")] != reply


def test_target_criteria_carry_labels_without_cross_action_leaks():
    ws = Workspace()
    ws.files["notes.md"] = "notes.md"
    questions, compiled = compile_questions(ws, mounted())
    read_head = compiled.target_heads[("INSPECT", "READ_FILE")]
    write_head = compiled.target_heads[("ACT", "WRITE_FILE")]
    assert questions[read_head]["criteria"]["notes.md"]["file"] == "notes.md"
    assert '"limit": 200' in questions[read_head]["criteria"]["notes.md"]["arguments"]
    assert "LLM_PARAMETERS" in questions[read_head]["criteria"]
    write_criteria = questions[write_head]["criteria"]
    assert write_criteria["notes.md"]["file"] == "notes.md"
    assert "LLM_PARAMETERS" in write_criteria
    assert "NEW" not in write_criteria


def test_target_filter_narrows_compatibility():
    class P2pOnly:
        def specs(self):
            return [ToolSpec(
                name="P2P_PING",
                description="ping a p2p chat",
                needs_target=True,
                target_pool="chats",
                target_filter=lambda entry: entry.meta.get("p2p"),
            )]

        def available(self, _workspace):
            return {"P2P_PING"}

        async def execute(self, _name, _ctx):
            return {"status": "ready"}

    ws = Workspace()
    ws.chats["oc_group"] = ChatRef(id="oc_group", name="group")
    ws.chats["oc_dm"] = ChatRef(id="oc_dm", name="dm", p2p=True)
    questions, compiled = compile_questions(ws, P2pOnly())
    head = compiled.target_heads[("INSPECT", "P2P_PING")]
    assert set(questions[head]["criteria"]) == {"oc_dm", "LLM_PARAMETERS"}


def test_phase_and_action_criteria_follow_state_gating():
    questions, compiled = compile_questions(Workspace(), mounted())
    assert set(questions["phase"]["criteria"]) == {
        "INSPECT", "ACT", "VERIFY", "RESPOND",
    }
    actions = set().union(*compiled.phase_actions.values())
    assert {"LIST_CHATS", "WRITE_FILE", "BASH", "ANSWER"} <= actions
    assert not {"DONE", "BLOCKED"} & actions
    assert "OPEN_CHAT" in actions


class BranchProvider:
    def specs(self):
        return [
            ToolSpec(name="PING", description="inspect ping", phases=("INSPECT",)),
            ToolSpec(name="PONG", description="inspect pong", phases=("INSPECT",)),
            ToolSpec(name="MAKE", description="act make", phases=("ACT",)),
            ToolSpec(name="EDIT", description="act edit", phases=("ACT",)),
        ]

    def available(self, _workspace):
        return {"PING", "PONG", "MAKE", "EDIT"}


class MultiReadProvider:
    def specs(self):
        return [
            ToolSpec(
                name="READ_FILE",
                description="read files",
                needs_target=True,
                target_pool="files",
                multi_target_max=4,
                phases=("INSPECT",),
            ),
        ]

    def available(self, _workspace):
        return {"READ_FILE"}


def test_choose_compiles_and_consumes_bounded_multi_file_selection(monkeypatch):
    workspace = Workspace(goal="compare files")
    for path in ("a.md", "b.md", "c.md"):
        workspace.files[path] = path
    questions, compiled = compile_questions(workspace, MultiReadProvider())
    branch = ("INSPECT", "READ_FILE")
    mode_head = compiled.target_mode_heads[branch]
    members = compiled.target_member_heads[branch]

    async def fake_post(_endpoint, _key, _body):
        answers = {
            "phase": {
                "choice": "INSPECT",
                "confidence": 0.9,
                "probabilities": {"INSPECT": 0.9, "RESPOND": 0.1},
            },
            mode_head: {
                "choice": "many",
                "confidence": 0.8,
                "probabilities": {"one": 0.2, "many": 0.8},
            },
        }
        for key, head in members.items():
            include = key != "c.md"
            answers[head] = {
                "choice": "include" if include else "skip",
                "confidence": 0.7,
                "probabilities": (
                    {"include": 0.8, "skip": 0.2}
                    if include else {"include": 0.1, "skip": 0.9}
                ),
            }
        return {"answers": answers}

    monkeypatch.setattr(model, "post_json", fake_post)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    decision = asyncio.run(model.choose(
        workspace, "compare files", [], provider=MultiReadProvider()))

    assert decision["target"] == ("a.md", "b.md")
    assert decision["confidence"] == 0.7
    assert set(questions[mode_head]["criteria"]) == {"one", "many"}



def test_multi_file_selection_degrades_to_scalar_when_all_members_skip(monkeypatch):
    workspace = Workspace(goal="read one file")
    for path in ("a.md", "b.md"):
        workspace.files[path] = path
    _questions, compiled = compile_questions(workspace, MultiReadProvider())
    branch = ("INSPECT", "READ_FILE")
    mode_head = compiled.target_mode_heads[branch]
    target_head = compiled.target_heads[branch]

    async def fake_post(_endpoint, _key, _body):
        answers = {
            "phase": {
                "choice": "INSPECT",
                "confidence": 0.9,
                "probabilities": {"INSPECT": 0.9, "RESPOND": 0.1},
            },
            mode_head: {
                "choice": "many",
                "confidence": 0.8,
                "probabilities": {"one": 0.2, "many": 0.8},
            },
            target_head: {
                "choice": "a.md",
                "confidence": 0.75,
                "probabilities": {"a.md": 0.75, "b.md": 0.20, "LLM_PARAMETERS": 0.05},
            },
        }
        for head in compiled.target_member_heads[branch].values():
            answers[head] = {
                "choice": "skip",
                "confidence": 0.9,
                "probabilities": {"include": 0.1, "skip": 0.9},
            }
        return {"answers": answers}

    monkeypatch.setattr(model, "post_json", fake_post)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    decision = asyncio.run(model.choose(
        workspace, "read one file", [], provider=MultiReadProvider()))

    assert decision["target"] == "a.md"
    assert any(
        item["role"] == "target_fallback"
        for item in decision["consumed_heads"]
    )


def test_choose_consumes_selected_heads_and_takes_minimum_confidence(monkeypatch):
    captured = {}
    async def fake_post(_endpoint, _key, _body):
        captured.update(_body)
        return {"answers": {
            "phase": {
                "choice": "INSPECT",
                "confidence": 0.9,
                "probabilities": {
                    "INSPECT": 0.9, "ACT": 0.05, "RESPOND": 0.05,
                },
            },
            "action__inspect": {
                "choice": "PING",
                "confidence": 0.55,
                "probabilities": {"PING": 0.55, "PONG": 0.45},
            },
            "action__act": "malformed but unselected",
            "target__inspect__ping": {
                "choice": "DEFAULT_ARGUMENTS", "confidence": 0.99,
                "probabilities": {"DEFAULT_ARGUMENTS": 0.99, "LLM_PARAMETERS": 0.01},
            },
        }}

    monkeypatch.setattr(model, "post_json", fake_post)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    workspace = Workspace()
    workspace.begin_turn("goal")
    decision = asyncio.run(model.choose(
        workspace, "goal", [{"raw": "must-not-be-duplicated"}],
        provider=BranchProvider(),
    ))
    assert decision["operation"] == "PING"
    assert decision["confidence"] == 0.55
    assert [head["role"] for head in decision["consumed_heads"]] == [
        "phase", "action", "target",
    ]
    assert "must-not-be-duplicated" not in str(captured)
    assert captured["state"]["goal"] == "goal"


def test_choose_rejects_missing_selected_head(monkeypatch):
    async def fake_post(_endpoint, _key, _body):
        return {"answers": {
            "phase": {
                "choice": "INSPECT",
                "confidence": 0.9,
                "probabilities": {
                    "INSPECT": 0.9, "ACT": 0.05, "RESPOND": 0.05,
                },
            },
        }}

    monkeypatch.setattr(model, "post_json", fake_post)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    with pytest.raises(ValueError, match="Invalid Jev response"):
        asyncio.run(model.choose(
            Workspace(), "goal", [], provider=BranchProvider()))


def test_choose_parses_meta_signals_leniently(monkeypatch):
    captured = {}

    def answers_with(meta):
        payload = {
            "target__inspect__ping": {
                "choice": "DEFAULT_ARGUMENTS", "confidence": 0.99,
                "probabilities": {"DEFAULT_ARGUMENTS": 0.99, "LLM_PARAMETERS": 0.01},
            },
            "phase": {
                "choice": "INSPECT",
                "confidence": 0.9,
                "probabilities": {"INSPECT": 0.9, "ACT": 0.05, "RESPOND": 0.05},
            },
            "action__inspect": {
                "choice": "PING",
                "confidence": 0.55,
                "probabilities": {"PING": 0.55, "PONG": 0.45},
            },
        }
        payload.update(meta)
        return payload

    async def fake_post(_endpoint, _key, _body):
        captured.update(_body)
        return {"answers": answers_with({
            "meta_ambiguity": {"type": "noul", "noul": 0.83},
            "meta_progress": {"type": "score", "score": 2.5, "confidence": 0.6},
        })}

    monkeypatch.setattr(model, "post_json", fake_post)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    decision = asyncio.run(model.choose(
        Workspace(), "goal", [], provider=BranchProvider()))

    assert decision["ambiguity"] == 0.83
    assert decision["progress"] == {"score": 2.5, "confidence": 0.6}
    assert captured["questions"]["meta_ambiguity"]["type"] == "noul"
    assert captured["questions"]["meta_progress"]["type"] == "score"
    assert len(captured["questions"]["meta_progress"]["criteria"]) == 4
    assert captured["state"]["decision_surface"]["phases"] == {
        "INSPECT": ["PING", "PONG"],
        "ACT": ["MAKE", "EDIT"],
        "RESPOND": ["ANSWER"],
    }

    async def fake_post_without_meta(_endpoint, _key, _body):
        return {"answers": answers_with({
            "meta_ambiguity": {"type": "noul", "noul": "not-a-number"},
            "meta_progress": {"type": "score", "score": 4.0, "confidence": 2.0},
        })}

    monkeypatch.setattr(model, "post_json", fake_post_without_meta)
    decision = asyncio.run(model.choose(
        Workspace(), "goal", [], provider=BranchProvider()))
    assert "ambiguity" not in decision and "progress" not in decision


def test_begin_turn_clears_transient_evidence_but_preserves_inventory():
    ws = Workspace(goal="old", answer="previous")
    ws.files["kept.txt"] = "kept.txt"
    ws.messages = [{"text": "stale"}]
    ws.doc_content = "stale doc"
    ws.doc_search_done = True
    ws.notes = [{"kind": "file", "text": "stale"}]
    ws.history = [{"operation": "READ_FILE"}]

    ws.begin_turn("new")

    assert ws.goal == "new"
    assert ws.prior_answer == "previous"
    assert ws.answer == ""
    assert ws.files == {"kept.txt": "kept.txt"}
    assert ws.messages == []
    assert ws.doc_content == ""
    assert ws.doc_search_done is False
    assert ws.notes == []
    # cross-turn attempt history is retained (bounded) for decisions; the new
    # turn's guards read only the slice after the boundary
    assert ws.history == [{"operation": "READ_FILE"}]
    assert ws.current_turn_history() == []


def test_file_activity_is_derived_from_current_turn_history():
    ws = Workspace(files={"a.md": "a.md", "b.md": "b.md"})
    ws.begin_turn("goal")
    ws.append_history({
        "operation": "READ_FILE", "target": "a.md",
        "status": "ready", "disposition": "SUCCEEDED",
        "read_files": ["a.md"],
    })
    assert ws.file_activity()["read_current"] == ["a.md"]

    questions, compiled = compile_questions(ws, SandboxTools())
    read_head = compiled.target_heads[("INSPECT", "READ_FILE")]
    assert "already read this turn" in questions[read_head]["criteria"]["a.md"]["file"]
    ws.append_history({
        "operation": "WRITE_FILE", "target": "a.md",
        "status": "ready", "disposition": "SUCCEEDED",
        "changed_files": ["a.md"],
    })
    assert ws.file_activity()["changed_unread"] == ["a.md"]

    ws.append_history({
        "operation": "READ_FILE", "target": ("a.md", "b.md"),
        "status": "ready", "disposition": "SUCCEEDED",
        "read_files": ["a.md", "b.md"],
    })
    activity = ws.file_activity()
    assert activity["read_current"] == ["a.md", "b.md"]
    assert activity["read_counts"] == {"a.md": 2, "b.md": 1}

    ws.append_history({
        "operation": "BASH", "status": "ready", "disposition": "SUCCEEDED",
        "files_may_have_changed": True,
    })
    assert ws.file_activity()["read_current"] == []
    assert ws.file_activity()["files_may_have_changed"] is True
