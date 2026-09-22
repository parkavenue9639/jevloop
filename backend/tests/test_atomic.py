"""Sandbox atomic tools: confinement contracts, runtime injection, cleanup.

Docker is never invoked here: the host-side contracts (argv construction,
validation before invocation, idempotent close) are pinned against injected
fakes; live-container behavior is Main's smoke test.
"""

import asyncio
from itertools import pairwise

import pytest

from jevloop.state import Workspace
from jevloop.tools import sandbox
from jevloop.tools.base import ToolContext, write_actions
from jevloop.tools.sandbox import (
    DockerSandboxContainer,
    DockerSandboxImage,
    SandboxTools,
    validate_relpath,
    workspace_volume_name,
)


class FakeRuntime:
    """In-memory stand-in for a started container: same async surface,
    deterministic, no Docker. Mirrors the real one's path contract."""

    def __init__(self):
        self.files = {}
        self.bash = []
        self.result = {"exit": 0, "output": "ok"}

    async def list_files(self):
        return sorted(self.files)[:200]

    async def read_file(self, name):
        validate_relpath(name)
        return self.files.get(name)

    async def write_file(self, name, content):
        validate_relpath(name)
        self.files[name] = content

    async def run_bash(self, command, timeout=30.0):
        self.bash.append(command)
        return dict(self.result)


class FakeDocker:
    """Replaces sandbox._run: records argv/stdin, plays scripted replies."""

    def __init__(self, script=()):
        self.calls = []
        self.script = list(script)

    async def __call__(self, argv, *, stdin=None, timeout=None):
        self.calls.append((tuple(argv), stdin))
        reply = self.script.pop(0) if self.script else (0, b"", b"")
        return reply(argv) if callable(reply) else reply

    def argvs(self):
        return [argv for argv, _ in self.calls]


class FakeStream:
    def __init__(self, data):
        self._data = data

    async def read(self, n):
        chunk, self._data = self._data[:n], self._data[n:]
        return chunk


class FakeStdin:
    def __init__(self):
        self.data = bytearray()
        self.closed = False

    def write(self, data):
        self.data.extend(data)

    async def drain(self):
        return None

    def close(self):
        self.closed = True



class FakeProc:
    def __init__(self, out=b"", err=b""):
        self.stdin = FakeStdin()
        self.stdout, self.stderr = FakeStream(out), FakeStream(err)
        self.argv = None
        self.killed = False

    async def wait(self):
        return 0

    def kill(self):
        self.killed = True


# -- path confinement ---------------------------------------------------------

def test_validate_relpath_rejects_escapes_and_absolutes():
    for bad in ("/etc/passwd", "../../etc/passwd", "a/../../b", "a/../.."):
        with pytest.raises(ValueError, match="escapes"):
            validate_relpath(bad)
    with pytest.raises(ValueError, match="invalid"):
        validate_relpath("")
    assert validate_relpath("notes.md") == "notes.md"
    assert validate_relpath("sub/dir/a.md") == "sub/dir/a.md"


def test_paths_rejected_before_docker_invocation(monkeypatch):
    box = DockerSandboxContainer("never-started")
    docker = FakeDocker(script=[AssertionError("docker must not be invoked")])
    monkeypatch.setattr(sandbox, "_run", docker)
    for bad in ("/etc/passwd", "../../etc/passwd", ""):
        with pytest.raises(ValueError):
            asyncio.run(box.read_file(bad))
        with pytest.raises(ValueError):
            asyncio.run(box.write_file(bad, "x"))
    assert docker.calls == []


# -- container hardening and argv contracts -----------------------------------

class _ImageStub:
    image_id = "sha256:deadbeefcafe"


def test_start_hardens_container(monkeypatch):
    docker = FakeDocker(script=[(0, b"cid\n", b""), (0, b"true\n", b"")])
    monkeypatch.setattr(sandbox, "_run", docker)
    box = asyncio.run(DockerSandboxContainer.start(_ImageStub(), "Jev Lane"))
    run_argv = docker.argvs()[0]
    assert run_argv[:3] == ("docker", "run", "-d")
    pairs, flags = set(pairwise(run_argv)), set(run_argv)
    assert ("--network", "none") in pairs
    assert ("--cap-drop", "ALL") in pairs
    assert ("--security-opt", "no-new-privileges") in pairs
    assert ("--memory", "512m") in pairs and ("--memory-swap", "512m") in pairs
    assert ("--cpus", "1.5") in pairs and ("--pids-limit", "256") in pairs
    assert ("--tmpfs", "/tmp:rw,nosuid,size=64m") in pairs
    assert "--read-only" in flags
    assert run_argv[-1] == "sha256:deadbeefcafe"  # exact same image id
    assert box.name.startswith("jevloop-sandbox-jev-lane-")


def test_start_can_enable_bridge_network(monkeypatch):
    docker = FakeDocker(script=[(0, b"cid\n", b""), (0, b"true\n", b"")])
    monkeypatch.setattr(sandbox, "_run", docker)
    box = asyncio.run(DockerSandboxContainer.start(
        _ImageStub(), "networked", network_enabled=True))
    pairs = set(pairwise(docker.argvs()[0]))
    assert ("--network", "bridge") in pairs
    assert box.network_enabled is True


def test_start_accepts_raw_image_id(monkeypatch):
    docker = FakeDocker(script=[(0, b"cid\n", b""), (0, b"true\n", b"")])
    monkeypatch.setattr(sandbox, "_run", docker)
    asyncio.run(DockerSandboxContainer.start("sha256:raw", "plain"))
    assert docker.argvs()[0][-1] == "sha256:raw"


def test_start_can_bind_a_persistent_session_volume(monkeypatch):
    docker = FakeDocker(script=[(0, b"cid\n", b""), (0, b"true\n", b"")])
    monkeypatch.setattr(sandbox, "_run", docker)
    box = asyncio.run(DockerSandboxContainer.start(
        "sha256:raw", "jev", workspace_key="session--jev"))

    run_argv = docker.argvs()[0]
    mount = run_argv[run_argv.index("--mount") + 1]
    assert mount == (
        f"type=volume,source={workspace_volume_name('session--jev')},target=/workspace")
    assert box.volume_name == workspace_volume_name("session--jev")


def test_file_ops_go_through_docker_exec_argv(monkeypatch):
    box = DockerSandboxContainer("box")
    docker = FakeDocker(script=[
        (0, b"", b""),                       # write
        (0, b"hi\n", b""),                   # read
        (1, b"", b"no such sandbox file"),   # read missing
        (0, b"x.md\ny.md\n", b""),           # list
    ])
    monkeypatch.setattr(sandbox, "_run", docker)
    asyncio.run(box.write_file("a.md", "hi\n"))
    assert docker.calls[0] == (("docker", "exec", "-i", "box",
                                "sandboxfs", "write", "a.md"), b"hi\n")
    assert asyncio.run(box.read_file("a.md")) == "hi\n"
    assert asyncio.run(box.read_file("gone.md")) is None
    assert asyncio.run(box.list_files()) == ["x.md", "y.md"]
    for argv in docker.argvs()[1:]:
        assert argv[:2] == ("docker", "exec") and argv[2] == "box"


def test_run_bash_uses_argv_exec_in_workspace(monkeypatch):
    box = DockerSandboxContainer("box")
    proc = FakeProc(out=b"hi\n")

    async def fake_exec(*argv, **kwargs):
        proc.argv = argv
        return proc

    monkeypatch.setattr(sandbox.asyncio, "create_subprocess_exec", fake_exec)
    result = asyncio.run(box.run_bash("echo hi"))
    assert proc.argv == (
        "docker", "exec", "-i", "-w", "/workspace", "box",
        "sandboxexec", "--timeout", "30", "--kill-after", "5",
    )
    assert bytes(proc.stdin.data) == b"echo hi"
    assert proc.stdin.closed is True
    assert result == {"exit": 0, "output": "hi\n"}


def test_run_bash_output_is_bounded(monkeypatch):
    box = DockerSandboxContainer("box")
    proc = FakeProc(out=b"x" * 500_000, err=b"y" * 500_000)
    monkeypatch.setattr(sandbox.asyncio, "create_subprocess_exec",
                        lambda *a, **k: _awaitable(proc))
    result = asyncio.run(box.run_bash("cat /dev/zero"))
    assert len(result["output"]) == sandbox.OUTPUT_CAP
    assert result["exit"] == 0


def _awaitable(value):
    async def coro():
        return value
    return coro()


def test_close_is_idempotent_and_reaps(monkeypatch):
    docker = FakeDocker(script=[(0, b"cid\n", b""), (0, b"true\n", b""),
                                (0, b"", b"")])
    monkeypatch.setattr(sandbox, "_run", docker)
    box = asyncio.run(DockerSandboxContainer.start("sha256:x", "jev"))
    asyncio.run(box.close())
    asyncio.run(box.close())  # repeated close must not re-invoke docker
    assert docker.argvs()[-1] == ("docker", "rm", "-f", "-v", box.name)
    with pytest.raises(RuntimeError, match="closed"):
        asyncio.run(box.list_files())


# -- image build contract ------------------------------------------------------

def test_image_build_is_content_addressed(monkeypatch, tmp_path):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "goal.txt").write_text("compare jev vs plain")

    def build_ok(argv):
        iid = tmp_path / argv[argv.index("--iidfile") + 1]
        iid.write_text("sha256:cafebabe")
        return (0, b"", b"")

    miss = (1, b"", b"not found")
    docker = FakeDocker(script=[
        miss, build_ok,
        miss, build_ok,
        miss, build_ok,
    ])
    monkeypatch.setattr(sandbox, "_run", docker)
    first = asyncio.run(DockerSandboxImage.build(seed))
    second = asyncio.run(DockerSandboxImage.build(seed))
    assert first.image_id == second.image_id == "sha256:cafebabe"
    assert first.reference == second.reference  # same seed -> same tag
    assert first.reference.startswith("jevloop-sandbox:")
    (seed / "goal.txt").write_text("mutated seed")
    third = asyncio.run(DockerSandboxImage.build(seed))
    assert third.reference != first.reference  # different seed -> different tag
    inspect_argv, build_argv = docker.argvs()[:2]
    assert inspect_argv[:3] == ("docker", "image", "inspect")
    assert build_argv[:2] == ("docker", "build")
    assert "-t" in build_argv and "--iidfile" in build_argv


def test_image_build_reuses_cached_digest_and_close_keeps_it(monkeypatch, tmp_path):
    seed = tmp_path / "seed"
    seed.mkdir()
    docker = FakeDocker(script=[(0, b"sha256:cached\n", b"")])
    monkeypatch.setattr(sandbox, "_run", docker)

    image = asyncio.run(DockerSandboxImage.build(seed))
    asyncio.run(image.close())

    assert image.image_id == "sha256:cached"
    assert docker.argvs() == [
        ("docker", "image", "inspect", "-f", "{{.Id}}", image.reference),
    ]


# -- SandboxTools over an injected runtime --------------------------------------

def test_write_then_read_roundtrip():
    runtime = FakeRuntime()
    tools = SandboxTools(runtime)
    ws = Workspace()
    outcome = asyncio.run(tools.execute("WRITE_FILE", ToolContext(
        workspace=ws, text="digest.md\n\n# 内容\n第一条", live=True)))
    assert outcome["action"].startswith("write_file(digest.md")
    assert runtime.files["digest.md"] == "# 内容\n第一条"
    assert ws.files == {"digest.md": "digest.md"}

    listing = asyncio.run(tools.execute("LIST_FILES", ToolContext(workspace=ws)))
    assert listing["action"] == "list_files(1)"
    read = asyncio.run(tools.execute("READ_FILE", ToolContext(
        workspace=ws, target="digest.md")))
    assert read["action"].startswith("read_file(digest.md")
    assert any("第一条" in n["text"] for n in ws.notes)


def test_new_file_rejects_source_code_without_a_filename_line():
    runtime = FakeRuntime()
    outcome = asyncio.run(SandboxTools(runtime).execute(
        "WRITE_FILE",
        ToolContext(
            workspace=Workspace(),
            target="NEW",
            text="import json\nprint('not a filename')",
        ),
    ))

    assert outcome["status"] == "failed"
    assert outcome["effect_proof"] == "pre_effect"
    assert runtime.files == {}


def test_ops_execute_inside_container_even_when_not_live():
    runtime = FakeRuntime()
    tools = SandboxTools(runtime)
    outcome = asyncio.run(tools.execute("WRITE_FILE", ToolContext(
        workspace=Workspace(), text="x.md\nhello", live=False)))
    assert "dry_run" not in outcome  # live governs Lark writes only
    assert runtime.files == {"x.md": "hello"}
    asyncio.run(tools.execute("BASH", ToolContext(
        workspace=Workspace(), text="echo hi", live=False)))
    assert runtime.bash == ["echo hi"]


def test_two_runtimes_from_one_surface_stay_isolated():
    jev, plain = FakeRuntime(), FakeRuntime()
    lane_a, lane_b = SandboxTools(jev), SandboxTools(plain)
    asyncio.run(lane_a.execute("WRITE_FILE", ToolContext(
        workspace=Workspace(), text="a.md\nfrom jev", live=True)))
    asyncio.run(lane_b.execute("WRITE_FILE", ToolContext(
        workspace=Workspace(), text="b.md\nfrom plain", live=True)))
    assert jev.files == {"a.md": "from jev"}
    assert plain.files == {"b.md": "from plain"}


def test_write_over_existing_file_takes_content_only():
    runtime = FakeRuntime()
    tools = SandboxTools(runtime)
    ws = Workspace()
    ws.files["old.md"] = "old.md"
    outcome = asyncio.run(tools.execute("WRITE_FILE", ToolContext(
        workspace=ws, target="old.md", text="fresh body", live=True)))
    assert outcome["action"].startswith("write_file(old.md")
    assert runtime.files["old.md"] == "fresh body"  # content only, no filename line


def test_bash_outcomes_and_notes():
    runtime = FakeRuntime()
    runtime.result = {"exit": 3, "output": "boom"}
    tools = SandboxTools(runtime)
    ws = Workspace()
    empty = asyncio.run(tools.execute("BASH", ToolContext(workspace=ws, text="   ")))
    assert empty == {
        "status": "failed",
        "reason": "empty command",
        "effect_disposition": "NOT_APPLIED",
        "effect_proof": "pre_effect",  # provably nothing ran
    }
    outcome = asyncio.run(tools.execute("BASH", ToolContext(
        workspace=ws, text="make build", live=True)))
    assert outcome["status"] == "failed"
    # a mutating shell that ran cannot prove it changed nothing
    assert outcome["effect_disposition"] == "UNKNOWN"
    assert outcome["resolved"] is True
    assert outcome["continuation"] == "allowed"
    assert outcome["exit"] == 3 and outcome["output"] == "boom"
    assert any(
        note["kind"] == "bash"
        and note["command"] == "make build"
        and note["output"] == "boom"
        and note["exit"] == 3
        for note in ws.notes
    )

    runtime.result = {"exit": None, "output": "command timed out after 30s"}
    timed_out = asyncio.run(tools.execute("BASH", ToolContext(
        workspace=ws, text="sleep 99", live=True)))
    assert timed_out["status"] == "failed"
    assert timed_out["effect_disposition"] == "UNKNOWN"
    assert timed_out["resolved"] is True
    assert timed_out["continuation"] == "allowed"
    assert "timed out" in timed_out["reason"]
    assert any(
        note["kind"] == "bash"
        and note["command"] == "sleep 99"
        and "timed out" in note["output"]
        and note["exit"] is None
        for note in ws.notes
    )


def test_multi_file_read_returns_one_bounded_partial_outcome():
    runtime = FakeRuntime()
    runtime.files = {"a.md": "alpha", "b.md": "beta"}
    tools = SandboxTools(runtime)
    ws = Workspace(files={name: name for name in ("a.md", "b.md", "missing.md")})

    outcome = asyncio.run(tools.execute(
        "READ_FILE",
        ToolContext(workspace=ws, target=("a.md", "missing.md", "b.md")),
    ))

    assert outcome["status"] == "ready"
    assert outcome["partial"] is True
    assert outcome["read_files"] == ["a.md", "b.md"]
    assert [item["status"] for item in outcome["file_results"]] == [
        "ready", "missing", "ready",
    ]
    assert [note["target"] for note in ws.notes] == ["a.md", "b.md"]


def test_multi_file_read_caps_aggregate_content():
    runtime = FakeRuntime()
    runtime.files = {"a.md": "a" * 6000, "b.md": "b" * 6000}
    outcome = asyncio.run(SandboxTools(runtime).execute(
        "READ_FILE",
        ToolContext(
            workspace=Workspace(files={"a.md": "a.md", "b.md": "b.md"}),
            target=("a.md", "b.md"),
        ),
    ))

    assert sum(item["returned_chars"] for item in outcome["file_results"]) == 9000
    assert outcome["file_results"][0]["truncated"] is False
    assert outcome["file_results"][1]["truncated"] is True


def test_read_missing_file_is_blocked_not_raised():
    tools = SandboxTools(FakeRuntime())
    ws = Workspace()
    ws.files["ghost.md"] = "ghost.md"
    outcome = asyncio.run(tools.execute("READ_FILE", ToolContext(
        workspace=ws, target="ghost.md")))
    assert outcome["status"] == "blocked"
    assert "no such sandbox file" in outcome["reason"]


def test_list_caps_workspace_pool():
    runtime = FakeRuntime()
    runtime.files = {f"f{i:03}.md": "x" for i in range(300)}
    tools = SandboxTools(runtime)
    ws = Workspace()
    outcome = asyncio.run(tools.execute("LIST_FILES", ToolContext(workspace=ws)))
    assert outcome["action"] == "list_files(200)"
    assert len(ws.files) == 200


def test_spec_only_mount_advertises_but_refuses_execution():
    tools = SandboxTools()  # runtime=None: catalog/compilation only
    assert {s.name for s in tools.specs()} >= {"LIST_FILES", "READ_FILE",
                                               "WRITE_FILE", "BASH"}
    assert tools.available(Workspace()) == {"LIST_FILES", "READ_FILE", "SEARCH_FILES", "WRITE_FILE", "BASH"}
    with pytest.raises(RuntimeError, match="injected sandbox runtime"):
        asyncio.run(tools.execute("WRITE_FILE", ToolContext(
            workspace=Workspace(), text="x.md\nhello")))


def test_workspace_mutations_do_not_consume_external_write_budget():
    tools = SandboxTools(FakeRuntime())
    assert write_actions(tools) == set()
    effects = {spec.name: spec for spec in tools.specs()}
    assert effects["WRITE_FILE"].mutates_workspace is True
    assert effects["BASH"].mutates_workspace is True
