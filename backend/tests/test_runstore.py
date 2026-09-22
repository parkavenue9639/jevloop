"""Run persistence roundtrip: append, load, list."""

import json

import pytest

from jevloop import runstore


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