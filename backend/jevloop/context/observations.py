"""Bounded historical observations; references are shortcuts, never authority.

Only explicit provider result fields yield references. In particular, shell
output is evidence, not a file-list protocol. No user-goal interpretation lives
in this module. Live projection and replay use these same deterministic limits.
"""

import hashlib
import json
from copy import deepcopy

VIEW_CAP = 4
VIEW_CHARS = 3500
WINDOW_CHARS = 6000
FILE_REF_CAP = 20
DIRECTORY_REF_CAP = 8
EVIDENCE_CHARS = 1600
REFERENCE_OPERATIONS = frozenset({"LIST_FILES", "READ_FILE", "WRITE_FILE", "SEARCH_FILES", "GLOB_FILES"})


def _size(value):
    return len(json.dumps(value, ensure_ascii=False))


def _id(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()[:20]


def _path(value, kind):
    if not isinstance(value, str) or not value or len(value) > 512:
        return None
    if value.startswith(("/", "\\")) or "\\" in value or "\x00" in value:
        return None
    if ".." in value.split("/"):
        return None
    parts = [part for part in value.split("/") if part not in ("", ".")]
    normalized = "/".join(parts) or "."
    return normalized if kind == "directory" or normalized != "." else None


def normalize_observation(raw, *, operation, call_id, arguments=None, reference_kinds=None):
    """Normalize one provider observation before it enters the durable ledger."""
    if not isinstance(raw, dict):
        return None
    allowed = set(reference_kinds if reference_kinds is not None else (
        ("file", "directory") if operation in REFERENCE_OPERATIONS else ()))
    allowed.intersection_update({"file", "directory"})
    scope = str(raw.get("scope", "sandbox"))
    # Keep identity exact via a digest if display metadata is exceptionally long.
    scope_key = _id(scope)
    evidence = str(raw.get("evidence", ""))
    view = {
        "id": _id([operation, scope_key]),
        "kind": operation,
        "scope": scope[:512],
        "scope_key": scope_key,
        "source": {"operation": operation, "call_id": call_id},
        "trust": "untrusted_evidence",
        "reference_domain": "sandbox" if allowed else "none",
        "historical": True,
        "evidence": evidence[:EVIDENCE_CHARS],
        "references": [],
        "truncated": bool(raw.get("truncated")) or len(evidence) > EVIDENCE_CHARS or len(scope) > 512,
    }
    # Range/completeness metadata is descriptive, never an executable binding.
    for key in ("total", "returned", "has_more", "offset", "limit", "line_start", "line_end",
                "next_offset", "scan_truncated", "scanned_files", "scanned_bytes",
                "skipped_files", "references_truncated", "references_rejected",
                "line_truncated"):
        value = raw.get(key)
        if isinstance(value, (bool, int)) and abs(value) <= 10**12:
            view[key] = value
        elif key == "next_offset" and key in raw and value is None:
            view[key] = None
    if raw.get("unit") in {"lines", "entries", "matches"}:
        view["unit"] = raw["unit"]
    for key in ("pattern", "query", "note"):
        if isinstance(raw.get(key), str):
            view[key] = raw[key][:256]
            view["truncated"] |= len(raw[key]) > 256
    # The canonical invocation remains in the tool call; expose useful scope
    # without embedding arbitrarily large authored content a second time.
    context = {key: value for key, value in (arguments or {}).items()
               if key in {"path", "pattern", "glob", "offset", "limit", "recursive"}
               and isinstance(value, (str, int, bool))}
    if context:
        view["invocation"] = {key: value[:256] if isinstance(value, str) else value
                              for key, value in context.items()}
        view["truncated"] |= any(isinstance(v, str) and len(v) > 256 for v in context.values())
    seen = set()
    counts = {"file": 0, "directory": 0}
    refs = raw.get("references", []) if allowed else []
    if not isinstance(refs, list):
        refs = []
        view["truncated"] = True
    for ref in refs:
        if not isinstance(ref, dict) or ref.get("kind") not in allowed:
            view["truncated"] = True
            continue
        kind = ref["kind"]
        value = _path(ref.get("value"), kind)
        if value is None:
            view["truncated"] = True
            continue
        identity = (kind, value)
        if identity in seen:
            continue
        seen.add(identity)
        cap = FILE_REF_CAP if kind == "file" else DIRECTORY_REF_CAP
        if counts[kind] >= cap:
            view["truncated"] = True
            continue
        counts[kind] += 1
        label = str(ref.get("label") or value)
        view["references"].append({
            "id": _id(["sandbox", kind, value]), "kind": kind,
            "value": value, "label": label[:160],
        })
        view["truncated"] |= len(label) > 160
    # Preserve the envelope/provenance and whole reference values. Never cut a
    # path to make it fit: omit that shortcut and report a partial view instead.
    while _size(view) > VIEW_CHARS and len(view["evidence"]) > 256:
        view["evidence"] = view["evidence"][:max(256, len(view["evidence"]) // 2)]
        view["truncated"] = True
    while _size(view) > VIEW_CHARS and view["references"]:
        view["references"].pop()
        view["truncated"] = True
    if _size(view) > VIEW_CHARS:
        view.pop("invocation", None)
        view["truncated"] = True
    if _size(view) > VIEW_CHARS:
        raise ValueError("Observation provenance exceeds the bounded view budget")
    return view


def update_views(previous, incoming):
    """Replace a same-operation/scope view, then bound the combined snapshot."""
    views = [deepcopy(view) for view in previous if view.get("id") != incoming.get("id")]
    views.append(deepcopy(incoming))
    views = views[-VIEW_CAP:]
    counts = {"file": 0, "directory": 0}
    seen = set()
    for view in reversed(views):
        kept = []
        for ref in view.get("references", []):
            kind = ref["kind"]
            cap = FILE_REF_CAP if kind == "file" else DIRECTORY_REF_CAP
            if ref["id"] in seen or counts[kind] >= cap:
                view["truncated"] = True
                continue
            seen.add(ref["id"])
            counts[kind] += 1
            kept.append(ref)
        view["references"] = kept
    while _size(views) > WINDOW_CHARS and len(views) > 1:
        views.pop(0)
    return views


def reference_entries(views, kind):
    """Newest evidence first, with its context/provenance attached to each ref."""
    result = {}
    cap = FILE_REF_CAP if kind == "file" else DIRECTORY_REF_CAP
    for view in reversed(views):
        for ref in view.get("references", []):
            if ref["kind"] != kind or ref["value"] in result or len(result) >= cap:
                continue
            result[ref["value"]] = {
                "label": ref["label"],
                "meta": {"reference_id": ref["id"], "view_id": view["id"],
                         "source": deepcopy(view["source"]), "scope": view["scope"],
                         "historical": True, "truncated": view["truncated"]},
            }
    return result
