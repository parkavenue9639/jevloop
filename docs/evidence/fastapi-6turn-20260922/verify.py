#!/usr/bin/env python3
"""Verify this evidence bundle without access to the original private traces."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
FORBIDDEN_KEYS = {
    "messages",
    "questions",
    "tools",
    "tool_choice",
    "cache_scopes",
    "allow_recipients",
    "target_label",
    "target_probabilities",
    "target_probabilities_labeled",
}
FORBIDDEN_TEXT = ("/Users/", "authorization", "bearer ", "api_key", "陆冲")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_forbidden_keys(value: Any, path: str = "$") -> list[str]:
    violations = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in FORBIDDEN_KEYS:
                violations.append(f"{path}.{key}")
            violations.extend(find_forbidden_keys(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            violations.extend(find_forbidden_keys(child, f"{path}[{index}]"))
    return violations


def find_long_strings(value: Any, path: str = "$") -> list[str]:
    violations = []
    if isinstance(value, dict):
        for key, child in value.items():
            violations.extend(find_long_strings(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            violations.extend(find_long_strings(child, f"{path}[{index}]"))
    elif isinstance(value, str) and len(value) > 80:
        violations.append(f"{path} ({len(value)} chars)")
    return violations


def empty_lane() -> dict[str, Any]:
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


def summarize(records: list[dict[str, Any]], manifest: dict[str, Any]) -> dict[str, Any]:
    lanes: dict[str, dict[str, Any]] = {}
    expected_seq: dict[str, int] = {}
    for record in records:
        run_id = record["run_id"]
        expected = expected_seq.get(run_id, 1)
        if record["seq"] != expected:
            raise AssertionError(f"{run_id}: expected seq {expected}, got {record['seq']}")
        expected_seq[run_id] = expected + 1

        event = record["event"]
        lane = event.get("lane")
        if lane is None:
            continue
        lane_summary = lanes.setdefault(lane, empty_lane())
        if event["type"] == "step":
            decision = (event.get("step") or {}).get("decision") or {}
            latency = decision.get("latency_ms")
            if latency is not None and decision.get("operation") not in {
                None,
                "RUN_LIMIT",
                "RESTORE",
                "INVALID",
            }:
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
        "schema_version": manifest["schema_version"],
        "session_id": manifest["session_id"],
        "run_ids": manifest["run_ids"],
        "turns": len(manifest["run_ids"]),
        "lanes": lanes,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-dir",
        type=Path,
        help="optional original runs directory; verifies source SHA256 when supplied",
    )
    args = parser.parse_args()

    manifest = json.loads((HERE / "manifest.json").read_text())
    expected_summary = json.loads((HERE / "summary.json").read_text())
    trace_path = HERE / manifest["trace"]["file"]
    trace_bytes = trace_path.read_bytes()
    assert len(trace_bytes) == manifest["trace"]["bytes"]
    assert file_sha256(trace_path) == manifest["trace"]["sha256"]

    text = trace_bytes.decode()
    lowered = text.lower()
    leaked_markers = [marker for marker in FORBIDDEN_TEXT if marker.lower() in lowered]
    assert not leaked_markers, f"forbidden text markers: {leaked_markers}"

    records = [json.loads(line) for line in text.splitlines()]
    assert len(records) == manifest["trace"]["lines"]
    violations = find_forbidden_keys(records)
    assert not violations, f"forbidden fields: {violations[:10]}"
    long_strings = find_long_strings(records)
    assert not long_strings, f"unredacted long strings: {long_strings[:10]}"
    assert summarize(records, manifest) == expected_summary

    if args.source_dir:
        for source in manifest["source_files"]:
            source_path = args.source_dir / source["file"]
            assert source_path.stat().st_size == source["bytes"]
            assert file_sha256(source_path) == source["sha256"]

    lanes = expected_summary["lanes"]
    print(
        "verified:",
        f"{len(records)} sanitized events,",
        f"JevLoop {lanes['jev']['wall_time_ms']}ms/${lanes['jev']['estimated_cost_usd']:.6f},",
        f"baseline {lanes['baseline']['wall_time_ms']}ms/${lanes['baseline']['estimated_cost_usd']:.6f}",
    )


if __name__ == "__main__":
    main()
