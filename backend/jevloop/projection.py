"""Context projection: the ledger is the single source of truth; every model's
context is RECOVERED from it by an engineering method.

- record_execution: the only ledger writer for finalized observations (verbatim
  data with typed error metadata; reuses an existing tool_call id when the step
  came from arbitration or a genuine LLM turn)
- rebuild_workspace: recovers the Jev view (structured candidates, summaries,
  recent attempt history incl. typed errors) from the ledger. The run-time
  workspace is this projection's cache — on session restore it is rebuilt from
  the ledger, never persisted separately.
"""

import hashlib
import json

from .state import PRIOR_ANSWER_EXCERPT_CHARS, ChatRef, DocRef, Workspace

_EXEC_KEYS = {
    "status", "action", "created", "exit", "output", "dry_run", "reason",
    "receipt", "effect_disposition", "effect_proof", "error", "partial",
    "read_files", "changed_files", "files_may_have_changed", "file_results",
    "resolved", "resolution", "continuation", "container_running",
}


def fingerprint(value) -> str:
    """Stable content hash over JSON-canonicalized data."""
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def intent_fingerprint(intent) -> str:
    """Hash of operation + canonical target + exact frozen authored arguments."""
    return fingerprint({
        "operation": (intent or {}).get("operation"),
        "target": (intent or {}).get("target"),
        "text": (intent or {}).get("text"),
    })


def observation_fingerprint(outcome) -> str:
    """Hash of the substantive result: normalized outcome minus model usage,
    latency and prose. An error contributes its stable code only, so changed
    diagnostic wording cannot reset duplicate/no-progress detection."""
    stable = {key: value for key, value in (outcome or {}).items() if key != "helper"}
    error = stable.get("error")
    if isinstance(error, dict):
        stable["error"] = {"code": error.get("code")}
    return fingerprint(stable)


def bounded_error(error):
    """The lossless-enough error envelope persisted and projected everywhere."""
    if not isinstance(error, dict):
        return None
    return {
        "code": error.get("code"),
        "kind": error.get("kind"),
        "stage": error.get("stage"),
        "recoverability": error.get("recoverability"),
        "message": str(error.get("message", ""))[:200],
    }


_TEXT_KEYS = ("answer", "query", "command", "content")


def _argument_text(arguments):
    return next((arguments[key] for key in _TEXT_KEYS
                 if isinstance(arguments.get(key), str)), None)


def _parse_arguments(call):
    try:
        arguments = json.loads(call.get("function", {}).get("arguments") or "{}")
    except (json.JSONDecodeError, AttributeError, TypeError):
        return {}
    return arguments if isinstance(arguments, dict) else {}


def _read_disposition(result):
    """Read-time interpretation of effect dispositions, including v1 ledgers:
    DENIED was a policy refusal (nothing applied); FAILED_RECOVERABLE from a
    possibly-mutating tool cannot prove the effect is absent, so it conservatively
    reads as UNKNOWN."""
    disposition = result.get("effect_disposition")
    if disposition == "DENIED":
        return "NOT_APPLIED"
    if disposition == "FAILED_RECOVERABLE":
        return "UNKNOWN"
    return disposition


def _enrich(operation, outcome, workspace):
    """Ledger result payload: typed outcome summary + everything needed to
    rebuild. Evidence-derived context is attached only when the operation
    completed (ready, or a delivered ANSWER): a refused READ/WRITE must not
    surface a stale previous note as if it were newly observed."""
    result = {k: v for k, v in outcome.items() if k in _EXEC_KEYS}
    if outcome.get("status") not in {"ready", "done"}:
        return result
    if operation in {"LIST_CHATS", "SEARCH_CHATS"}:
        result["chats"] = [
            {"id": key, "name": ref.name, "p2p": ref.p2p, "via_user_id": ref.via_user_id}
            for key, ref in list(workspace.chats.items())[:20]
        ]
    elif operation in {"OPEN_CHAT", "REPLY_MESSAGE"}:
        result["messages"] = workspace.messages[-12:]
    elif operation in {"SEARCH_DOCS"}:
        result["docs"] = [{"id": key, "title": ref.title, "url": ref.url}
                           for key, ref in list(workspace.docs.items())[:10]]
    elif operation == "OPEN_DOC":
        result["doc_content_excerpt"] = workspace.doc_content[:1000]
    elif operation in {"LIST_FILES", "WRITE_FILE"}:
        result["files"] = list(workspace.files)[:20]
    elif operation == "READ_FILE" and not result.get("file_results"):
        if workspace.notes and workspace.notes[-1].get("kind") == "file":
            result["content_excerpt"] = workspace.notes[-1]["text"][:1500]
    elif operation == "BASH":
        if workspace.notes and workspace.notes[-1].get("kind") == "bash":
            note = workspace.notes[-1]
            result["bash_note"] = {
                "command": str(note.get("command", ""))[:500],
                "output": str(note.get("output", ""))[:1500],
                "exit": note.get("exit"),
            }
    elif operation == "CREATE_DOC" and outcome.get("created"):
        for ref in workspace.docs.values():
            if ref.url == outcome["created"] or ref.id in str(outcome.get("created")):
                result["docs"] = [{"id": ref.id, "title": ref.title, "url": ref.url}]
                break
    if outcome.get("answer"):
        result["delivered_answer"] = outcome["answer"][:PRIOR_ANSWER_EXCERPT_CHARS]
    return result


def record_execution(transcript, operation, arguments, outcome, workspace,
                    call_id=None, observation=None):
    """Write one finalized observation into the ledger — executed or refused.
    `call_id` reuses an existing pending tool_call (arbitration / genuine LLM
    turn) instead of ghost-writing a new assistant turn — a dangling id would
    400 every later ledger request. The canonical `observation` metadata
    (ids, phase, fingerprints, dispatched flag, provenance) is persisted with
    the result so replay reconstructs history instead of fabricating it."""
    if call_id is None:
        if operation in (None, "BLOCKED"):
            return None
        call_id = transcript.append_action(operation, arguments or {})
    result = _enrich(operation, outcome, workspace)
    canonical = observation or {}
    for key in ("observation_id", "attempt_id", "phase", "dispatched"):
        if canonical.get(key) is not None:
            result[key] = canonical[key]
    fingerprints = canonical.get("fingerprints") or {}
    if fingerprints.get("intent") is not None:
        result["intent_fingerprint"] = fingerprints["intent"]
    if fingerprints.get("observation") is not None:
        result["observation_fingerprint"] = fingerprints["observation"]
    if canonical.get("provenance"):
        result["provenance"] = canonical["provenance"]
    transcript.append_result(call_id, result)
    return call_id


def rebuild_workspace(transcript) -> Workspace:
    """Recover the Jev view from the ledger. Best-effort by design: truncated
    or unparsable results are skipped — the ledger itself stays complete for
    the LLM, and the Jev view only needs candidates and recent context.
    Superseded sibling results are protocol bookkeeping, not attempts: they
    apply nothing and produce no history entries."""
    workspace = Workspace()
    messages = transcript.dump()
    for index, message in enumerate(messages):
        if message.get("role") != "assistant":
            if message.get("role") == "user" and isinstance(message.get("_runtime_note"), dict):
                workspace.append_history(_history_from_runtime_note(message["_runtime_note"]))
            continue
        for call in message.get("tool_calls") or []:
            name = call.get("function", {}).get("name", "")
            result = _result_after(messages, index, call.get("id"))
            if result is None:
                continue
            if isinstance(result, dict) and result.get("superseded"):
                continue
            _apply(workspace, name, result)
            arguments = _parse_arguments(call)
            if name == "ANSWER" and result.get("status") == "done" \
                    and not result.get("error"):
                # recover the FULL successful ANSWER argument (the assistant
                # call carries it verbatim); state() later renders one excerpt
                authored = arguments.get("answer")
                if isinstance(authored, str) and authored:
                    workspace.answer = authored
            workspace.append_history(_history_from_result(name, arguments, result))
    return workspace


def _result_after(messages, assistant_index, call_id):
    for message in messages[assistant_index + 1:]:
        if message.get("role") == "tool" and message.get("tool_call_id") == call_id:
            try:
                return json.loads(message.get("content") or "")
            except json.JSONDecodeError:
                return None
        if message.get("role") in {"assistant", "user"}:
            return None  # no response turn for this call
    return None


def _history_from_result(operation, arguments, result):
    """One history projector: prefer the canonical observation metadata the
    kernel persisted with the result; fall back to derived fields only for
    ledgers written before the canonical contract (v1)."""
    canonical_intent_fp = result.get("intent_fingerprint")
    canonical_observation_fp = result.get("observation_fingerprint")
    return {
        "phase": result.get("phase"),
        "operation": operation,
        "target": arguments.get("targets") or arguments.get("target"),
        "status": result.get("status"),
        "exit": result.get("exit"),
        "disposition": _read_disposition(result),
        "error": bounded_error(result.get("error")),
        "intent_fingerprint": (
            canonical_intent_fp if canonical_intent_fp is not None else fingerprint({
                "operation": operation,
                "target": arguments.get("targets") or arguments.get("target"),
                "text": _argument_text(arguments),
            })),
        "observation_fingerprint": canonical_observation_fp,
        "dispatched": (
            result["dispatched"] if isinstance(result.get("dispatched"), bool)
            else result.get("status") in {"ready", "failed"}),
        "observation_id": result.get("observation_id"),
        "attempt_id": result.get("attempt_id"),
        "read_files": result.get("read_files"),
        "changed_files": result.get("changed_files"),
        "files_may_have_changed": result.get("files_may_have_changed"),
        "summary": f"{operation}({result.get('action') or ''})",
    }


def _history_from_runtime_note(meta):
    error = bounded_error(meta.get("error")) or {}
    return {
        "phase": meta.get("phase"),
        "operation": meta.get("operation"),
        "target": meta.get("target"),
        "status": meta.get("status"),
        "disposition": meta.get("disposition"),
        "error": bounded_error(meta.get("error")),
        "dispatched": bool(meta.get("dispatched")),
        "observation_id": meta.get("observation_id"),
        "attempt_id": meta.get("attempt_id"),
        "summary": f"runtime:{meta.get('operation') or 'decision'} "
                   f"{error.get('code') or ''}".strip(),
    }


def _apply(workspace, operation, result):
    if not isinstance(result, dict):
        return
    if operation in {"LIST_CHATS", "SEARCH_CHATS"}:
        for item in result.get("chats") or []:
            if item.get("id"):
                ref = ChatRef(id=str(item["id"]), name=item.get("name", ""),
                              p2p=bool(item.get("p2p")),
                              via_user_id=bool(item.get("via_user_id")))
                workspace.chats[ref.id] = ref
                if ref.p2p:
                    workspace.recipients[ref.id] = ref.name
    elif operation in {"OPEN_CHAT", "REPLY_MESSAGE"}:
        found = result.get("messages")
        if isinstance(found, list):
            workspace.messages = found
    elif operation in {"SEARCH_DOCS", "CREATE_DOC"}:
        for item in result.get("docs") or []:
            if item.get("id"):
                workspace.docs[str(item["id"])] = DocRef(
                    id=str(item["id"]), title=item.get("title", ""),
                    url=item.get("url", ""))
    elif operation == "OPEN_DOC":
        excerpt = result.get("doc_content_excerpt")
        if excerpt:
            workspace.doc_content = excerpt
    elif operation in {"LIST_FILES", "WRITE_FILE"}:
        for rel in result.get("files") or []:
            workspace.files[str(rel)] = str(rel)
    elif operation == "READ_FILE":
        file_results = result.get("file_results") or []
        if file_results:
            for item in file_results:
                if item.get("status") == "ready":
                    workspace.notes.append({
                        "kind": "file",
                        "target": item.get("target"),
                        "text": f"[file: {item.get('target')}]\n"
                                f"{item.get('content', '')}",
                    })
        else:
            excerpt = result.get("content_excerpt")
            if excerpt:
                workspace.notes.append({"kind": "file", "text": excerpt})
    elif operation == "BASH":
        note = result.get("bash_note")
        if isinstance(note, dict):
            workspace.notes.append({
                "kind": "bash",
                "command": str(note.get("command", "")),
                "output": str(note.get("output", "")),
                "exit": note.get("exit"),
            })
        elif result.get("output_excerpt"):
            # Compatibility with ledgers written before structured Bash notes.
            workspace.notes.append({
                "kind": "bash",
                "command": "",
                "output": str(result["output_excerpt"]),
                "exit": result.get("exit"),
            })
    elif operation == "ANSWER":
        answer = result.get("delivered_answer")
        if answer:
            workspace.answer = answer
