"""Deterministic LLM view of the durable transcript, not a persistence format.

Only this module owns model-visible result fields and display budgets. Jev and
recovery read the original records independently. New internal envelope fields
are invisible by default; resource text and nested provider data remain data.
"""

import json
from copy import deepcopy

from jevloop.contracts.media import record_images

RESULT_CAP = 12000
_EXECUTION_FIELDS = (
    "status", "created", "exit", "dry_run", "reason", "receipt",
    "effect_disposition", "effect_proof", "partial", "read_files",
    "changed_files", "files_may_have_changed", "resolved", "resolution",
    "continuation", "container_running", "superseded", "result_omitted",
    "evidence_bounded",
)
_ERROR_FIELDS = ("code", "kind", "stage", "recoverability", "message")
_COVERAGE_FIELDS = (
    "scope", "historical", "truncated", "total", "returned", "has_more",
    "offset", "limit", "line_start", "line_end", "next_offset", "unit",
    "scan_truncated", "scanned_files", "scanned_bytes", "skipped_files",
    "references_truncated", "references_rejected", "line_truncated",
)
_FILE_FIELDS = (
    "target", "path", "status", "content", "reason", "returned_chars",
    "offset", "limit", "unit", "truncated", "line_truncated", "next_offset",
)
_IDENTITY_FIELDS = {"id", "path", "target", "url", "tool_call_id"}


def _fields(value, names):
    return {key: deepcopy(value[key]) for key in names if key in value}


def _records(value, names):
    if not isinstance(value, list):
        return []
    return [_fields(item, names) for item in value if isinstance(item, dict)]


def _observation(result):
    # New provider facts and old bounded views are distinct storage versions.
    for name in ("observation", "observation_view"):
        value = result.get(name)
        if isinstance(value, dict):
            return value
    return {}


def result_view(result, operation=None):
    """Project code-owned envelopes only; never filter inside resource data."""
    if not isinstance(result, dict):
        return {"effect_disposition": "UNKNOWN", "unparsed_result": result,
                "reason": "Stored result is not a structured execution record."}
    visible = _fields(result, _EXECUTION_FIELDS)
    if visible.get("effect_disposition") == "FAILED_RECOVERABLE":
        visible["effect_disposition"] = "UNKNOWN"
    elif visible.get("effect_disposition") == "DENIED":
        visible["effect_disposition"] = "NOT_APPLIED"
    if isinstance(result.get("error"), dict):
        visible["error"] = _fields(result["error"], _ERROR_FIELDS)
    if "data" in result:
        visible["data"] = deepcopy(result["data"])
    observation = _observation(result)
    if observation:
        visible["coverage"] = _fields(observation, _COVERAGE_FIELDS)
        # A stored observation is historical evidence, not a live resource probe.
        visible["coverage"]["historical"] = True
    if result.get("file_results"):
        visible["file_results"] = _records(result["file_results"], _FILE_FIELDS)
        visible.pop("read_files", None)  # paths are in each per-file result
    elif operation == "BASH":
        note = result.get("bash_note")
        if not isinstance(note, dict):
            if note is not None:
                visible["evidence_warning"] = "Malformed legacy shell note omitted."
            note = {}
        # New provider evidence is authoritative for this call. Only legacy
        # mixed records need a fullest-representation compatibility fallback.
        candidates = [observation.get("evidence"), note.get("output"),
                      result.get("output_excerpt"), result.get("output")]
        outputs = [value for value in candidates if isinstance(value, str)]
        raw = result.get("observation")
        if isinstance(raw, dict) and isinstance(raw.get("evidence"), str):
            visible["output"] = raw["evidence"]
        elif outputs:
            visible["output"] = max(outputs, key=len)
        if "exit" not in visible and isinstance(note, dict) and "exit" in note:
            visible["exit"] = note["exit"]
    elif operation == "SEARCH_FILES" and isinstance(result.get("matches"), list):
        visible["matches"] = _records(result["matches"], ("path", "line", "snippet"))
    elif observation and operation not in {"WRITE_FILE", "ANSWER", "DONE"}:
        visible["output"] = deepcopy(observation.get("evidence", ""))
    elif "content_excerpt" in result:
        visible["content"] = deepcopy(result["content_excerpt"])
    elif "output" in result:
        visible["output"] = deepcopy(result["output"])
    elif "output_excerpt" in result:
        visible["output"] = deepcopy(result["output_excerpt"])
    # Old structured business evidence remains usable, including actual tool IDs.
    if "chats" in result:
        visible["chats"] = _records(result["chats"], ("id", "name"))
    if "docs" in result:
        visible["docs"] = _records(result["docs"], ("id", "title", "url"))
    for name in ("messages", "doc_content_excerpt"):
        if name in result:
            visible[name] = deepcopy(result[name])
    if operation == "LIST_FILES" and not observation and "files" in result:
        visible["files"] = deepcopy(result["files"])
    # Unknown legacy operations may have only an action description. It is not
    # needed for canonical shell calls (which can contain the entire command).
    if ("action" in result and operation not in
            {"BASH", "READ_FILE", "LIST_FILES", "SEARCH_FILES", "WRITE_FILE", "ANSWER", "DONE"}):
        visible["action"] = deepcopy(result["action"])
    # A successful ANSWER's full body already resides in its committed call.
    if not visible:
        visible = {"result_omitted": True,
                   "reason": "No supported result fields; no success inferred."}
    return visible


def _bound(value, cap):
    if isinstance(value, str):
        return value if len(value) <= cap else value[:cap] + f"…[truncated {len(value) - cap} chars]"
    if isinstance(value, list):
        return [_bound(item, cap) for item in value]
    if isinstance(value, dict):
        return {key: deepcopy(item) if key in _IDENTITY_FIELDS else _bound(item, cap)
                for key, item in value.items()}
    return value


def _serialize(result):
    text = json.dumps(result, ensure_ascii=False, default=str)
    cap = 2000
    while len(text) > RESULT_CAP and cap >= 64:
        bounded = {**_bound(result, cap), "evidence_bounded": True}
        text = json.dumps(bounded, ensure_ascii=False, default=str)
        cap //= 2
    if len(text) > RESULT_CAP:
        # Structural overflow: retain effect/error semantics, never claim that
        # omitted data was empty or that an uncertain execution succeeded.
        keep = {key: value for key, value in _fields(result, _EXECUTION_FIELDS).items()
                if isinstance(value, (str, int, float, bool)) or value is None}
        if isinstance(result.get("error"), dict):
            keep["error"] = {key: value for key, value in result["error"].items()
                             if isinstance(value, (str, int, float, bool)) or value is None}
        keep.update(result_omitted=True, evidence_bounded=True)
        keep["omission_reason"] = "Projected result exceeded the LLM display budget."
        text = json.dumps(_bound(keep, 256), ensure_ascii=False, default=str)
    if len(text) > RESULT_CAP:
        # Even malformed legacy envelopes cannot bypass the hard display cap.
        # Do not drop error/effect semantics silently to squeeze under a budget.
        raise ValueError("LLM result budget is too small for the execution envelope")
    return text


def project_llm_messages(records):
    """Project in source order; no future record can change a previous prefix."""
    messages, calls = [], {}
    for record in records:
        message = _fields(record, ("role", "content", "tool_calls", "tool_call_id", "name"))
        for call in message.get("tool_calls") or []:
            calls[call.get("id")] = (call.get("function") or {}).get("name")
        if message.get("role") == "tool":
            content = message.get("content")
            try:
                result = json.loads(content) if isinstance(content, str) else content
            except (ValueError, TypeError):
                result = content
            message["content"] = _serialize(result_view(result, calls.get(message.get("tool_call_id"))))
        images = record_images(record)
        if images:
            message["images"] = images
        messages.append(message)
    return messages
