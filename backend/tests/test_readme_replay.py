"""The README replay preserves source identity and distinguishes effects from attempts."""

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/replay_readme_fastapi.py"
SPEC = importlib.util.spec_from_file_location("readme_replay", SCRIPT)
replay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(replay)


def fixture_source(tmp_path, monkeypatch):
    monkeypatch.setattr(replay, "ROOT", tmp_path)
    runs = tmp_path / "backend/artifacts/runs"
    evidence = tmp_path / "docs/evidence/fastapi-6turn-20260922"
    runs.mkdir(parents=True)
    evidence.mkdir(parents=True)
    files = []
    for index in range(6):
        raw = json.dumps({"event": {"type": "meta", "params": {
            "goal": f"goal {index}", "profile": "paired_shadow", "step_pause": False,
            "max_steps": 0, "cache_scopes": {"private": "do not copy"}}}}).encode()
        filename = f"{index}.jsonl"
        (runs / filename).write_bytes(raw)
        files.append({"file": filename, "run_id": str(index),
                      "sha256": hashlib.sha256(raw).hexdigest()})
    (evidence / "manifest.json").write_text(json.dumps({"source_files": files}))
    return runs


def test_source_preserves_exact_goals_and_unlimited_step_budget(tmp_path, monkeypatch):
    fixture_source(tmp_path, monkeypatch)
    turns = replay.source_turns()
    assert [turn["params"]["goal"] for turn in turns] == [f"goal {i}" for i in range(6)]
    assert all(turn["params"]["max_steps"] == 0 for turn in turns)
    assert all("cache_scopes" not in turn["params"] for turn in turns)


def test_changed_source_fails_before_any_replay(tmp_path, monkeypatch):
    runs = fixture_source(tmp_path, monkeypatch)
    (runs / "0.jsonl").write_text("changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        replay.source_turns()


def test_failed_attempt_is_not_a_successful_direct_tool():
    steps = [{"decision": {"operation": "READ_FILE"},
              "model_calls": [{"kind": "jev_decision"}],
              "outcome": {"effect_disposition": effect}}
             for effect in ("NOT_APPLIED", "SUCCEEDED")]
    events = [{"type": "step", "lane": "jev", "step": step} for step in steps]
    events.extend([{"type": "step", "lane": "jev", "step": {"final": "completed"}},
                   {"type": "final", "lane": "jev", "final": {"answer": "done"}}])
    record = replay.lane_record(SimpleNamespace(log=list(enumerate(events))), "jev", "goal", False)
    assert record["successful_direct_tools"] == 1
    assert record["hard_failures"] == 0


def test_stopped_turn_with_no_answer_is_not_completed():
    record = replay.lane_record(SimpleNamespace(log=[]), "jev", "goal", True)
    assert record["final"] == "timeout"
    assert record["hard_failures"] == 1
