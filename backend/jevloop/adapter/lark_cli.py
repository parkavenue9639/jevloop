"""Feishu adapter: drive lark-cli as an async subprocess, parse envelopes,
return normalized objects.

Envelope contract (verified against @larksuite/cli v1.0.89):
- success: stdout ``{"ok": true, "identity", "data", "meta"}``, exit code 0
- error:   stderr ``{"ok": false, "error": {...}}``, non-zero exit code
- success MUST be checked via ok==true or exit code, never via code==0
- exit code 10 is the high-risk confirmation gate: surfaced, never auto-bypassed
- every command accepts --dry-run; sends accept --idempotency-key

Response shapes verified with live read calls on 2026-09-20 (user identity):
- im +chat-list        -> data.chats[]            {chat_id, name, p2p_target_id?}
- im +chat-messages... -> data.messages[]         {message_id, sender{name}, create_time, content, msg_type}
- docs +search         -> data.results[]          {title_highlighted, result_meta{token, url}}
"""

import asyncio
import json
import os
import re

from ..state import ChatRef, DocRef

LARK_ENV = {
    "LARKSUITE_CLI_NO_UPDATE_NOTIFIER": "1",
    "LARKSUITE_CLI_NO_SKILLS_NOTIFIER": "1",
}

_HIGHLIGHT = re.compile(r"</?h>")


class ConfirmationRequired(Exception):
    """lark-cli exit code 10: a human must confirm. Never append --yes automatically."""


class LarkCliError(Exception):
    def __init__(self, payload):
        self.payload = payload
        super().__init__(payload.get("error", {}).get("message", "unknown lark-cli error"))


async def _spawn(argv, env):
    return await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env
    )


async def run(args, dry_run=False):
    """Run lark-cli and return the `data` field on success; raise on anything else."""
    argv = ["lark-cli", *args, "--format", "json"]
    if dry_run:
        argv.append("--dry-run")
    env = {**os.environ, **LARK_ENV}  # merge: replacing env wholesale would drop PATH
    proc = await _spawn(argv, env)
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
    out_text, err_text = stdout.decode(errors="replace"), stderr.decode(errors="replace")
    if proc.returncode == 10:
        raise ConfirmationRequired(err_text.strip())
    if proc.returncode != 0:
        try:
            raise LarkCliError(json.loads(err_text))
        except json.JSONDecodeError:
            raise LarkCliError({"error": {"message": err_text.strip()[:500]}}) from None
    envelope = json.loads(out_text)
    if envelope.get("ok") is not True:
        raise LarkCliError(envelope)
    return envelope.get("data", {})


def _chatref(item):
    p2p = bool(item.get("p2p_target_id"))
    return ChatRef(id=str(item.get("chat_id", "")), name=item.get("name", ""), p2p=p2p)


def _docref(item):
    meta = item.get("result_meta", {}) or {}
    return DocRef(
        id=str(meta.get("token", "")),
        title=_HIGHLIGHT.sub("", item.get("title_highlighted", "")).strip(),
        url=meta.get("url", ""),
    )


def _message(item):
    content = item.get("content")
    text = content
    if item.get("msg_type") == "text" and isinstance(content, str):
        try:
            text = json.loads(content).get("text", content)
        except json.JSONDecodeError:
            pass
    elif not isinstance(content, str):
        text = str(content)
    sender = item.get("sender", {}) or {}
    return {
        "sender": sender.get("name", sender.get("id", "")),
        "time": str(item.get("create_time", "")),
        "text": text,
        "message_id": item.get("message_id", ""),
    }


async def _me():
    """Read the logged-in user's identity from local auth state (no network call)."""
    env = {**os.environ, **LARK_ENV}
    proc = await _spawn(["lark-cli", "auth", "status"], env)  # auth status rejects --format
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
    try:
        user = json.loads(stdout.decode(errors="replace")).get("identities", {}).get("user", {})
    except json.JSONDecodeError:
        user = {}
    if user.get("status") != "ready":
        return None
    return {"id": user.get("openId", ""), "name": user.get("userName", "")}


class LarkAdapter:
    """All MVP commands run with the user identity (p2p chats and docs require it)."""

    def __init__(self, identity="user", recorder=None):
        self.identity = identity
        self.recorder = recorder  # optional RunMetrics; timed here per subprocess call
        self._send_by_user_id = set()  # recipient ids that must use --user-id, not --chat-id

    async def _call(self, *args, dry_run=False):
        started = asyncio.get_running_loop().time()
        try:
            data = await run(list(args) + ["--as", self.identity], dry_run=dry_run)
        except Exception:
            if self.recorder:
                self.recorder.lark(" ".join(args[:3]),
                                   (asyncio.get_running_loop().time() - started) * 1000,
                                   dry_run=dry_run, ok=False)
            raise
        if self.recorder:
            self.recorder.lark(" ".join(args[:3]),
                               (asyncio.get_running_loop().time() - started) * 1000,
                               dry_run=dry_run)
        return data

    async def list_chats(self):
        """Enumerate joined chats. The agent decides when to call this — nothing is
        preloaded, so both lanes start from the same blank context (fair comparison)."""
        data = await self._call("im", "+chat-list", "--types", "group,p2p",
                                "--sort", "active_time", "--page-size", "20")
        me = await _me()
        refs = []
        for item in data.get("chats", []):
            ref = _chatref(item)
            if not ref.id:
                continue
            if me and item.get("p2p_target_id") == me["id"]:
                ref.name = me["name"]  # my own p2p chat: label it with my name
            refs.append(ref)
        if me and me["id"] and not any(
            item.get("p2p_target_id") == me["id"] for item in data.get("chats", [])
        ):
            # Feishu has no self-chat by default; add "send to me" via --user-id.
            refs.append(ChatRef(id=me["id"], name=me["name"], p2p=True))
            self._send_by_user_id.add(me["id"])
        return refs

    async def search_chats(self, query):
        data = await self._call("im", "+chat-search", "--query", query)
        items = data.get("chats", data.get("items", []))  # listing and search envelope variants
        return [_chatref(item) for item in items]

    async def open_chat(self, key):
        # default order=desc (newest first), page-size max 50
        data = await self._call("im", "+chat-messages-list", "--chat-id", key, "--page-size", "50")
        return [_message(item) for item in data.get("messages", [])]

    async def search_docs(self, query):
        data = await self._call("docs", "+search", "--query", query)
        return [_docref(item) for item in data.get("results", [])]

    async def open_doc(self, key):
        # scope=full, detail=simple by default
        return await self._call("docs", "+fetch", "--doc", key)

    async def create_doc(self, title, body, dry_run=True):
        return await self._call("docs", "+create", "--title", title, "--content", body,
                                "--parent-position", "my_library", dry_run=dry_run)

    async def write_doc(self, key, body, dry_run=True):
        # append, not overwrite: an agent edit must not be able to destroy existing content
        return await self._call("docs", "+update", "--doc", key, "--command", "append",
                                "--content", body, dry_run=dry_run)

    async def send_message(self, key, content, idempotency_key, dry_run=True,
                           via_user_id=None):
        if not idempotency_key:
            raise ValueError("send_message requires a durable idempotency key")
        if via_user_id is None:
            via_user_id = key in self._send_by_user_id
        target = ["--user-id", key] if via_user_id else ["--chat-id", key]
        return await self._call(
            "im", "+messages-send", *target, "--text", content,
            "--idempotency-key", idempotency_key, dry_run=dry_run)

    async def reply_message(self, key, content, idempotency_key, dry_run=True):
        if not idempotency_key:
            raise ValueError("reply_message requires a durable idempotency key")
        return await self._call(
            "im", "+messages-reply", "--message-id", key, "--text", content,
            "--idempotency-key", idempotency_key, dry_run=dry_run)
