"""Lark tool provider: mounts the Feishu (lark-cli) action surface into the framework.

Wraps the LarkAdapter transport layer; owns the tool catalog, validity gating and
execution merging. The framework core never imports anything lark-specific.
"""

from .base import ToolContext, ToolSpec

SEARCH_TEXT = ("Return a short keyword string to search for, derived from the goal; "
               "or null only if no keyword can possibly be inferred.")
DOC_TEXT = ("Write the complete document body (markdown) from the goal and gathered "
            "material; never return null when material exists.")
MESSAGE_TEXT = ("Write the complete message content from the goal and gathered "
                "material; never return null when material exists.")

SPECS = [
    ToolSpec(
        name="LIST_CHATS",
        description="Enumerate the chats you have joined, with names. Preferred first "
                    "gather step when the target chat is unknown: needs no query, and "
                    "one listing serves any target named in the goal.",
        phases=("INSPECT",),
    ),
    ToolSpec(
        name="SEARCH_CHATS",
        description="Find chats by keyword. Use only when LIST_CHATS has already run "
                    "and the target is missing, or the joined list is too large to scan.",
        needs_text=True, text_instruction=SEARCH_TEXT, consumes=("goal",),
        phases=("INSPECT",),
    ),
    ToolSpec(
        name="OPEN_CHAT",
        description="Read recent messages of a known chat to gather discussion content.",
        needs_target=True, target_pool="chats",
        phases=("INSPECT", "VERIFY"),
    ),
    ToolSpec(
        name="SEARCH_DOCS",
        description="Find documents by keyword when the target document is not known yet.",
        needs_text=True, text_instruction=SEARCH_TEXT, consumes=("goal",),
        phases=("INSPECT",),
    ),
    ToolSpec(
        name="OPEN_DOC",
        description="Read the content of a known document.",
        needs_target=True, target_pool="docs",
        phases=("INSPECT", "VERIFY"),
    ),
    ToolSpec(
        name="CREATE_DOC",
        description="Create a new document (title/body come from the text helper).",
        needs_text=True, text_instruction=DOC_TEXT,
        consumes=("goal", "messages", "doc", "notes"), write=True,
        phases=("ACT",),
    ),
    ToolSpec(
        name="WRITE_DOC",
        description="Append content to a known document.",
        needs_target=True, target_pool="docs",
        needs_text=True, text_instruction=DOC_TEXT,
        consumes=("goal", "messages", "doc", "notes"), write=True,
        phases=("ACT",),
    ),
    ToolSpec(
        name="SEND_MESSAGE",
        description="Send a message to a known recipient (content comes from the text helper).",
        needs_target=True, target_pool="recipients",
        needs_text=True, text_instruction=MESSAGE_TEXT,
        consumes=("goal", "messages", "doc", "notes"), write=True, recipient_gate=True,
        phases=("ACT",),
    ),
    ToolSpec(
        name="REPLY_MESSAGE",
        description="Reply within the target chat (content comes from the text helper).",
        needs_target=True, target_pool="chats",
        needs_text=True, text_instruction=MESSAGE_TEXT,
        consumes=("goal", "messages", "doc", "notes"), write=True, recipient_gate=True,
        phases=("ACT",),
    ),
]

_SPEC_BY_NAME = {spec.name: spec for spec in SPECS}


def _docref_from_create(data, title):
    from ..state import DocRef

    def walk(node):
        if isinstance(node, dict):
            url = node.get("url") or node.get("doc_url")
            token = node.get("token") or node.get("doc_token") or node.get("document_id")
            if isinstance(url, str) and "http" in url:
                return DocRef(id=str(token or url), title=title, url=url)
            if token:
                return DocRef(id=str(token), title=title, url=str(url or ""))
            for value in node.values():
                found = walk(value)
                if found:
                    return found
        elif isinstance(node, list):
            for value in node:
                found = walk(value)
                if found:
                    return found
        return None

    return walk(data)


class LarkTools:
    """Tool provider backed by the lark-cli adapter (user identity)."""

    def __init__(self, adapter):
        self.adapter = adapter

    def specs(self):
        return SPECS

    def available(self, workspace):
        # Lack of observed bindings does not hide a tool. Closed references
        # and recipient authorization are still checked before dispatch.
        return {spec.name for spec in SPECS}

    async def execute(self, name, ctx: ToolContext) -> dict:
        workspace, target, text = ctx.workspace, ctx.target, ctx.text
        adapter = self.adapter
        label = self._label(workspace, target)
        if name == "LIST_CHATS":
            for ref in await adapter.list_chats():
                self._merge_chat(workspace, ref)
            return {"status": "ready", "action": "list_chats"}
        if name == "SEARCH_CHATS":
            for ref in await adapter.search_chats(text):
                self._merge_chat(workspace, ref)
            return {"status": "ready", "action": f"search_chats({text!r})"}
        if name == "OPEN_CHAT":
            workspace.messages = await adapter.open_chat(target)
            return {"status": "ready", "action": f"open_chat({label})"}
        if name == "SEARCH_DOCS":
            for ref in await adapter.search_docs(text):
                if ref.id:
                    workspace.docs[ref.id] = ref
            workspace.doc_search_done = True
            return {"status": "ready", "action": f"search_docs({text!r})"}
        if name == "OPEN_DOC":
            data = await adapter.open_doc(target)
            workspace.doc_content = str(data)
            return {"status": "ready", "action": f"open_doc({label})"}
        if name == "CREATE_DOC":
            data = await adapter.create_doc(title=text.splitlines()[0][:100], body=text,
                                            dry_run=not ctx.live)
            outcome = {"status": "ready", "action": "create_doc", "dry_run": not ctx.live}
            if ctx.live:
                ref = _docref_from_create(data, title=text.splitlines()[0][:100])
                if ref and ref.id:
                    workspace.docs[ref.id] = ref
                    outcome["created"] = ref.url or ref.id
            return outcome
        if name == "WRITE_DOC":
            await adapter.write_doc(target, body=text, dry_run=not ctx.live)
            return {"status": "ready", "action": f"write_doc({label})", "dry_run": not ctx.live}
        if name == "SEND_MESSAGE":
            ref = workspace.chats.get(target)
            data = await adapter.send_message(
                target,
                content=text,
                idempotency_key=ctx.idempotency_key,
                dry_run=not ctx.live,
                via_user_id=ref.via_user_id if ref else None,
            )
            return {
                "status": "ready",
                "action": f"send_message({label})",
                "dry_run": not ctx.live,
                "receipt": data,
            }
        if name == "REPLY_MESSAGE":
            data = await adapter.reply_message(
                target,
                content=text,
                idempotency_key=ctx.idempotency_key,
                dry_run=not ctx.live,
            )
            return {
                "status": "ready",
                "action": f"reply({label})",
                "dry_run": not ctx.live,
                "receipt": data,
            }
        raise KeyError(f"unknown tool {name}")

    @staticmethod
    def _label(workspace, target):
        if not target:
            return ""
        ref = workspace.chats.get(target) or workspace.docs.get(target)
        return getattr(ref, "name", None) or getattr(ref, "title", None) \
            or workspace.recipients.get(target, str(target)[:12])

    def _merge_chat(self, workspace, ref):
        if not ref.id:
            return
        if ref.id in self.adapter._send_by_user_id:
            ref.via_user_id = True
        workspace.chats[ref.id] = ref
        if ref.p2p:
            workspace.recipients[ref.id] = ref.name
