"""JevLoop CLI: `run` a goal, `bench` paired lanes, or `serve` the dashboard."""

import argparse
import json
import os
import sys
import uuid
from datetime import datetime

from jevloop.config import DEFAULT_AMBIGUITY_GATE, DEFAULT_ANSWER_PROGRESS_FLOOR, DEFAULT_ESCALATE_THRESHOLD
from jevloop.paths import BACKEND_ROOT

ENV_KEYS = ("TYPESAFE_API_KEY", "DEEPSEEK_API_KEY")


def load_env_file():
    """Load a project .env (backend/ first, repo root as fallback) without overriding env."""
    here = BACKEND_ROOT
    for path in (here / ".env", here.parent / ".env"):
        try:
            lines = path.read_text().splitlines() if path.is_file() else None
        except OSError:  # unreadable (permissions, sandbox): treat as absent
            lines = None
        if lines is None:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip("'\"")
            if key in ENV_KEYS and not os.environ.get(key):
                os.environ[key] = value
        return


def build_parser():
    parser = argparse.ArgumentParser(prog="jevloop")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run one goal; dry-run by default")
    run.add_argument("goal")
    run.add_argument("--live", action="store_true", help="actually execute writes (still guarded)")
    run.add_argument("--max-steps", type=int, default=30)
    run.add_argument("--max-writes", type=int, default=0,
                     help="optional external Lark mutation cap; 0 means unlimited")
    run.add_argument(
        "--offline-sandbox",
        dest="sandbox_network",
        action="store_false",
        help="disable sandbox network egress (network is enabled by default)",
    )
    run.add_argument("--min-confidence", type=float, default=0.6)
    run.add_argument("--allow-recipient", action="append", default=[],
                     help="recipient name allowed for sends (repeatable)")
    run.add_argument("--escalate-threshold", type=float,
                     default=DEFAULT_ESCALATE_THRESHOLD,
                     help="Jev decisions below this confidence are adjudicated by the LLM")
    run.add_argument("--ambiguity-gate", type=float,
                     default=DEFAULT_AMBIGUITY_GATE,
                     help="read-only low-confidence choices at or below this Noul ambiguity may pass")
    run.add_argument("--no-ambiguity-gate", dest="ambiguity_gate",
                     action="store_const", const=None,
                     help="disable the Noul ambiguity gate (confidence-only routing)")
    run.add_argument("--answer-progress-floor", type=float,
                     default=DEFAULT_ANSWER_PROGRESS_FLOOR,
                     help="optional first-ANSWER progress floor (default: disabled)")
    sub.add_parser("smoke", help="run the offline kernel + Docker end-to-end smoke")

    bench = sub.add_parser(
        "bench", help="paired multi-turn benchmark over the fixed scenario suite")
    bench.add_argument("--scenarios", default=None,
                       help="scenario suite path (default: benchmarks/scenarios.json)")
    bench.add_argument("--only", action="append", default=[], metavar="ID",
                       help="run only these scenario ids (repeatable)")
    bench.add_argument("--list", action="store_true",
                       help="validate the suite and exit (no keys or Docker needed)")
    bench.add_argument("--keep-volumes", action="store_true",
                       help="keep per-scenario Docker workspace volumes for inspection")
    bench.add_argument("--no-journal", action="store_true",
                       help="do not write bench turns into the dashboard run history")
    bench.add_argument("--artifacts", default=None,
                       help="output directory (default: backend/artifacts/bench/<stamp>)")
    bench.add_argument("--turn-timeout", type=float, default=420.0)
    bench.add_argument("--escalate-threshold", type=float,
                       default=DEFAULT_ESCALATE_THRESHOLD)
    bench.add_argument("--ambiguity-gate", type=float,
                       default=DEFAULT_AMBIGUITY_GATE,
                       help="read-only low-confidence choices at or below this "
                            "Noul ambiguity may pass")
    bench.add_argument("--no-ambiguity-gate", dest="ambiguity_gate",
                       action="store_const", const=None,
                       help="disable the Noul ambiguity gate (confidence-only routing)")
    bench.add_argument("--answer-progress-floor", type=float,
                       default=DEFAULT_ANSWER_PROGRESS_FLOOR,
                       help="arbitrate a turn's first ANSWER below this progress "
                            "score (default: disabled)")
    bench.add_argument("--min-confidence", type=float, default=0.6)
    bench.add_argument("--repeat", type=int, default=1,
                       help="run the suite N times and report medians "
                            "(per-run reports are kept in the combined report)")
    bench.add_argument("--allow-failures", action="store_true",
                       help="exit 0 even when scenario verifications fail")

    serve = sub.add_parser("serve", help="start the dashboard server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8790)
    return parser


def cmd_run(args):
    load_env_file()
    for required in ENV_KEYS:
        if not os.environ.get(required):
            sys.exit(f"Missing {required} in environment; no action executed.")

    import asyncio

    from jevloop.contracts.policy import WritePolicy
    from jevloop.decision.drivers import JevDriver
    from jevloop.runtime.kernel import RuntimeKernel
    from jevloop.storage import runstore
    from jevloop.tools.sandbox import DockerSandboxContainer, DockerSandboxImage, SandboxTools

    async def _run():
        run_id = uuid.uuid4().hex[:12]
        journal = runstore.Journal(run_id)
        journal.emit({
            "type": "meta",
            "params": {
                "goal": args.goal,
                "profile": "single_live" if args.live else "single_shadow",
                "max_steps": args.max_steps,
                "max_writes": args.max_writes,
                "sandbox_network": args.sandbox_network,
            },
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        })
        print(json.dumps({"run_id": run_id}, ensure_ascii=False), flush=True)
        image = await DockerSandboxImage.build()
        container = None
        failed = None
        try:
            container = await DockerSandboxContainer.start(
                image, "cli", network_enabled=args.sandbox_network)
            tools = SandboxTools(container)
            policy = WritePolicy(allowed_recipients=set(args.allow_recipient),
                                 min_confidence=args.min_confidence)
            kernel = RuntimeKernel(
                JevDriver(
                    escalate_threshold=args.escalate_threshold,
                    ambiguity_gate=args.ambiguity_gate,
                    answer_progress_floor=args.answer_progress_floor,
                ),
                tools,
                policy,
                live=args.live,
                max_steps=args.max_steps,
                max_writes=args.max_writes,
                event_sink=lambda event: journal.emit({**event, "lane": "jev"}),
            )
            async for step in kernel.run(args.goal):
                journal.emit({"type": "step", "lane": "jev", "step": step})
                journal.emit({
                    "type": "metrics", "lane": "jev", "metrics": kernel.metrics.summary()})
                if step.get("final"):
                    journal.emit({
                        "type": "final",
                        "lane": "jev",
                        "final": {"answer": kernel.workspace.answer},
                        "metrics": kernel.metrics.summary(),
                    })
                print(json.dumps(step, ensure_ascii=False, default=str), flush=True)
        except Exception as error:  # noqa: BLE001 - persist then surface CLI failure
            failed = error
            journal.emit({
                "type": "error", "lane": "jev",
                "message": f"{type(error).__name__}: {error}",
            })
        finally:
            if container is not None:
                await container.close()
            await image.close()
            journal.emit({"type": "done"})
        if failed is not None:
            raise failed

    asyncio.run(_run())

def cmd_smoke():
    from jevloop.evaluation.smoke import main as run_smoke

    print(json.dumps(run_smoke(), ensure_ascii=False, default=str))


def cmd_bench(args):
    import asyncio
    from pathlib import Path

    from jevloop.evaluation import bench

    load_env_file()
    suite_path = Path(args.scenarios or bench.DEFAULT_SCENARIOS)
    scenarios = bench.load_scenarios(suite_path)
    if args.only:
        known = {scenario.id for scenario in scenarios}
        unknown = set(args.only) - known
        if unknown:
            sys.exit(f"unknown scenario ids: {sorted(unknown)}; suite has {sorted(known)}")
        scenarios = [s for s in scenarios if s.id in set(args.only)]
    if args.list:
        print(f"suite {suite_path} digest {bench.suite_digest(suite_path)} "
              f"({len(scenarios)} scenarios, "
              f"{sum(len(s.turns) for s in scenarios)} turns)")
        for scenario in scenarios:
            print(f"  {scenario.id:22s} {scenario.category:22s} expect={scenario.favor:6s} "
                  f"turns={len(scenario.turns)} max_steps={scenario.max_steps}")
        return

    for required in ("TYPESAFE_API_KEY", "DEEPSEEK_API_KEY"):
        if not os.environ.get(required):
            sys.exit(f"Missing {required} in environment; no action executed.")
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.artifacts) if args.artifacts else (
        bench.DEFAULT_SCENARIOS.parent.parent / "artifacts" / "bench" / stamp)
    async def run_repeats():
        reports = []
        for index in range(max(1, args.repeat)):
            run_stamp = f"{stamp}-r{index + 1}" if args.repeat > 1 else stamp
            ctx = bench.BenchContext(
                sessions_dir=out_dir / "sessions",
                stamp=run_stamp,
                escalate_threshold=args.escalate_threshold,
                ambiguity_gate=args.ambiguity_gate,
                answer_progress_floor=args.answer_progress_floor,
                min_confidence=args.min_confidence,
                turn_timeout=args.turn_timeout,
                keep_volumes=args.keep_volumes,
                journal=not args.no_journal,
            )
            reports.append(await bench.run_suite(
                scenarios, ctx, suite_path=suite_path))
            verification = reports[-1]["aggregate"]["verification"]
            print(f"[run {index + 1}/{args.repeat}] "
                  f"{verification['scenarios_passed']}/{verification['scenarios']} scenarios, "
                  f"{verification['turns_passed']}/{verification['turns']} turns passed",
                  flush=True)
        return reports

    reports = asyncio.run(run_repeats())

    if args.repeat > 1:
        combined = {"suite": reports[0]["suite"], "runs": reports,
                    "median": bench.median_report(reports)}
        json_path, md_path = bench.write_repeat_report(combined, out_dir)
        median = combined["median"]
        jev, baseline = median["lanes"]["jev"], median["lanes"]["baseline"]
        print(f"\nmedian over {median['runs']} runs:")
        print(f"  jev      : {jev['steps']:.0f} steps, {jev['direct_jev_steps']:.0f} direct, "
              f"esc {jev['escalations_upheld']:.0f}/{jev['escalations_overridden']:.0f}, "
              f"${jev['cost_usd']:.4f}, {jev['elapsed_ms'] / 1000:.1f}s")
        print(f"  baseline : {baseline['steps']:.0f} steps, "
              f"${baseline['cost_usd']:.4f}, {baseline['elapsed_ms'] / 1000:.1f}s")
        print(f"  turns passed {median['turns_passed']:.0f}; authoring denials "
              f"dsml={median['denials']['dsml_fragment']:.0f} "
              f"echo={median['denials']['operation_echo']:.0f}")
    else:
        report = reports[0]
        json_path, md_path = bench.write_report(report, out_dir)
        verification = report["aggregate"]["verification"]
        print(f"\nbench {verification['scenarios_passed']}/{verification['scenarios']} scenarios, "
              f"{verification['turns_passed']}/{verification['turns']} turns passed")
    print(f"report: {md_path}")
    print(f"       {json_path}")
    if not args.allow_failures:
        failed = any(
            report["aggregate"]["verification"]["scenarios_passed"]
            < report["aggregate"]["verification"]["scenarios"]
            for report in reports
        )
        if failed:
            sys.exit(1)


def cmd_serve(args):
    load_env_file()
    from jevloop.apps.server import serve

    serve(host=args.host, port=args.port)


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        cmd_run(args)
    elif args.command == "serve":
        cmd_serve(args)
    elif args.command == "smoke":
        cmd_smoke()
    elif args.command == "bench":
        cmd_bench(args)


if __name__ == "__main__":
    main()
