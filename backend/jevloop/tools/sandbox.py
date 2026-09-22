"""Sandbox atomic tools: file read/write and shell, confined to a container.

The traditional agent primitives (read / write / bash equivalents) mounted
through the same ToolProvider surface as any domain tool. Execution lives in
Docker: one immutable image — built once from an explicit seed root and
content addressed — starts any number of isolated containers (comparison
lanes each get their own), and every file or shell operation is a
`docker exec` argv call: no host shell, no host paths, no network and no
capabilities inside the container. The container is injected into
SandboxTools; this module never touches the host filesystem. `live` governs
domain (Lark) writes only — local file and bash operations always execute
for real inside the container.
"""

import asyncio
import hashlib
import re
import shutil
import tempfile
import uuid
from pathlib import Path

from .base import ToolContext, ToolSpec

DOCKER_DIR = Path(__file__).resolve().parents[2] / "docker" / "sandbox"
DEFAULT_SEED = DOCKER_DIR / "seed"
IMAGE_PREFIX = "jevloop-sandbox"

WRITE_TEXT = ("When creating a NEW file: the first line is the flat file name (no "
              "directories, never an operation name), the remaining lines are the "
              "complete content. When overwriting an existing file: respond with "
              "the content only. Never split filename and content into separate "
              "fields.")

BASH_TEXT = (
    "Reply with ONE shell command line that bash executes as-is inside the "
    "sandbox working directory — a real command such as "
    "'python -m py_compile app.py' or 'curl -s 127.0.0.1:8000/'. "
    "Operation names from the tool catalog (READ_FILE, LIST_FILES, WRITE_FILE, "
    "ANSWER, ...) are agent tools, never shell commands: never reply with one. "
    "Join several shell steps on one line with '&&' or ';'. Each call runs with "
    "a 30-second timeout, so keep waits bounded and poll briefly. When starting "
    "a background service, redirect logs, save the exact `$!` PID to a file, "
    "and stop only that recorded PID after `kill -0` verification. Never use "
    "broad `pgrep -f`, `pkill -f`, `killall`, or `/proc/*/cmdline` scans to "
    "select processes: those patterns can match the current command itself."
)

SPECS = [
    ToolSpec(
        name="LIST_FILES",
        description="List the files currently in the sandbox. Use when the set "
                    "of files is uncertain (before reading or overwriting) or to "
                    "confirm what a run created. File contents are not included "
                    "— that is READ_FILE.",
        phases=("INSPECT", "VERIFY"),
    ),
    ToolSpec(
        name="READ_FILE",
        description="Read one or several known files into context. Select several "
                    "only when each file is independently relevant now. Prefer "
                    "files not yet read in this turn and files changed since their "
                    "last read. For command output use BASH; for discovery use "
                    "LIST_FILES.",
        needs_target=True, target_pool="files", multi_target_max=4,
        phases=("INSPECT", "VERIFY"),
    ),
    ToolSpec(
        name="WRITE_FILE",
        description="Create or replace one file with content that can be "
                    "authored directly: notes, drafts, data, deliverables. "
                    "Target an existing file to overwrite it, or NEW to create "
                    "one (the new name is written as the content's first line). "
                    "Not for programmatically produced output — generated files, "
                    "formatters and many-file scripts belong to BASH. One file "
                    "per step; when the goal also asks to report or tell, finish "
                    "with ANSWER after persisting instead of writing more.",
        needs_target=True, target_pool="files",
        target_extra=(("NEW", ("create a new file; its name is the first line "
                               "of the written content")),),
        needs_text=True, text_instruction=WRITE_TEXT,
        phases=("ACT",),
        consumes=("goal", "messages", "doc", "notes"), mutates_workspace=True,
    ),
    ToolSpec(
        name="BASH",
        description="Run one shell command in the sandbox; its output is "
                    "captured into context. For effects that are not directly "
                    "authorable file content: verify results with commands, "
                    "compute, search-and-transform or reformat many files, run "
                    "builds, formatters and code generators. Do not use it to "
                    "write content you could author (WRITE_FILE) or to pull one "
                    "file into context (READ_FILE).",
        needs_text=True, text_instruction=BASH_TEXT,
        consumes=("goal", "messages", "doc", "notes"), mutates_workspace=True,
        phases=("INSPECT", "ACT", "VERIFY"),
    ),
]

MULTI_READ_MAX = 4
OUTPUT_CAP = 4000   # chars of bash output kept for the ledger
READ_CAP = 20000    # aggregate chars pulled into context by one READ_FILE call
MULTI_READ_CAP = 9000  # aggregate multi-file content kept replay-safe
LIST_CAP = 200      # files surfaced by LIST_FILES
NEW_FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
BASH_TIMEOUT = 30.0  # seconds per bash command
CONTAINER_MEMORY = "512m"
CONTAINER_CPUS = "1.5"
CONTAINER_PIDS = 256


def validate_relpath(name: str) -> str:
    """Lexical confinement, enforced before any Docker invocation: relative,
    no parent hops, no absolute or empty names. The in-container helper
    re-checks (symlink-aware) as defense in depth."""
    if not name or "\x00" in name:
        raise ValueError(f"invalid sandbox path: {name!r}")
    if name.startswith(("/", "\\")):
        raise ValueError(f"path escapes the sandbox: {name}")
    if ".." in name.split("/"):
        raise ValueError(f"path escapes the sandbox: {name}")
    return name


async def _run(argv, *, stdin: bytes | None = None,
               timeout: float | None = None) -> tuple[int, bytes, bytes]:
    """Run one argv program (never a shell) with a bounded wait."""
    proc = await asyncio.create_subprocess_exec(
        *argv, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin), timeout)
    except TimeoutError:
        proc.kill()
        raise
    return proc.returncode or 0, out, err


async def _drain(stream, cap: int) -> bytes:
    """Read a pipe to EOF keeping at most `cap` bytes — draining past the cap
    keeps the writer from blocking on a full pipe."""
    kept = bytearray()
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            return bytes(kept)
        kept.extend(chunk[:cap - len(kept)])


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "lane"


def workspace_volume_name(workspace_key: str) -> str:
    digest = hashlib.sha256(workspace_key.encode()).hexdigest()[:24]
    return f"{IMAGE_PREFIX}-workspace-{digest}"

def _context_digest(context: Path) -> str:
    """Content address of a build context: sha256 over every file's relative
    path and bytes. Identical Dockerfile + helper + seed => identical tag, so
    the same seed always rederives the same image."""
    digest = hashlib.sha256()
    for path in sorted(p for p in context.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(context)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


class DockerSandboxImage:
    """Content-addressed immutable sandbox image shared across runs.

    `build()` first resolves the digest-derived tag from Docker's local image
    cache and builds only on a miss. `close()` releases this Python handle but
    deliberately keeps the immutable image: dashboard, CLI, smoke, and bench
    may coexist, and no one caller owns the shared Docker object. Workspace
    state lives in per-Session/lane volumes, never in this image.
    """

    def __init__(self, image_id: str, reference: str):
        self.image_id = image_id
        self.reference = reference
        self._closed = False

    @classmethod
    async def build(cls, seed_root: Path | None = None) -> "DockerSandboxImage":
        if shutil.which("docker") is None:
            raise RuntimeError("docker CLI not found; the sandbox runs in Docker")
        seed = Path(seed_root) if seed_root is not None else DEFAULT_SEED
        context = Path(tempfile.mkdtemp(prefix="jevloop-sandbox-ctx-"))
        try:
            for name in ("Dockerfile", "sandboxfs.py", "sandboxexec.py"):
                source = DOCKER_DIR / name
                if not source.is_file():
                    raise FileNotFoundError(f"missing docker sandbox asset: {source}")
                shutil.copy2(source, context / name)
            if not seed.is_dir():
                raise FileNotFoundError(f"seed root not found: {seed}")
            shutil.copytree(seed, context / "seed")
            iid_file = context / ".image-id"
            reference = f"{IMAGE_PREFIX}:{_context_digest(context)}"
            code, out, _err = await _run(
                ["docker", "image", "inspect", "-f", "{{.Id}}", reference],
                timeout=30,
            )
            if code == 0 and out.strip():
                return cls(out.decode().strip(), reference)
            code, out, err = await _run(
                ["docker", "build", "--iidfile", str(iid_file),
                 "-t", reference, str(context)], timeout=600)
            if code != 0:
                tail = (out + err).decode(errors="replace").strip()[-1200:]
                raise RuntimeError(f"docker build failed (exit {code}):\n{tail}")
            return cls(iid_file.read_text().strip(), reference)
        finally:
            shutil.rmtree(context, ignore_errors=True)

    async def close(self) -> None:
        """Release the handle; the content-addressed Docker cache stays warm."""
        self._closed = True

    async def delete(self) -> None:
        """Explicit maintenance operation; never called by ordinary runs."""
        code, _, err = await _run(["docker", "rmi", "-f", self.image_id], timeout=120)
        if code == 0 or b"No such image" in err:
            self._closed = True
            return
        raise RuntimeError(f"docker rmi failed for {self.reference}: "
                           f"{err.decode(errors='replace')[:300]}")


class DockerSandboxContainer:
    """The mutable half of a sandbox: one container running an immutable image.

    start() chooses explicit bridge or no-network mode, with a read-only root
    filesystem, all capabilities dropped, no-new-privileges and hard resource
    limits. The image's VOLUME gives each lane a writable workspace; /tmp is a
    small private tmpfs. Operations are `docker exec` argv calls with no host
    environment, credentials, home directory or sockets passed through.
    """

    def __init__(self, name: str, volume_name: str | None = None,
                 network_enabled: bool = False):
        self.name = name
        self.volume_name = volume_name
        self.network_enabled = network_enabled
        self._closed = False

    @classmethod
    async def start(cls, image, lane_id: str, workspace_key: str | None = None,
                    network_enabled: bool = False) -> "DockerSandboxContainer":
        """Start a container with an optional persistent volume and egress."""
        if shutil.which("docker") is None:
            raise RuntimeError("docker CLI not found; the sandbox runs in Docker")
        reference = getattr(image, "image_id", None) or str(image)
        name = f"{IMAGE_PREFIX}-{_slug(lane_id)}-{uuid.uuid4().hex[:8]}"
        argv = [
            "docker", "run", "-d", "--name", name,
            "--network", "bridge" if network_enabled else "none", "--read-only",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--memory", CONTAINER_MEMORY, "--memory-swap", CONTAINER_MEMORY,
            "--cpus", CONTAINER_CPUS, "--pids-limit", str(CONTAINER_PIDS),
            "--tmpfs", "/tmp:rw,nosuid,size=64m",
        ]
        volume_name = None
        if workspace_key:
            volume_name = workspace_volume_name(workspace_key)
            argv.extend([
                "--mount", f"type=volume,source={volume_name},target=/workspace",
            ])
        argv.append(reference)
        code, out, err = await _run(argv, timeout=120)
        if code != 0:
            raise RuntimeError(f"docker run failed for {reference}: "
                               f"{(out + err).decode(errors='replace')[:400]}")
        container = cls(name, volume_name, network_enabled)
        code, out, err = await _run(
            ["docker", "inspect", "-f", "{{.State.Running}}", name], timeout=30)
        if code != 0 or out.strip() != b"true":
            await container.close()
            raise RuntimeError(f"sandbox container {name} is not running "
                               f"(inspect said {out.strip()!r})")
        return container

    # -- docker exec plumbing ------------------------------------------------
    async def _exec(self, argv_tail, *, stdin: bytes | None = None,
                    timeout: float = 60.0) -> tuple[int, bytes, bytes]:
        if self._closed:
            raise RuntimeError(f"sandbox container {self.name} is closed")
        argv = ["docker", "exec"]
        if stdin is not None:
            argv.append("-i")
        return await _run([*argv, self.name, *argv_tail], stdin=stdin, timeout=timeout)

    # -- file / shell surface -------------------------------------------------
    async def list_files(self) -> list[str]:
        code, out, err = await self._exec(["sandboxfs", "list"])
        if code != 0:
            raise RuntimeError(f"sandboxfs list failed (exit {code}): "
                               f"{err.decode(errors='replace')[:200]}")
        return out.decode(errors="replace").splitlines()[:LIST_CAP]

    async def read_file(self, name: str) -> str | None:
        rel = validate_relpath(name)
        code, out, err = await self._exec(["sandboxfs", "read", rel])
        if code == 1:
            return None  # no such file
        if code != 0:
            raise RuntimeError(f"sandboxfs read failed (exit {code}): "
                               f"{err.decode(errors='replace')[:200]}")
        return out.decode(errors="replace")[:READ_CAP]

    async def write_file(self, name: str, content: str) -> None:
        rel = validate_relpath(name)
        code, _, err = await self._exec(["sandboxfs", "write", rel],
                                        stdin=content.encode("utf-8"))
        if code != 0:
            raise RuntimeError(f"sandboxfs write failed (exit {code}): "
                               f"{err.decode(errors='replace')[:200]}")

    async def is_running(self) -> bool:
        code, out, _err = await _run(
            ["docker", "inspect", "-f", "{{.State.Running}}", self.name],
            timeout=30,
        )
        return code == 0 and out.strip() == b"true"

    async def run_bash(self, command: str, timeout: float = BASH_TIMEOUT) -> dict:
        """Run one stdin-provided script in /workspace. sandboxexec gives only
        this command a process group and enforces the in-container deadline;
        the host deadline is a Docker-transport backstop."""
        if self._closed:
            raise RuntimeError(f"sandbox container {self.name} is closed")
        argv = [
            "docker", "exec", "-i", "-w", "/workspace", self.name,
            "sandboxexec", "--timeout", str(max(1, round(timeout))),
            "--kill-after", "5",
        ]
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            assert proc.stdin is not None
            proc.stdin.write(command.encode())
            await proc.stdin.drain()
            proc.stdin.close()
            out, err = await asyncio.wait_for(asyncio.gather(
                _drain(proc.stdout, OUTPUT_CAP * 4),
                _drain(proc.stderr, OUTPUT_CAP * 4)), timeout + 15)
            code = await proc.wait()
        except (BrokenPipeError, ConnectionResetError):
            out, err = b"", b"command transport closed while sending script"
            code = await proc.wait()
        except TimeoutError:
            proc.kill()
            return {
                "exit": None,
                "output": f"command transport timed out after {timeout:.0f}s",
                "container_running": await self.is_running(),
            }
        result = {
            "exit": code,
            "output": (out + err).decode(errors="replace")[:OUTPUT_CAP],
        }
        if code != 0:
            result["container_running"] = await self.is_running()
        return result

    async def close(self) -> None:
        """Remove the container; named session workspace volumes remain."""
        if self._closed:
            return
        code, _, err = await _run(["docker", "rm", "-f", "-v", self.name], timeout=60)
        if code == 0 or b"No such container" in err:
            self._closed = True
            return
        raise RuntimeError(f"docker rm failed for {self.name}: "
                           f"{err.decode(errors='replace')[:300]}")


class SandboxTools:
    """The atomic primitives, executed inside an injected sandbox container.

    `runtime` is any object with the DockerSandboxContainer surface (async
    list_files / read_file / write_file / run_bash, close). Comparison lanes
    each inject their own container started from the same image. A spec-only
    mount (runtime=None) can still advertise the catalog and answer schema
    compilation, but executing any tool fails fast: this provider has no
    host-filesystem mode, and `live` no longer matters to it — file and
    shell operations always run for real inside the container; only domain
    (Lark) writes keep the live/dry-run distinction.
    """

    def __init__(self, runtime=None):
        self._runtime = runtime

    def specs(self):
        return SPECS

    def available(self, workspace):
        valid = {"LIST_FILES", "WRITE_FILE", "BASH"}
        if workspace.files:
            valid.add("READ_FILE")
        return valid

    async def execute(self, name: str, ctx: ToolContext) -> dict:
        if name not in {"LIST_FILES", "READ_FILE", "WRITE_FILE", "BASH"}:
            raise KeyError(f"unknown tool {name}")
        if self._runtime is None:
            raise RuntimeError("SandboxTools needs an injected sandbox runtime "
                               "(start a DockerSandboxContainer and pass it in)")
        workspace = ctx.workspace
        if name == "LIST_FILES":
            return await self._list_files(workspace)
        if name == "READ_FILE":
            return await self._read_file(workspace, ctx.target)
        if name == "WRITE_FILE":
            return await self._write_file(ctx)
        return await self._bash(workspace, ctx)

    # -- files ---------------------------------------------------------------
    async def _list_files(self, workspace):
        rels = (await self._runtime.list_files())[:LIST_CAP]
        for rel in rels:
            workspace.files[rel] = rel
        return {"status": "ready", "action": f"list_files({len(rels)})"}

    async def _read_file(self, workspace, target):
        targets = tuple(target) if isinstance(target, (list, tuple)) else (target,)
        if not 1 <= len(targets) <= MULTI_READ_MAX or len(set(targets)) != len(targets):
            return {
                "status": "failed",
                "reason": f"READ_FILE requires 1..{MULTI_READ_MAX} unique targets",
                "effect_disposition": "NOT_APPLIED",
                "effect_proof": "pre_effect",
            }

        remaining = READ_CAP if len(targets) == 1 else MULTI_READ_CAP
        results = []
        read_files = []
        for path in targets:
            content = await self._runtime.read_file(path)
            if content is None:
                results.append({"target": path, "status": "missing"})
                continue
            excerpt = content[:remaining]
            remaining -= len(excerpt)
            truncated = len(excerpt) < len(content)
            workspace.notes.append({
                "kind": "file",
                "target": path,
                "text": f"[file: {path}]\n{excerpt}",
            })
            results.append({
                "target": path,
                "status": "ready",
                "content": excerpt,
                "returned_chars": len(excerpt),
                "truncated": truncated,
            })
            read_files.append(path)

        if not read_files:
            return {
                "status": "blocked",
                "reason": (
                    f"no such sandbox file: {targets[0]}"
                    if len(targets) == 1
                    else "none of the requested sandbox files exist"
                ),
                "file_results": results,
                "read_files": [],
            }
        total_chars = sum(item.get("returned_chars", 0) for item in results)
        action = (
            f"read_file({read_files[0]}, {total_chars} chars)"
            if len(read_files) == 1
            else f"read_file({len(read_files)} files, {total_chars} chars)"
        )
        return {
            "status": "ready",
            "action": action,
            "file_results": results,
            "read_files": read_files,
            "partial": len(read_files) != len(targets),
        }

    async def _write_file(self, ctx):
        lines = ctx.text.splitlines()
        if ctx.target and ctx.target != "NEW":
            filename = Path(ctx.target).name
            content = ctx.text
        else:
            candidate = lines[0].strip() if lines else ""
            if not NEW_FILE_NAME.fullmatch(candidate):
                return {
                    "status": "failed",
                    "reason": (
                        "NEW file content must start with a flat filename using "
                        "letters, digits, dot, underscore, or hyphen"
                    ),
                    "effect_disposition": "NOT_APPLIED",
                    "effect_proof": "pre_effect",
                }
            filename = candidate
            content = "\n".join(lines[1:]).lstrip("\n")
        await self._runtime.write_file(filename, content)
        ctx.workspace.files[filename] = filename
        return {
            "status": "ready",
            "action": f"write_file({filename}, {len(content)} chars)",
            "changed_files": [filename],
        }

    # -- shell ----------------------------------------------------------------
    async def _bash(self, workspace, ctx):
        command = (ctx.text or "").strip()
        if not command:
            return {
                "status": "failed",
                "reason": "empty command",
                "effect_disposition": "NOT_APPLIED",
                "effect_proof": "pre_effect",
            }
        result = await self._runtime.run_bash(command)
        output = (result.get("output") or "")[:OUTPUT_CAP]
        workspace.notes.append({
            "kind": "bash",
            "command": command,
            "output": output,
            "exit": result["exit"],
        })
        outcome = {
            "status": "ready" if result["exit"] == 0 else "failed",
            "action": f"bash({command!r})",
            "exit": result["exit"],
            "files_may_have_changed": True,
        }
        if result["exit"] is None:
            outcome["reason"] = output or "command timed out"
        elif result["exit"] != 0:
            outcome["reason"] = f"command exited with status {result['exit']}"
        if result["exit"] != 0:
            container_running = result.get("container_running", True)
            outcome.update({
                "effect_disposition": "UNKNOWN",
                "container_running": container_running,
            })
            if container_running:
                # The exact command ended and Docker still answers for this
                # lane. Preserve partial-effect uncertainty as evidence, but
                # do not turn an ordinary sandbox failure into a permanent
                # session lock.
                outcome.update({
                    "resolved": True,
                    "resolution": "sandbox_command_ended",
                    "continuation": "allowed",
                })
        if output:
            outcome["output"] = output[:300]
        return outcome
