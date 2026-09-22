#!/usr/bin/env python3
"""Export replay traces as a reviewable, content-redacted evidence bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
EVENT_TYPES = {
    "meta",
    "sandbox_ready",
    "attempt_started",
    "decision_ready",
    "intent",
    "dispatch_started",
    "observation",
    "step",
    "metrics",
    "final",
    "done",
}
SAFE_META_PARAMS = {
    "profile",
    "step_pause",
    "escalate_threshold",
    "ambiguity_gate",
    "answer_progress_floor",
    "max_steps",
    "max_writes",
    "sandbox_network",
    "min_confidence",
    "cache_policy",
}
SAFE_DECISION_FIELDS = {
    "operation",
    "operation_confidence",
    "confidence",
    "operation_probabilities",
    "phase",
    "phase_confidence",
    "phase_probabilities",
    "target_confidence",
    "latency_ms",
    "usage",
    "arbitrated",
    "turn",
    "ambiguity",
}


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _digest(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    data = value if isinstance(value, bytes) else _json_bytes(value)
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    helper = metrics.get("helper") or {}
    escalations = metrics.get("escalations") or {}
    return {
        "elapsed_ms": metrics.get("elapsed_ms"),
        "jev": metrics.get("jev") or {},
        "helper": {
            key: value
            for key, value in helper.items()
            if key != "detail"
        },
        "routing": metrics.get("routing") or {},
        "est_cost_usd": metrics.get("est_cost_usd"),
        "pricing": metrics.get("pricing") or {},
        "cache": {"policy": (metrics.get("cache") or {}).get("policy")},
        "lark": metrics.get("lark") or {},
        "denial_count": len(metrics.get("denials") or []),
        "escalations": {
            "count": escalations.get("count", 0),
            "upheld": escalations.get("upheld", 0),
            "overridden": escalations.get("overridden", 0),
        },
    }


def _safe_outcome(outcome: dict[str, Any] | None) -> dict[str, Any]:
    outcome = outcome or {}
    return {
        "status": outcome.get("status"),
        "effect_disposition": outcome.get("effect_disposition"),
        "action": _digest(outcome.get("action")),
        "error": _digest(outcome.get("error")),
    }


def _safe_decision(decision: dict[str, Any] | None) -> dict[str, Any]:
    decision = decision or {}
    safe = {key: decision.get(key) for key in SAFE_DECISION_FIELDS if key in decision}
    progress = decision.get("progress") or {}
    if progress:
        safe["progress"] = {
            key: progress.get(key)
            for key in ("score", "noul")
            if key in progress
        }
    safe["target"] = _digest(decision.get("target"))
    safe["target_candidate_count"] = len(decision.get("target_probabilities") or {})
    return safe


def _sanitize_event(event: dict[str, Any]) -> dict[str, Any]:
    kind = event.get("type")
    if kind not in EVENT_TYPES:
        raise ValueError(f"unsupported event type {kind!r}; fail closed rather than leak it")

    lane = event.get("lane")
    safe: dict[str, Any] = {"type": kind}
    if lane is not None:
        safe["lane"] = lane

    if kind == "meta":
        params = event.get("params") or {}
        safe["created_at"] = event.get("created_at")
        safe["params"] = {key: params.get(key) for key in SAFE_META_PARAMS if key in params}
        safe["goal"] = _digest(params.get("goal"))
        safe["recipient_count"] = len(params.get("allow_recipients") or [])
    elif kind == "sandbox_ready":
        safe.update({"image_id": event.get("image_id"), "lanes": event.get("lanes") or []})
    elif kind == "attempt_started":
        safe.update({"attempt_id": event.get("attempt_id"), "step": event.get("step")})
    elif kind == "decision_ready":
        safe.update({
            "attempt_id": event.get("attempt_id"),
            "operation": event.get("operation"),
            "needs_authoring": event.get("needs_authoring"),
        })
    elif kind == "intent":
        safe.update({
            "intent_id": event.get("intent_id"),
            "operation": event.get("operation"),
            "target": _digest(event.get("target")),
            "write": event.get("write"),
            "workspace_mutation": event.get("workspace_mutation"),
            "live": event.get("live"),
            "text_sha256": event.get("text_sha256"),
            "text_length": event.get("text_length"),
        })
    elif kind == "dispatch_started":
        safe.update({"intent_id": event.get("intent_id"), "operation": event.get("operation")})
    elif kind == "observation":
        safe.update({
            "observation_id": event.get("observation_id"),
            "attempt_id": event.get("attempt_id"),
            "intent_id": event.get("intent_id"),
            "operation": event.get("operation"),
            "target": _digest(event.get("target")),
            "disposition": event.get("disposition"),
            "dispatched": event.get("dispatched"),
            "outcome": _safe_outcome(event.get("outcome")),
            "fingerprints": event.get("fingerprints") or {},
            "budget": event.get("budget") or {},
            "provenance": {
                "decision_source": (event.get("provenance") or {}).get("decision_source"),
                "has_arbitration_reason": bool(
                    (event.get("provenance") or {}).get("arbitration_reason")
                ),
            },
        })
    elif kind == "step":
        step = event.get("step") or {}
        request = step.get("request") or {}
        final = step.get("final")
        safe["step"] = {
            "decision": _safe_decision(step.get("decision")),
            "model": request.get("model"),
            "intent_id": step.get("intent_id"),
            "outcome": _safe_outcome(step.get("outcome")),
            "steps": step.get("steps"),
            "writes": step.get("writes"),
            "denial_count": len(step.get("denials") or []),
            "denials": _digest(step.get("denials")),
            "was_denied": bool(step.get("denied")),
            "denied": _digest(step.get("denied")),
            "final": _digest(final),
            "model_call_count": len(step.get("model_calls") or []),
            "has_escalation": bool(step.get("escalation")),
        }
    elif kind == "metrics":
        safe["metrics"] = _safe_metrics(event.get("metrics") or {})
    elif kind == "final":
        safe["final"] = _digest(event.get("final"))
        safe["metrics"] = _safe_metrics(event.get("metrics") or {})
    return safe


def _empty_lane() -> dict[str, Any]:
    return {
        "wall_time_ms": 0,
        "jev_calls": 0,
        "llm_calls": 0,
        "jev_input_tokens": 0,
        "jev_output_tokens": 0,
        "llm_input_tokens": 0,
        "llm_output_tokens": 0,
        "cache_hit_tokens": 0,
        "cache_miss_tokens": 0,
        "estimated_cost_usd": 0.0,
        "runtime_steps": 0,
        "direct_jev_steps": 0,
        "answered_turns": 0,
        "escalations": 0,
        "escalations_upheld": 0,
        "escalations_overridden": 0,
        "decision_latencies_ms": [],
    }


def summarize(records: list[dict[str, Any]], session_id: str, run_ids: list[str]) -> dict[str, Any]:
    lanes: dict[str, dict[str, Any]] = {}
    for record in records:
        event = record["event"]
        lane = event.get("lane")
        if lane is None:
            continue
        lane_summary = lanes.setdefault(lane, _empty_lane())
        if event["type"] == "step":
            latency = (event.get("step") or {}).get("decision", {}).get("latency_ms")
            operation = (event.get("step") or {}).get("decision", {}).get("operation")
            if latency is not None and operation not in {None, "RUN_LIMIT", "RESTORE", "INVALID"}:
                lane_summary["decision_latencies_ms"].append(latency)
        elif event["type"] == "final":
            metrics = event["metrics"]
            jev = metrics["jev"]
            helper = metrics["helper"]
            routing = metrics["routing"]
            escalations = metrics["escalations"]
            lane_summary["wall_time_ms"] += metrics["elapsed_ms"]
            lane_summary["jev_calls"] += jev["calls"]
            lane_summary["llm_calls"] += helper["calls"]
            lane_summary["jev_input_tokens"] += jev["input_tokens"]
            lane_summary["jev_output_tokens"] += jev["output_tokens"]
            lane_summary["llm_input_tokens"] += helper["input_tokens"]
            lane_summary["llm_output_tokens"] += helper["output_tokens"]
            lane_summary["cache_hit_tokens"] += helper["cache_hit_tokens"]
            lane_summary["cache_miss_tokens"] += helper["cache_miss_tokens"]
            lane_summary["estimated_cost_usd"] += metrics["est_cost_usd"]
            lane_summary["runtime_steps"] += routing["decision_steps"]
            lane_summary["direct_jev_steps"] += routing["direct_jev_steps"]
            lane_summary["answered_turns"] += int(event.get("final") is not None)
            lane_summary["escalations"] += escalations["count"]
            lane_summary["escalations_upheld"] += escalations["upheld"]
            lane_summary["escalations_overridden"] += escalations["overridden"]

    for lane_summary in lanes.values():
        latencies = lane_summary.pop("decision_latencies_ms")
        lane_summary["decision_median_ms"] = round(statistics.median(latencies)) if latencies else None
        lane_summary["estimated_cost_usd"] = round(lane_summary["estimated_cost_usd"], 6)
        lane_summary["total_input_tokens"] = (
            lane_summary["jev_input_tokens"] + lane_summary["llm_input_tokens"]
        )
        lane_summary["total_output_tokens"] = (
            lane_summary["jev_output_tokens"] + lane_summary["llm_output_tokens"]
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "session_id": session_id,
        "run_ids": run_ids,
        "turns": len(run_ids),
        "lanes": lanes,
    }


def export(source_dir: Path, output_dir: Path, session_id: str, run_ids: list[str]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    sanitized: list[dict[str, Any]] = []
    sources = []
    models: set[str] = set()
    image_ids: set[str] = set()

    for turn, run_id in enumerate(run_ids, start=1):
        path = source_dir / f"{run_id}.jsonl"
        first_ts = None
        line_count = 0
        with path.open(encoding="utf-8") as handle:
            for line_count, line in enumerate(handle, start=1):
                record = json.loads(line)
                first_ts = record["ts"] if first_ts is None else first_ts
                event = record["event"]
                if event.get("type") == "sandbox_ready" and event.get("image_id"):
                    image_ids.add(event["image_id"])
                if event.get("type") == "step":
                    model = ((event.get("step") or {}).get("request") or {}).get("model")
                    if model:
                        models.add(model)
                    for call in (event.get("step") or {}).get("model_calls") or []:
                        if isinstance(call, dict) and call.get("model"):
                            models.add(call["model"])
                sanitized.append({
                    "run_id": run_id,
                    "turn": turn,
                    "seq": record["seq"],
                    "elapsed_ms": round((record["ts"] - first_ts) * 1000),
                    "event": _sanitize_event(event),
                })
        sources.append({
            "run_id": run_id,
            "file": path.name,
            "bytes": path.stat().st_size,
            "lines": line_count,
            "sha256": _file_digest(path),
        })

    trace_path = output_dir / "trace.jsonl"
    trace_path.write_text(
        "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in sanitized),
        encoding="utf-8",
    )
    summary = summarize(sanitized, session_id, run_ids)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "case": "fastapi-6turn-20260922",
        "session_id": session_id,
        "run_ids": run_ids,
        "source_files": sources,
        "trace": {
            "file": trace_path.name,
            "bytes": trace_path.stat().st_size,
            "lines": len(sanitized),
            "sha256": _file_digest(trace_path),
        },
        "models": sorted(models),
        "sandbox_image_ids": sorted(image_ids),
        "redaction": {
            "removed": [
                "prompts and model message content",
                "model response and final-answer content",
                "tool targets and tool output content",
                "recipient identities and cache scope identifiers",
                "denial and arbitration free text",
            ],
            "retained": [
                "event ordering and relative timing",
                "operations, dispositions, budgets, and content hashes",
                "model labels, token usage, routing, latency, and cost metrics",
                "intent, effect, and verification fingerprints",
            ],
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--run-id", action="append", required=True, dest="run_ids")
    args = parser.parse_args()
    export(args.source_dir, args.output_dir, args.session_id, args.run_ids)


if __name__ == "__main__":
    main()
