"""Run persistence: append-only JSONL event logs on disk, one file per run.

The dashboard is replay-driven, so a persisted event log is a faithful recording:
loading it back gives byte-identical UI. Files live under artifacts/runs/ (gitignored).
"""

import json
import os
import re
import threading
import time
from pathlib import Path

from jevloop.paths import BACKEND_ROOT

DIR = Path(os.environ.get("JEVLOOP_RUNS_DIR")
           or BACKEND_ROOT / "artifacts" / "runs")
_id_re = re.compile(r"[A-Za-z0-9_-]{1,128}")


def validate_session_id(session_id):
    """Accept opaque local IDs, never paths or empty query values."""
    if not isinstance(session_id, str) or not _id_re.fullmatch(session_id):
        raise ValueError("invalid session_id")
    return session_id


def _path(run_id):
    return DIR / f"{run_id}.jsonl"


def append(run_id, seq, event):
    """Durably append one event; failure is fatal before another effect."""
    DIR.mkdir(parents=True, exist_ok=True)
    record = json.dumps(
        {"seq": seq, "ts": time.time(), "event": event},
        ensure_ascii=False,
        default=str,
    )
    with _path(run_id).open("a", encoding="utf-8") as handle:
        handle.write(record + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def load(run_id):
    """Return ordered events; tolerate only a torn final line."""
    file = _path(run_id)
    if not file.is_file():
        return None
    lines = file.read_text(encoding="utf-8").splitlines()
    entries = []
    expected_seq = 1
    for index, line in enumerate(lines):
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            if index == len(lines) - 1:
                break
            raise RuntimeError(
                f"run {run_id!r} has corrupt event at line {index + 1}") from error
        if record.get("seq") != expected_seq:
            raise RuntimeError(
                f"run {run_id!r} event sequence gap: expected {expected_seq}, "
                f"got {record.get('seq')!r}")
        entries.append((expected_seq, record["event"]))
        expected_seq += 1
    return entries


class Journal:
    """Small single-process event sink for CLI and offline runs."""

    def __init__(self, run_id):
        self.run_id = run_id
        self.seq = 0
        self._append_error = None
        self._lock = threading.Lock()

    def emit(self, event):
        with self._lock:
            if self._append_error is not None:
                raise self._append_error
            next_seq = self.seq + 1
            try:
                append(self.run_id, next_seq, event)
            except Exception as error:
                # A failed flush/fsync may already have written the record.
                # Never append again with an uncertain journal position.
                self._append_error = error
                raise
            self.seq = next_seq
            return next_seq


def list_runs(limit=50, *, session_id=None):
    """Newest-first summaries; an explicit session returns all of its runs.

    The global history remains bounded. Session filtering must precede that
    bound so replay never silently loses earlier turns to unrelated runs.
    """
    if session_id is not None:
        validate_session_id(session_id)
    runs = []
    if not DIR.is_dir():
        return runs
    files = sorted(DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if session_id is None:
        files = files[:limit]
    for file in files:
        meta, finished, has_error = None, False, False
        try:
            with file.open(encoding="utf-8") as handle:
                for line in handle:
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        break
                    event = record.get("event", {})
                    if event.get("type") == "meta":
                        meta = event
                    elif event.get("type") == "done":
                        finished = True
                    elif event.get("type") == "error":
                        has_error = True
        except OSError:
            continue
        if not meta:
            continue
        params = meta.get("params", {})
        stored_session_id = params.get("session_id") or file.stem
        if session_id is not None and stored_session_id != session_id:
            continue
        profile = params.get("profile")
        runs.append({
            "run_id": file.stem,
            "session_id": stored_session_id,
            "created_at": meta.get("created_at"),
            "goal": params.get("goal", ""),
            "compare": profile in {"paired_simulator", "paired_shadow"} or bool(params.get("compare")),
            "live": profile == "single_live" or bool(params.get("live")),
            "finished": finished,
            "error": has_error,
        })
    return runs
