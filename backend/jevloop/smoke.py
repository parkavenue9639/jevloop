"""Deterministic end-to-end smoke for the kernel and Docker sandbox."""

import asyncio
import json

from .argument_helper import generate_arguments
from .drivers import DriverProposal
from .guardrails import WritePolicy
from .kernel import RuntimeKernel
from .state import Workspace
from .tools.base import ToolContext
from .tools.sandbox import DockerSandboxContainer, DockerSandboxImage, SandboxTools
from .transcript import llm_tool_schemas


class SmokeDriver:
    name = "smoke"

    def __init__(self):
        self._index = 0

    async def decide(self, _context):
        decisions = [
            ("WRITE_FILE", "NEW", "smoke.txt\nsmoke-ok"),
            ("READ_FILE", "smoke.txt", None),
            ("BASH", None, "python -c 'import fastapi, uvicorn' && "
             + "test \"$(cat smoke.txt)\" = smoke-ok && printf bash-ok"),
            ("ANSWER", None, "offline-smoke-ok"),
        ]
        operation, target, content = decisions[self._index]
        self._index += 1
        decision = {
            "operation": operation,
            "target": target,
            "confidence": 1.0,
            "operation_probabilities": {operation: 1.0},
            "target_probabilities": {target: 1.0} if target else {},
            "target_confidence": 1.0 if target else None,
            "latency_ms": 0,
            "usage": {},
            "ledger_content": content,
        }
        return DriverProposal(decision=decision, base_decision=decision)


async def check_canonical_tools(container):
    """Exercise open arguments through the provider and real sandbox helpers.

    A separate workspace keeps these checks out of the legacy kernel metrics.
    The caller owns the disposable container and its cleanup.
    """
    provider = SandboxTools(container)
    workspace = Workspace()

    async def execute(operation, **arguments):
        outcome = await provider.execute(operation, ToolContext(
            workspace=workspace, arguments=arguments))
        assert outcome["status"] == "ready", (operation, outcome)
        return outcome

    content = "not-a-filename\n第二行🙂\ncanonical-search-marker\n"
    written = await execute("WRITE_FILE", path="observation-smoke/a.txt", content=content)
    assert written["changed_files"] == ["observation-smoke/a.txt"]
    assert await container.read_file("observation-smoke/a.txt") == content
    await execute("WRITE_FILE", path="observation-smoke/child/nested.txt", content="nested")
    await execute("WRITE_FILE", path="observation-smoke/z.txt", content="last")

    root = (await execute("LIST_FILES"))["observation"]
    assert "observation-smoke/a.txt" not in root["evidence"]
    assert any(ref["kind"] == "directory" and ref["value"] == "observation-smoke"
               for ref in root["references"])
    first = (await execute("LIST_FILES", path="observation-smoke", limit=1))["observation"]
    assert first["references"] == [{
        "kind": "file", "value": "observation-smoke/a.txt", "label": "observation-smoke/a.txt"}]
    assert first["truncated"] is True and first["next_offset"] == 1
    second = (await execute("LIST_FILES", path="observation-smoke",
                            offset=first["next_offset"], limit=1))["observation"]
    assert second["references"] == [{
        "kind": "directory", "value": "observation-smoke/child",
        "label": "observation-smoke/child"}]
    assert "nested.txt" not in second["evidence"]

    read = await execute("READ_FILE", path="observation-smoke/a.txt", offset=1, limit=1)
    assert read["file_results"][0]["content"] == "第二行🙂\n"
    assert read["file_results"][0]["next_offset"] == 2
    assert read["observation"]["unit"] == "lines" and read["observation"]["truncated"]
    multiple = await execute("READ_FILE", path=["observation-smoke/a.txt",
                                               "observation-smoke/z.txt"], limit=1)
    assert multiple["read_files"] == ["observation-smoke/a.txt", "observation-smoke/z.txt"]

    search = await execute("SEARCH_FILES", path="observation-smoke",
                           pattern="canonical-search-marker", glob="*.txt", limit=5)
    assert len(search["matches"]) == 1
    assert "canonical-search-marker" in search["observation"]["evidence"]
    assert search["observation"]["references"] == [{
        "kind": "file", "value": "observation-smoke/a.txt", "label": "observation-smoke/a.txt"}]

    before = dict(workspace.files)
    bash = await execute("BASH", command="printf 'invented.txt\\nfolder/\\nnot a file listing'")
    assert bash["observation"]["evidence"] == "invented.txt\nfolder/\nnot a file listing"
    assert bash["observation"]["references"] == []
    assert workspace.files == before
    return ["write_path_content", "list_scoped_pagination", "read_line_range",
            "read_multiple", "search_references", "bash_raw_no_references"]


async def run_smoke():
    image = await DockerSandboxImage.build()
    container = None
    events = []
    checkpoints = []
    try:
        container = await DockerSandboxContainer.start(image, "offline-smoke")
        kernel = RuntimeKernel(
            SmokeDriver(),
            SandboxTools(container),
            WritePolicy(min_confidence=0.9),
            live=False,
            max_steps=6,
            max_writes=0,
            event_sink=events.append,
            checkpoint=lambda transcript: checkpoints.append(len(transcript.messages())),
        )
        steps = [step async for step in kernel.run(
            "Create, read and validate smoke.txt, then answer.")]
        content = await container.read_file("smoke.txt")
        event_types = [event["type"] for event in events]
        dispositions = [
            event["disposition"] for event in events if event["type"] == "observation"
        ]
        assert content == "smoke-ok"
        assert steps[-1]["final"] == "completed"
        assert kernel.workspace.answer == "offline-smoke-ok"
        assert event_types == [
            "attempt_started", "decision_ready", "intent", "dispatch_started", "observation",
            "attempt_started", "decision_ready", "intent", "dispatch_started", "observation",
            "attempt_started", "decision_ready", "intent", "dispatch_started", "observation",
            "attempt_started", "decision_ready", "intent", "dispatch_started", "observation",
        ]
        assert dispositions == ["SUCCEEDED"] * 4
        assert any(note.get("kind") == "file" for note in kernel.workspace.notes)
        assert any(
            note.get("kind") == "bash" and "bash-ok" in note.get("output", "")
            for note in kernel.workspace.notes
        )
        canonical_checks = await check_canonical_tools(container)
        argument_checks = await check_argument_routes(container)
        return {
            "ok": True,
            "image_id": image.image_id,
            "answer": kernel.workspace.answer,
            "steps": kernel.budget.steps,
            "writes": kernel.budget.writes,
            "events": event_types,
            "checkpoints": len(checkpoints),
            "canonical_checks": canonical_checks,
            "argument_checks": argument_checks,
        }
    finally:
        if container is not None:
            await container.close()
        await image.close()


async def check_argument_routes(container):
    """Real kernel/provider execution with deterministic LLM transport receipts."""
    provider = SandboxTools(container)
    authored = []
    path = "cache-contract/new.txt"

    class Driver:
        name = "jev"

        def __init__(self):
            self.index = 0

        async def decide(self, _context):
            decisions = [
                {"operation": "WRITE_FILE", "binding_mode": "llm_parameters", "bound_arguments": {}},
                {"operation": "READ_FILE", "binding_mode": "observed",
                 "bound_arguments": {"path": path, "offset": 0, "limit": 200}},
                {"operation": "ANSWER", "binding_mode": "llm_parameters", "bound_arguments": {}},
            ]
            decision = {**decisions[self.index], "confidence": 1.0}
            self.index += 1
            return DriverProposal(decision=decision, base_decision=decision)

    async def author(ledger, tools, operation):
        async def post(_url, _key, request):
            assert request["tools"] == llm_tool_schemas(provider)
            authored.append(operation)
            arguments = ({"path": path, "content": "full-arguments-ok"}
                         if operation == "WRITE_FILE" else {"answer": "cache-contract-ok"})
            return {"choices": [{"finish_reason": "tool_calls", "message": {
                "role": "assistant", "content": None, "tool_calls": [{
                    "id": f"smoke-{len(authored)}", "type": "function", "function": {
                        "name": operation, "arguments": json.dumps(arguments)}}]}}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0}}
        return await generate_arguments(ledger, tools, operation, post=post)

    kernel = RuntimeKernel(Driver(), provider, WritePolicy(), arguer=author, max_steps=3)
    steps = [step async for step in kernel.run("Write a file, read it, and answer.")]
    assert steps[-1]["final"] == "completed"
    assert await container.read_file(path) == "full-arguments-ok"
    assert authored == ["WRITE_FILE", "ANSWER"]  # complete READ executes without an LLM
    assert len(kernel.metrics.helper_calls) == 2
    assert kernel.transcript.repair() == 0
    return ["full_argument_generation", "complete_read_without_llm", "stable_authoring_schemas"]


def main():
    return asyncio.run(run_smoke())
