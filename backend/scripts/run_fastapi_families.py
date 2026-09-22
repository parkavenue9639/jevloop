"""Run frozen FastAPI task families without changing the agent runtime.

Uses the existing sequential paired bench lifecycle. Strict post-turn probes are
not agent observations and require both a successful exit and their marker.
Full journals remain the accounting source, including unsuccessful attempts.
"""

import argparse
import asyncio
import hashlib
import importlib.util
import json
import math
import os
import statistics
import uuid
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path

from jevloop import bench
from jevloop.arguments import arguments_complete, validate_arguments
from jevloop.cli import load_env_file
from jevloop.guardrails import InvalidProposal
from jevloop.tools.sandbox import SPECS, workspace_volume_name

ROOT = Path(__file__).resolve().parents[1]
SUITE_SOURCE = ROOT / "benchmarks/fastapi_families.py"
ORIGINAL_CHECK_FILES = bench._check_files


def load_payload():
    spec = importlib.util.spec_from_file_location("fastapi_families", SUITE_SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.suite_payload()


async def strict_checks(turn, container):
    """A failed checker is a recorded failure, never a silent pass or rerun."""
    try:
        checks = await asyncio.wait_for(
            ORIGINAL_CHECK_FILES(replace(turn, bash=()), container), 60)
    except Exception as error:  # noqa: BLE001 - preserve a failed check and continue other turns
        checks = [{"name": "file checks available", "ok": False,
                   "detail": f"{type(error).__name__}: {error}"}]
    for index, want in enumerate(turn.bash, 1):
        try:
            result = await asyncio.wait_for(container.run_bash(want["command"]), 60)
            output = result.get("output") or ""
            checks.append({"name": f"strict behavioral probe {index}",
                           "ok": result.get("exit") == 0 and want["contains"] in output,
                           "detail": {"exit": result.get("exit"), "output": output[-4000:]}})
        except Exception as error:  # noqa: BLE001 - a checker failure must never become a pass
            checks.append({"name": f"strict behavioral probe {index}", "ok": False,
                           "detail": f"{type(error).__name__}: {error}"})
    return checks


def actual_arguments(step):
    decision = step.get("decision") or {}
    operation = decision.get("operation")
    if isinstance(decision.get("arguments"), dict):
        return decision["arguments"]
    for call in reversed(step.get("model_calls") or []):
        if call.get("kind") == "jev_decision":
            continue
        for invocation in (call.get("response") or {}).get("tool_calls") or []:
            function = invocation.get("function") or {}
            if function.get("name") == operation:
                try:
                    value = json.loads(function["arguments"])
                    if isinstance(value, dict):
                        return value
                except (ValueError, KeyError, TypeError):
                    pass
    if decision.get("binding_mode") in {"observed", "defaults"}:
        return decision.get("bound_arguments")
    return None


def matching_candidates(step):
    """Exact post-hoc coverage of normalized proposal args, NOT an optimality oracle.

    Only emitted concrete scalar criteria count. No goal matching, inferred
    candidates, cross-products or synthetic multi-target combinations.
    """
    operation = (step.get("decision") or {}).get("operation")
    spec = next((spec for spec in SPECS if spec.name == operation), None)
    actual = actual_arguments(step)
    if spec is None or not isinstance(actual, dict) or not arguments_complete(spec, actual):
        return []
    try:
        actual = validate_arguments(spec, actual)
    except (InvalidProposal, ValueError):
        return []
    matches = set()
    for call in step.get("model_calls") or []:
        if call.get("kind") != "jev_decision":
            continue
        for head, question in (call.get("request") or {}).get("questions", {}).items():
            if not head.startswith("target__") or not head.endswith("__" + operation.lower()):
                continue
            for key, criterion in question.get("criteria", {}).items():
                if not isinstance(criterion, dict) or "arguments" not in criterion:
                    continue
                try:
                    args = json.loads(criterion["arguments"])
                except (ValueError, TypeError):
                    continue
                if arguments_complete(spec, args):
                    try:
                        if validate_arguments(spec, args) == actual:
                            matches.add(key)
                    except (InvalidProposal, ValueError):
                        continue
    return sorted(matches)


def analyze_journals(out_dir):
    grouped = {}
    for file in sorted((out_dir / "runs").glob("*.jsonl")):
        events = [json.loads(line)["event"] for line in file.read_text().splitlines()]
        meta = next(event["params"] for event in events if event["type"] == "meta")
        for lane in bench.LANES:
            key = (meta["scenario"], lane)
            group = grouped.setdefault(key, {"operations": Counter(), "calls": Counter(),
                "latencies": defaultdict(list), "successful_direct": [],
                "exact_candidate_coverage": [], "errors": [], "arbitration_reasons": Counter(),
                "phase_only_arbitrations": 0, "process_pattern_commands": [],
                "llm_input_tokens": 0, "llm_output_tokens": 0})
            for event in events:
                if event.get("type") == "error" and event.get("lane") == lane:
                    group["errors"].append({"run": file.stem, "turn": meta["turn"],
                                            "stage": "turn", "error": event.get("message")})
                if event.get("type") != "step" or event.get("lane") != lane:
                    continue
                step = event["step"]
                calls = step.get("model_calls") or []
                decision = step.get("decision") or {}
                operation = decision.get("operation")
                reference = {"run": file.stem, "turn": meta["turn"], "operation": operation}
                if calls:
                    group["operations"][operation] += 1
                outcome = step.get("outcome") or {}
                if outcome.get("error"):
                    group["errors"].append({**reference, "error": outcome["error"]})
                for call in calls:
                    kind = call.get("kind", "unknown")
                    group["calls"][kind] += 1
                    group["latencies"][kind].append(call.get("latency_ms") or 0)
                    if kind != "jev_decision":
                        usage = call.get("usage") or {}
                        group["llm_input_tokens"] += usage.get("prompt_tokens", usage.get("input_tokens", 0))
                        group["llm_output_tokens"] += usage.get("completion_tokens", usage.get("output_tokens", 0))
                direct = (calls and all(call.get("kind") == "jev_decision" for call in calls)
                          and operation not in {None, "ANSWER", "DONE"}
                          and outcome.get("effect_disposition") == "SUCCEEDED")
                if direct:
                    group["successful_direct"].append({**reference,
                        "binding_mode": decision.get("binding_mode"), "arguments": actual_arguments(step)})
                if lane == "jev" and outcome.get("effect_disposition") == "SUCCEEDED":
                    matches = matching_candidates(step)
                    if matches:
                        group["exact_candidate_coverage"].append({**reference,
                            "matches": matches, "arguments": actual_arguments(step),
                            "successful_direct": bool(direct)})
                escalation = step.get("escalation") or {}
                if escalation:
                    group["arbitration_reasons"][escalation.get("reason")] += 1
                    original = next((c.get("response") or {} for c in calls
                                     if c.get("kind") == "jev_decision"), {})
                    if (escalation.get("reason") == "low_confidence"
                            and original.get("phase_confidence", 1) < .5
                            and (original.get("operation_confidence")
                                 if original.get("operation_confidence") is not None else 1) >= .5):
                        group["phase_only_arbitrations"] += 1
                args = actual_arguments(step) or {}
                command = args.get("command", "") if operation == "BASH" else ""
                if any(marker in command for marker in ("pkill", "killall", "pgrep", "/proc/*/cmdline")):
                    group["process_pattern_commands"].append({**reference, "command": command})
    result = {}
    for (scenario, lane), group in grouped.items():
        latency = {}
        for kind, values in group.pop("latencies").items():
            latency[kind] = {"calls": len(values), "sum_ms": sum(values),
                             "median_ms": statistics.median(values),
                             "p95_ms": sorted(values)[math.ceil(.95 * len(values)) - 1],
                             "max_ms": max(values)}
        result.setdefault(scenario, {})[lane] = {**group, "latency": latency}
    return result


def restore_partial_accounting(report, out_dir):
    """Keep recorded costs when the native harness returns a timeout/error record.

    In-flight unrecorded requests remain unknown; partial costs are lower bounds.
    Do not substitute these values for an unqualified successful-run comparison.
    """
    latest = {}
    for file in sorted((out_dir / "runs").glob("*.jsonl")):
        events = [json.loads(line)["event"] for line in file.read_text().splitlines()]
        meta = next(event["params"] for event in events if event["type"] == "meta")
        for event in events:
            if event.get("type") == "metrics":
                latest[(meta["scenario"], event.get("lane"), meta["turn"])] = event["metrics"]
    for scenario in report["scenarios"]:
        for lane in bench.LANES:
            turns = scenario["lanes"][lane]["turns"]
            for index, record in enumerate(turns, 1):
                if record.get("metrics") is None:
                    record["metrics"] = latest.get((scenario["id"], lane, index), {})
                    record["accounting_note"] = "Partial journal accounting; unrecorded in-flight cost unknown."
            scenario["lanes"][lane]["totals"] = bench._lane_totals(turns)
        scenario["comparison"] = bench._compare({
            lane: scenario["lanes"][lane]["totals"] for lane in bench.LANES})
    report["aggregate"] = bench._aggregate(report["scenarios"])


async def run(out_dir):
    out_dir.mkdir(parents=True, exist_ok=False)
    payload = load_payload()
    suite_path = out_dir / "frozen-suite.json"
    suite_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    scenarios = bench.load_scenarios(suite_path)
    runtime_before = bench.runtime_digest()
    cleanup = []
    stamp = uuid.uuid4().hex[:12]
    owned_volumes = {workspace_volume_name(f"bench-{scenario.id}-{stamp}--{lane}")
                     for scenario in scenarios for lane in bench.LANES}

    async def remove_owned_volume(name):
        if name not in owned_volumes:
            raise ValueError("Cleanup target is outside this run")
        process = await asyncio.create_subprocess_exec(
            "docker", "volume", "rm", name,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await process.communicate()
        cleanup.append({"volume": name, "exit": process.returncode,
                        "stdout": stdout.decode()[-1000:], "stderr": stderr.decode()[-1000:]})

    context = bench.BenchContext(sessions_dir=out_dir / "sessions", runs_dir=out_dir / "runs",
        stamp=stamp, escalate_threshold=.5, ambiguity_gate=.4,
        answer_progress_floor=None, min_confidence=.6, turn_timeout=600,
        remove_volume=remove_owned_volume)
    bench._check_files = strict_checks
    try:
        report = await bench.run_suite(scenarios, context, suite_path=suite_path)
    finally:
        bench._check_files = ORIGINAL_CHECK_FILES
    restore_partial_accounting(report, out_dir)
    report["protocol"] = {
        "lane_schedule": "sequential per turn, Jev first; separate persistent containers per scenario",
        "checks": "post-turn, outside agent context/time; exit zero AND success marker required",
        "runtime_unchanged": runtime_before == bench.runtime_digest(),
        "source_sha256": hashlib.sha256(SUITE_SOURCE.read_bytes()).hexdigest(),
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "stamp": context.stamp,
        "limits": "20 agent steps and 600 seconds per lane-turn, no retries of failed turns",
        "coverage": "successful effects only; exact scalar candidates matched to provider-normalized proposal args, "
                    "not a frozen-ledger audit or independent judgment of necessity/optimality",
    }
    report["cleanup"] = cleanup
    report["trace_analysis"] = analyze_journals(out_dir)
    bench.write_report(report, out_dir)
    print(json.dumps({"out": str(out_dir), "verification": report["aggregate"]["verification"],
                      "runtime": report["suite"]["runtime_digest"]}), flush=True)
    return (all(scenario["status"] == "passed" for scenario in report["scenarios"])
            and len(cleanup) == len(owned_volumes) and all(item["exit"] == 0 for item in cleanup))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    options = parser.parse_args()
    load_env_file()
    if not all(os.environ.get(key) for key in ("TYPESAFE_API_KEY", "DEEPSEEK_API_KEY")):
        raise SystemExit("Missing model credentials")
    raise SystemExit(0 if asyncio.run(run(options.out)) else 1)
