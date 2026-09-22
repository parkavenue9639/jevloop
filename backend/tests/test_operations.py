"""Action catalog assembly: core actions + provider tools, with state gating."""

from jevloop.model import action_catalog
from jevloop.state import ChatRef, Workspace
from jevloop.tools.lark import LarkTools


def test_blank_workspace_starts_with_gather_actions_and_answer():
    ws = Workspace()
    actions = action_catalog(ws, LarkTools(None))
    assert "LIST_CHATS" in actions
    # ANSWER is always offered: meta-questions ("what can you do?") need no material
    assert "ANSWER" in actions
    assert "DONE" not in actions and "BLOCKED" not in actions
    # No shortcut candidates does not hide the operation or its LLM binding.
    assert "OPEN_CHAT" in actions and "SEND_MESSAGE" in actions


def test_known_chats_unlock_target_tools():
    ws = Workspace()
    ws.chats["oc_1"] = ChatRef(id="oc_1", name="g1")
    actions = action_catalog(ws, LarkTools(None))
    assert {"OPEN_CHAT", "REPLY_MESSAGE"} <= set(actions)
