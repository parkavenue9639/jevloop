import asyncio
import importlib.util
import json
from pathlib import Path

from jevloop.bench import Turn

SPEC = importlib.util.spec_from_file_location(
    "family_runner", Path(__file__).resolve().parents[1] / "scripts/run_fastapi_families.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def test_checker_requires_exit_zero_even_when_marker_is_printed(monkeypatch):
    async def no_files(*args):
        return []

    monkeypatch.setattr(runner, "ORIGINAL_CHECK_FILES", no_files)

    class Container:
        async def run_bash(self, command):
            return {"exit": 1, "output": "PASS"}

    checks = asyncio.run(runner.strict_checks(
        Turn(goal="test", bash=({"command": "test", "contains": "PASS"},)), Container()))
    assert checks[0]["ok"] is False


def test_checker_error_is_retained_as_failure(monkeypatch):
    async def unavailable(*args):
        raise TimeoutError("probe timed out")

    monkeypatch.setattr(runner, "ORIGINAL_CHECK_FILES", unavailable)
    checks = asyncio.run(runner.strict_checks(Turn(goal="test"), None))
    assert checks == [{"name": "file checks available", "ok": False,
                       "detail": "TimeoutError: probe timed out"}]


def test_exact_candidate_coverage_uses_executed_arguments_with_defaults():
    step = {"decision": {"operation": "READ_FILE"}, "model_calls": [
        {"kind": "jev_decision", "request": {"questions": {
            "target__inspect__read_file": {"criteria": {
                "a.py": {"arguments": json.dumps({"path": "a.py", "offset": 0, "limit": 200})}}}}}},
        {"kind": "parameter_authoring", "response": {"tool_calls": [{"function": {
            "name": "READ_FILE", "arguments": '{"path":"a.py"}'}}]}}]}
    assert runner.matching_candidates(step) == ["a.py"]
    step["model_calls"][-1]["response"]["tool_calls"][0]["function"]["arguments"] = (
        '{"path":"a.py","offset":200}')
    assert runner.matching_candidates(step) == []


def test_partial_write_binding_is_not_full_coverage():
    step = {"decision": {"operation": "WRITE_FILE", "arguments": {"path": "a.py", "content": "new"}},
            "model_calls": [{"kind": "jev_decision", "request": {"questions": {
                "target__act__write_file": {"criteria": {"a.py": {
                    "arguments": '{"path":"a.py"}'}}}}}}]}
    assert runner.matching_candidates(step) == []


def test_journal_analysis_excludes_failed_coverage_and_retains_turn_errors(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    events = [
        {"type": "meta", "params": {"scenario": "test", "turn": 1}},
        {"type": "step", "lane": "jev", "step": {
            "decision": {"operation": "LIST_FILES", "binding_mode": "defaults",
                         "bound_arguments": {"path": ".", "offset": 0, "limit": 100}},
            "model_calls": [{"kind": "jev_decision", "latency_ms": 10, "request": {
                "questions": {"target__inspect__list_files": {"criteria": {
                    "DEFAULT_ARGUMENTS": {"arguments": '{"path":".","offset":0,"limit":100}'}}}}}}],
            "outcome": {"effect_disposition": "NOT_APPLIED", "error": {"code": "REFUSED"}}}},
        {"type": "error", "lane": "jev", "message": "turn timeout"}]
    (runs / "test.jsonl").write_text("\n".join(json.dumps({"event": event}) for event in events))
    analysis = runner.analyze_journals(tmp_path)["test"]["jev"]
    assert analysis["successful_direct"] == []
    assert analysis["exact_candidate_coverage"] == []
    assert len(analysis["errors"]) == 2


def test_timeout_retains_last_recorded_cost_as_partial_accounting(tmp_path):
    runs = tmp_path / "runs"
    runs.mkdir()
    metrics = {"routing": {"decision_steps": 2}, "jev": {"calls": 2, "est_cost_usd": .01}}
    events = [{"type": "meta", "params": {"scenario": "test", "turn": 1}},
              {"type": "metrics", "lane": "jev", "metrics": metrics}]
    (runs / "test.jsonl").write_text("\n".join(json.dumps({"event": event}) for event in events))
    records = {lane: {"turns": [{"final": "timeout", "metrics": None,
                                "elapsed_ms": 600000, "hard_failures": 1}], "totals": {}}
               for lane in ("jev", "baseline")}
    report = {"scenarios": [{"id": "test", "favor": "either", "status": "failed", "lanes": records}]}
    runner.restore_partial_accounting(report, tmp_path)
    assert records["jev"]["totals"]["steps"] == 2
    assert records["jev"]["totals"]["cost_usd"] == .01
    assert "in-flight cost unknown" in records["jev"]["turns"][0]["accounting_note"]
    assert report["aggregate"]["verification"]["turns_passed"] == 0
