"""Live smoke: one compiled JevLoop decision against the local Laya server.

Requires `jevloop laya-serve` already listening. It does not start Docker or
call the text model. The question set is the real sandbox catalog.
"""

import asyncio
import os

import httpx

from jevloop.context.state import Workspace
from jevloop.decision.laya import DEFAULT_BASE_URL
from jevloop.decision.model import choose
from jevloop.tools.sandbox import SPECS


class _Catalog:
    def specs(self):
        return list(SPECS)

    def available(self, _workspace):
        return {spec.name for spec in SPECS}


async def run():
    base = os.environ.get("LAYA_BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/")
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            response = await client.get(base + "/health")
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise SystemExit(
                f"Laya server is not reachable at {base}. "
                "Start it with: uv run jevloop laya-serve"
            ) from error
        health = response.json()
    if health.get("status") != "ok":
        raise SystemExit(f"Laya health is not ok: {health}")

    workspace = Workspace(goal="帮我生成一个简单的 fastapi 项目，只要一个 hello world 接口")
    workspace.files[".gitkeep"] = ".gitkeep"
    decision = await choose(
        workspace, workspace.goal, [], provider=_Catalog(), decision_backend="laya")
    operation = decision.get("operation")
    phase = decision.get("phase")
    if operation not in {spec.name for spec in SPECS} | {"ANSWER", "DONE", "BLOCKED"}:
        raise SystemExit(f"Laya selected an unknown operation: {operation}")
    if phase not in {"INSPECT", "ACT", "VERIFY", "RESPOND"}:
        raise SystemExit(f"Laya selected an unknown phase: {phase}")
    probabilities = decision.get("phase_probabilities") or {}
    if abs(sum(probabilities.values()) - 1) >= 0.02:
        raise SystemExit(f"phase probabilities are not a distribution: {probabilities}")
    return {
        "ok": True,
        "runtime": health.get("runtime"),
        "loaded": health.get("loaded"),
        "phase": phase,
        "operation": operation,
        "confidence": decision.get("confidence"),
        "latency_ms": decision.get("latency_ms"),
        "questions": len((decision.get("request") or {}).get("questions") or {}),
    }


def main():
    return asyncio.run(run())
