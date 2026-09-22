"""Session checkpoints are atomic and corrupted files fail closed."""

import pytest

from jevloop.context.transcript import Transcript
from jevloop.storage import sessions


def test_save_atomically_replaces_session_without_temp_leaks(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "DIR", tmp_path)
    first = Transcript("system", "one")
    sessions.save("s1", first)
    second = Transcript("system", "two")
    second.append_user("continued")
    sessions.save("s1", second)

    assert sessions.load("s1").dump() == second.dump()
    assert [path.name for path in tmp_path.iterdir()] == ["s1.json"]


def test_existing_corrupt_session_is_not_treated_as_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "DIR", tmp_path)
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "broken.json").write_text("{not-json")

    with pytest.raises(RuntimeError, match="is corrupt"):
        sessions.load("broken")
