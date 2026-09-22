"""Live flagship run: digest a group discussion into a doc, DM the link to yourself.

Creates one real document in 我的云空间 (my_library) and sends one real DM to
the logged-in user. Guardrails: recipient allowlist is the operator's own name,
min confidence 0.6, budgets enforced, sends carry idempotency keys.

Usage:
    uv run python scripts/live_demo.py [--chat 架构设计研讨] [--me 陆冲]
"""

import argparse
import asyncio
import json
import time

from jevloop.adapter.lark_cli import LarkAdapter
from jevloop.cli import load_env_file
from jevloop.drivers import JevDriver
from jevloop.guardrails import WritePolicy
from jevloop.kernel import RuntimeKernel
from jevloop.tools.lark import LarkTools


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--chat", default="架构设计研讨")
    parser.add_argument("--me", default="陆冲")
    parser.add_argument("--max-steps", type=int, default=8)
    args = parser.parse_args()

    load_env_file()
    goal = (f"把「{args.chat}」群最近的讨论总结成一篇新文档，"
            f"并把文档链接通过私信发给我自己（{args.me}）。")
    kernel = RuntimeKernel(
        JevDriver(escalate_threshold=0.5),
        LarkTools(LarkAdapter()),
        WritePolicy(min_confidence=0.6, allowed_recipients={args.me}),
        live=True,
        max_steps=args.max_steps,
        max_writes=0,
    )

    async def _drive():
        t0 = time.perf_counter()
        async for step in kernel.run(goal):
            decision = step.get("decision", {})
            outcome = step.get("outcome", {})
            if decision:
                print(f"{decision['operation']:14s} conf={decision['confidence']:.2f} "
                      f"target={kernel.label(decision.get('target'))!r} "
                      f"jev={decision['latency_ms']}ms", flush=True)
            if outcome:
                print(f"   -> {outcome.get('action')} created={outcome.get('created')} "
                      f"helper={(outcome.get('helper') or {}).get('latency_ms')}ms", flush=True)
            if "denied" in step:
                print("   DENIED:", step["denied"][:200], flush=True)
            if "final" in step:
                print(f"FINAL: {step['final']} | total {time.perf_counter() - t0:.1f}s "
                      f"| jev calls {len(kernel.metrics.jev_calls)}")
    asyncio.run(_drive())
    trace_path = "artifacts/live_demo_trace.json"
    import pathlib

    pathlib.Path("artifacts").mkdir(exist_ok=True)
    pathlib.Path(trace_path).write_text(
        json.dumps(kernel.trace, ensure_ascii=False, indent=1, default=str))
    print(f"trace saved to {trace_path}")


if __name__ == "__main__":
    main()
