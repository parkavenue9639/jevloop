"""Run persistence roundtrip: append, load, list."""

import json
import os
from types import SimpleNamespace

import pytest

from jevloop.storage import runstore


def test_runstore_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(runstore, "DIR", tmp_path)
    runstore.append("r1", 1, {"type": "meta", "params": {"goal": "测试", "compare": True}})
    runstore.append("r1", 2, {"type": "step", "lane": "jev", "step": {"decision": {"operation": "DONE"}}})
    runstore.append("r1", 3, {"type": "done"})

    entries = runstore.load("r1")
    assert [e["type"] for _s, e in entries] == ["meta", "step", "done"]
    assert [seq for seq, _e in entries] == [1, 2, 3]

    summaries = runstore.list_runs()
    assert len(summaries) == 1
    assert summaries[0]["run_id"] == "r1"
    assert summaries[0]["goal"] == "测试"
    assert summaries[0]["compare"] is True
    assert summaries[0]["finished"] is True

    assert runstore.load("missing") is None


def test_load_tolerates_only_torn_tail(tmp_path, monkeypatch):
    monkeypatch.setattr(runstore, "DIR", tmp_path)
    runstore.append("tail", 1, {"type": "meta"})
    with (tmp_path / "tail.jsonl").open("a") as handle:
        handle.write('{"seq": 2')
    assert runstore.load("tail") == [(1, {"type": "meta"})]

    records = [
        {"seq": 1, "ts": 0, "event": {"type": "meta"}},
        "not json",
        {"seq": 2, "ts": 0, "event": {"type": "done"}},
    ]
    (tmp_path / "middle.jsonl").write_text(
        "\n".join(item if isinstance(item, str) else json.dumps(item) for item in records))
    with pytest.raises(RuntimeError, match="corrupt event"):
        runstore.load("middle")


def test_journal_assigns_contiguous_sequences(tmp_path, monkeypatch):
    monkeypatch.setattr(runstore, "DIR", tmp_path)
    journal = runstore.Journal("cli")
    assert journal.emit({"type": "meta"}) == 1
    assert journal.emit({"type": "done"}) == 2
    assert [seq for seq, _event in runstore.load("cli")] == [1, 2]


@pytest.mark.parametrize("sink_kind", ["journal", "server"])
def test_failed_fsync_latches_sink_without_reusing_sequence(tmp_path, monkeypatch, sink_kind):
    from jevloop.apps.server import RunState

    monkeypatch.setattr(runstore, "DIR", tmp_path)
    sink = runstore.Journal("failure") if sink_kind == "journal" else RunState("failure", {})
    real_fsync = runstore.os.fsync

    def broken_fsync(_fd):
        raise OSError("fsync failed after write")

    monkeypatch.setattr(runstore.os, "fsync", broken_fsync)
    with pytest.raises(OSError, match="fsync failed"):
        sink.emit({"type": "jev_request"})
    monkeypatch.setattr(runstore.os, "fsync", real_fsync)
    with pytest.raises(OSError, match="fsync failed"):
        sink.emit({"type": "error"})
    assert sink.seq == 0
    assert runstore.load("failure") == [(1, {"type": "jev_request"})]
    if sink_kind == "server":
        assert sink.log == []  # Uncertain persistence must not fan out as success.


def test_session_runs_are_not_truncated_by_global_history_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(runstore, "DIR", tmp_path)
    for index in range(62):
        run_id = f"run-{index}"
        # Most target turns are older than the global history window.
        session_id = "target" if index < 11 or index == 61 else "other"
        runstore.append(run_id, 1, {
            "type": "meta", "created_at": f"2026-09-24T00:{index:02}:00",
            "params": {"session_id": session_id, "goal": run_id},
        })
        runstore.append(run_id, 2, {"type": "done"})
        os.utime(tmp_path / f"{run_id}.jsonl", (index + 1, index + 1))
    assert len(runstore.list_runs()) == 50
    assert "run-0" not in {run["run_id"] for run in runstore.list_runs()}
    assert [run["run_id"] for run in runstore.list_runs(session_id="target")] == [
        "run-61", *[f"run-{index}" for index in reversed(range(11))],
    ]
    assert len(runstore.list_runs(limit=1, session_id="target")) == 12


def test_session_runs_support_legacy_run_id_grouping(tmp_path, monkeypatch):
    monkeypatch.setattr(runstore, "DIR", tmp_path)
    runstore.append("legacy-run", 1, {"type": "meta", "params": {"goal": "old"}})
    runstore.append("legacy-run", 2, {"type": "done"})
    assert [run["run_id"] for run in runstore.list_runs(session_id="legacy-run")] == ["legacy-run"]
    assert runstore.list_runs(session_id="unknown") == []


@pytest.mark.parametrize("session_id", ["", "../target", "/tmp/target", "a b", "a.b", "a" * 129, 12])
def test_session_runs_reject_invalid_id(session_id):
    with pytest.raises(ValueError, match="invalid session_id"):
        runstore.list_runs(session_id=session_id)


def _get_runs(path):
    # Invoke the real GET route without opening sockets or starting a dashboard.
    from jevloop.apps.server import Handler

    handler = SimpleNamespace(path=path, _json=lambda payload, status=200: (status, payload))
    return Handler.do_GET(handler)


def test_session_runs_get_contract_and_default_compatibility(tmp_path, monkeypatch):
    monkeypatch.setattr(runstore, "DIR", tmp_path)
    runstore.append("r1", 1, {"type": "meta", "params": {"session_id": "session_1"}})
    runstore.append("r1", 2, {"type": "done"})
    status, payload = _get_runs("/api/runs?session_id=session_1")
    assert status == 200
    assert payload == {"session_id": "session_1", "complete": True,
                       "runs": runstore.list_runs(session_id="session_1")}
    assert _get_runs("/api/runs") == (200, {"runs": runstore.list_runs()})
    assert _get_runs("/api/runs?session_id=unknown") == (
        200, {"runs": [], "session_id": "unknown", "complete": True})


@pytest.mark.parametrize("query", [
    "session_id=", "session_id=..%2Ftarget", "session_id=%2Ftmp%2Ftarget",
    "session_id=a%00b", "session_id=a&session_id=b", "session_id=a&unexpected=1",
])
def test_session_runs_get_rejects_invalid_query_before_storage(query, monkeypatch):
    def unexpected_read(*args, **kwargs):
        pytest.fail("invalid query must not access run storage")

    monkeypatch.setattr(runstore, "list_runs", unexpected_read)
    status, payload = _get_runs(f"/api/runs?{query}")
    assert status == 400
    assert "error" in payload
