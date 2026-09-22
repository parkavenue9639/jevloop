"""Session store: multi-turn conversations survive across runs and restarts.

A session IS its ledger — the single source of truth. Continuing a session
appends the new goal to the existing ledger (the LLM's continuity and prompt
cache carry across turns); the Jev workspace is recovered from the ledger by
projection.rebuild_workspace, never persisted separately.
"""

import json
import os
import time
import uuid
from pathlib import Path

from jevloop.context.transcript import Transcript
from jevloop.paths import BACKEND_ROOT

DIR = Path(os.environ.get("JEVLOOP_SESSIONS_DIR")
           or BACKEND_ROOT / "artifacts" / "sessions")


def new_session_id() -> str:
    return uuid.uuid4().hex[:12]


def path_for(session_id: str) -> Path:
    return DIR / f"{session_id}.json"


def save(session_id: str, transcript: Transcript):
    """Atomically persist the ledger or raise before another effect is allowed."""
    DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "session_id": session_id,
        "saved_at": time.time(),
        "messages": transcript.dump(),
    }
    destination = path_for(session_id)
    temporary = DIR / f".{session_id}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, default=str)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        directory_fd = os.open(DIR, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def load(session_id: str):
    """Return a saved Transcript; corrupted existing sessions fail closed."""
    file = path_for(session_id)
    if not file.is_file():
        return None
    try:
        payload = json.loads(file.read_text(encoding="utf-8"))
        messages = payload["messages"]
        if not isinstance(messages, list):
            raise TypeError("messages is not a list")
        return Transcript.from_messages(messages)
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"session {session_id!r} is corrupt: {error}") from error
