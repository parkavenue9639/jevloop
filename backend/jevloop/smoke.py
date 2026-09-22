"""Deterministic end-to-end smoke for the kernel and Docker sandbox."""

import asyncio

from .drivers import DriverProposal
from .guardrails import WritePolicy
from .kernel import RuntimeKernel
from .tools.sandbox import DockerSandboxContainer, DockerSandboxImage, SandboxTools


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
        return {
            "ok": True,
            "image_id": image.image_id,
            "answer": kernel.workspace.answer,
            "steps": kernel.budget.steps,
            "writes": kernel.budget.writes,
            "events": event_types,
            "checkpoints": len(checkpoints),
        }
    finally:
        if container is not None:
            await container.close()
        await image.close()


def main():
    return asyncio.run(run_smoke())
