"""Offline tests for the lark-cli envelope contract (verified shapes, no network)."""

import asyncio
import json

import pytest

from jevloop.adapter import lark_cli
from jevloop.adapter.lark_cli import ConfirmationRequired, LarkCliError, run


class FakeProc:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = None
        self._returncode = returncode
        self._stdout = stdout.encode() if isinstance(stdout, str) else stdout
        self._stderr = stderr.encode() if isinstance(stderr, str) else stderr

    async def communicate(self):
        self.returncode = self._returncode
        return self._stdout, self._stderr


def _fake_spawn(monkeypatch, returncode, stdout="", stderr=""):
    proc = FakeProc(returncode, stdout, stderr)
    seen = {}

    async def spawn(argv, env):
        seen["argv"] = argv
        return proc

    monkeypatch.setattr(lark_cli, "_spawn", spawn)
    return seen


def test_success_envelope_returns_data(monkeypatch):
    payload = {"ok": True, "identity": "user", "data": {"items": [1, 2]}, "meta": {}}
    _fake_spawn(monkeypatch, 0, stdout=json.dumps(payload))
    assert asyncio.run(run(["im", "+chat-list"])) == {"items": [1, 2]}


def test_ok_false_on_exit_zero_is_an_error(monkeypatch):
    # The classic trap: envelope rendered on stdout with exit 0 but ok != true.
    payload = {"ok": False, "error": {"message": "scope missing"}}
    _fake_spawn(monkeypatch, 0, stdout=json.dumps(payload))
    with pytest.raises(LarkCliError):
        asyncio.run(run(["im", "+chat-list"]))


def test_exit_10_is_confirmation_gate(monkeypatch):
    _fake_spawn(monkeypatch, 10, stderr='{"ok":false,"error":{"type":"confirmation"}}')
    with pytest.raises(ConfirmationRequired):
        asyncio.run(run(["im", "+messages-send"]))


def test_nonzero_exit_error_envelope(monkeypatch):
    err = {"ok": False, "error": {"type": "api", "code": 230002, "message": "no permission"}}
    _fake_spawn(monkeypatch, 1, stderr=json.dumps(err))
    with pytest.raises(LarkCliError) as excinfo:
        asyncio.run(run(["docs", "+fetch", "--doc", "x"]))
    assert "no permission" in str(excinfo.value)


def test_dry_run_appends_flag(monkeypatch):
    seen = _fake_spawn(monkeypatch, 0, stdout='{"ok":true,"data":{}}')
    asyncio.run(run(["im", "+chat-list"], dry_run=True))
    assert seen["argv"] == ["lark-cli", "im", "+chat-list", "--format", "json", "--dry-run"]


def test_send_uses_caller_owned_stable_idempotency_key(monkeypatch):
    seen = _fake_spawn(monkeypatch, 0, stdout='{"ok":true,"data":{"message_id":"m1"}}')
    adapter = lark_cli.LarkAdapter()
    result = asyncio.run(adapter.send_message(
        "chat", "hello", "intent-key", dry_run=True, via_user_id=False))

    assert result == {"message_id": "m1"}
    argv = seen["argv"]
    assert argv[argv.index("--idempotency-key") + 1] == "intent-key"
    assert argv[-1] == "--dry-run"


def test_send_rejects_missing_idempotency_key_before_spawn(monkeypatch):
    async def forbidden_spawn(_argv, _env):
        raise AssertionError("must not spawn")

    monkeypatch.setattr(lark_cli, "_spawn", forbidden_spawn)
    with pytest.raises(ValueError, match="durable idempotency key"):
        asyncio.run(lark_cli.LarkAdapter().send_message(
            "chat", "hello", "", via_user_id=False))
