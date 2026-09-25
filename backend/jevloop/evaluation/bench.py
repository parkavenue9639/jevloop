"""Paired multi-turn benchmark over a fixed scenario suite.

The scenario suite is the versioned artifact: goals, turn order and
expectations live in benchmarks/scenarios.json, and every report records the
suite digest so measured numbers stay attributable to an exact revision.

Both lanes drive the shared RuntimeKernel with dashboard session semantics:
per-lane ledgers (append-only, checkpointed after every step), per-lane
prompt-cache namespaces and per-lane Docker workspace volumes, so turns
continue one conversation and files persist across a scenario. Lanes run
sequentially within each turn to keep latency measurements free of cross-lane
rate-limit or Docker contention. Scenario workspace volumes are removed after
each scenario unless keep_volumes is set, so reruns start from clean state.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import statistics
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from jevloop.apps.server import CACHE_POLICY_ISOLATED, _cache_scope
from jevloop.config import DEFAULT_AMBIGUITY_GATE, DEFAULT_ANSWER_PROGRESS_FLOOR, DEFAULT_ESCALATE_THRESHOLD
from jevloop.contracts.policy import WritePolicy
from jevloop.decision.drivers import JevDriver, PlainLlmDriver
from jevloop.decision.laya import reported_model
from jevloop.paths import BACKEND_ROOT
from jevloop.runtime.kernel import RuntimeKernel
from jevloop.runtime.metrics import RunMetrics
from jevloop.storage import runstore, sessions
from jevloop.storage.runstore import Journal
from jevloop.tools.sandbox import (
    DockerSandboxContainer,
    DockerSandboxImage,
    SandboxTools,
    workspace_volume_name,
)

LANES = ("jev", "baseline")
DEFAULT_SCENARIOS = BACKEND_ROOT / "benchmarks" / "scenarios.json"
FILE_SCAN_CAP = 40


@dataclass(frozen=True)
class Turn:
    goal: str
    contains: tuple[str, ...] = ()
    contains_any: tuple[str, ...] | None = None
    max_chars: int | None = None
    files: tuple[dict, ...] = ()          # {"name": substring, "contains": str | None}
    any_file_contains: tuple[str, ...] = ()
    min_files: int | None = None
    bash: tuple[dict, ...] = ()             # {"command": str, "contains": str}
    inject_files: tuple[dict, ...] = ()     # {"name": relative path, "content": str}
    soft: tuple[str, ...] = ()            # check names matching a fragment warn only

    @classmethod
    def from_json(cls, payload, scenario_id, index):
        if not isinstance(payload, dict) or not str(payload.get("goal") or "").strip():
            raise ValueError(f"{scenario_id} turn {index}: goal must be a non-empty string")
        known = {"goal", "answer_contains", "answer_contains_any", "answer_max_chars",
                 "files", "any_file_contains", "min_files", "bash", "soft",
                 "inject_files"}
        unknown = set(payload) - known
        if unknown:
            raise ValueError(f"{scenario_id} turn {index}: unknown keys {sorted(unknown)}")
        files = payload.get("files") or []
        for want in files:
            if not isinstance(want, dict) or not want.get("name"):
                raise ValueError(f"{scenario_id} turn {index}: file checks need a 'name'")
        for want in payload.get("bash") or []:
            if (not isinstance(want, dict) or not str(want.get("command") or "").strip()
                    or not isinstance(want.get("contains"), str)):
                raise ValueError(
                    f"{scenario_id} turn {index}: bash checks need 'command' "
                    "and a string 'contains'")
        inject_files = payload.get("inject_files") or []
        for item in inject_files:
            if (
                not isinstance(item, dict)
                or not str(item.get("name") or "").strip()
                or not isinstance(item.get("content"), str)
            ):
                raise ValueError(
                    f"{scenario_id} turn {index}: injected files need string "
                    "'name' and 'content'")
        return cls(
            goal=payload["goal"],
            contains=tuple(payload.get("answer_contains") or ()),
            contains_any=(tuple(payload["answer_contains_any"])
                          if payload.get("answer_contains_any") else None),
            max_chars=payload.get("answer_max_chars"),
            files=tuple(files),
            any_file_contains=tuple(payload.get("any_file_contains") or ()),
            min_files=payload.get("min_files"),
            bash=tuple(payload.get("bash") or ()),
            soft=tuple(payload.get("soft") or ()),
            inject_files=tuple(inject_files),
        )


@dataclass(frozen=True)
class Scenario:
    id: str
    category: str
    favor: str
    description: str = ""
    max_steps: int = 20
    turns: tuple[Turn, ...] = ()

    @classmethod
    def from_json(cls, payload):
        scenario_id = payload.get("id")
        if not scenario_id or not isinstance(scenario_id, str):
            raise ValueError(f"scenario needs a string id: {payload!r:.120}")
        favor = payload.get("favor")
        if favor not in {"jev", "plain", "either"}:
            raise ValueError(f"{scenario_id}: favor must be jev | plain | either")
        turns = tuple(
            Turn.from_json(item, scenario_id, index)
            for index, item in enumerate(payload.get("turns") or ()))
        if not turns:
            raise ValueError(f"{scenario_id}: at least one turn is required")
        return cls(
            id=scenario_id,
            category=str(payload.get("category") or "uncategorized"),
            favor=favor,
            description=str(payload.get("description") or ""),
            max_steps=int(payload.get("max_steps") or 20),
            turns=turns,
        )


def load_scenarios(path) -> list[Scenario]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    scenarios = [Scenario.from_json(item) for item in payload.get("scenarios") or ()]
    if not scenarios:
        raise ValueError(f"{path}: suite contains no scenarios")
    ids = [scenario.id for scenario in scenarios]
    duplicates = {sid for sid in ids if ids.count(sid) > 1}
    if duplicates:
        raise ValueError(f"{path}: duplicate scenario ids {sorted(duplicates)}")
    return scenarios


def suite_digest(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]


def runtime_digest() -> str:
    """Content digest of executable backend/sandbox sources for attribution."""
    root = BACKEND_ROOT
    candidates = [
        *sorted((root / "jevloop").rglob("*.py")),
        *sorted(path for path in (root / "docker" / "sandbox").rglob("*")
                if path.is_file()),
        root / "pyproject.toml",
    ]
    digest = hashlib.sha256()
    for path in candidates:
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


@dataclass
class BenchContext:
    sessions_dir: Path
    stamp: str
    escalate_threshold: float = DEFAULT_ESCALATE_THRESHOLD
    min_confidence: float = 0.6
    ambiguity_gate: float | None = DEFAULT_AMBIGUITY_GATE
    answer_progress_floor: float | None = DEFAULT_ANSWER_PROGRESS_FLOOR
    turn_timeout: float = 420.0
    keep_volumes: bool = False
    journal: bool = True
    runs_dir: Path | None = None
    suite_digest: str | None = None
    build_image: Callable = DockerSandboxImage.build
    start_container: Callable = DockerSandboxContainer.start
    provider_for: Callable = lambda container: SandboxTools(container)
    driver_factories: dict | None = None
    remove_volume: Callable | None = None
    log: Callable = lambda line: print(line, flush=True)

    def driver_for(self, lane) -> Callable:
        if self.driver_factories:
            return self.driver_factories[lane]
        if lane == "jev":
            return lambda: JevDriver(
                escalate_threshold=self.escalate_threshold,
                ambiguity_gate=self.ambiguity_gate,
                answer_progress_floor=self.answer_progress_floor,
            )
        return PlainLlmDriver


@contextmanager
def _sessions_at(directory: Path):
    original = sessions.DIR
    sessions.DIR = Path(directory)
    try:
        yield
    finally:
        sessions.DIR = original


@contextmanager
def _runs_at(directory: Path | None):
    if directory is None:
        yield
        return
    original = runstore.DIR
    runstore.DIR = Path(directory)
    try:
        yield
    finally:
        runstore.DIR = original


async def _remove_volume(name: str):
    proc = await asyncio.create_subprocess_exec(
        "docker", "volume", "rm", name,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    await proc.communicate()


async def _run_turn(scenario, turn, lane, storage_id, cache_scope, container, ctx,
                    journal=None):
    from jevloop.context.projection import rebuild_workspace

    transcript = sessions.load(storage_id)
    workspace = None
    cache_policy = CACHE_POLICY_ISOLATED
    if transcript:
        workspace = rebuild_workspace(transcript)
        if cache_scope:
            system = transcript.messages()[0].get("content", "")
            if not system.startswith(f"[cache-scope:{cache_scope}]"):
                cache_policy = "legacy_shared"
                cache_scope = None
    metrics = RunMetrics(cache_policy=cache_policy, cache_scope_hash=cache_scope)
    kernel = RuntimeKernel(
        ctx.driver_for(lane)(),
        ctx.provider_for(container),
        WritePolicy(min_confidence=ctx.min_confidence),
        live=False,
        auto_acknowledge_unknown=False,
        max_steps=scenario.max_steps,
        max_writes=0,
        metrics=metrics,
        transcript=transcript,
        workspace=workspace,
        event_sink=(lambda event: journal.emit({**event, "lane": lane}))
        if journal else None,
        checkpoint=lambda current: sessions.save(storage_id, current),
        cache_scope=cache_scope,
    )
    started = time.perf_counter()
    final = None
    async for step in kernel.run(turn.goal):
        if "final" in step:
            final = step["final"]
        if journal:
            journal.emit({"type": "step", "lane": lane, "step": step})
            journal.emit({"type": "metrics", "lane": lane, "metrics": metrics.summary()})
            if step.get("final"):
                journal.emit({
                    "type": "final", "lane": lane,
                    "final": {"answer": kernel.workspace.answer},
                    "metrics": metrics.summary(),
                })
    routing_samples = []
    for step in kernel.trace:
        decision = step.get("decision") or {}
        operation = decision.get("operation")
        if not operation or operation in {"RUN_LIMIT", "RESTORE", "INVALID"}:
            continue
        escalation = step.get("escalation") or {}
        outcome = step.get("outcome") or {}
        error = outcome.get("error") or {}
        routing_samples.append({
            "operation": operation,
            "phase": decision.get("phase"),
            "confidence": decision.get("confidence"),
            "ambiguity": decision.get("ambiguity"),
            "progress_score": (decision.get("progress") or {}).get("score"),
            "escalation_reason": escalation.get("reason"),
            "arbitrated": bool(decision.get("arbitrated")),
            "outcome_status": outcome.get("status"),
            "error_code": error.get("code"),
        })
    return {
        "goal": turn.goal,
        "answer": kernel.workspace.answer,
        "final": final,
        "elapsed_ms": round((time.perf_counter() - started) * 1000),
        "metrics": metrics.summary(),
        "routing_samples": routing_samples,
    }


def _check_answer(turn: Turn, answer) -> list[dict]:
    text = (answer or "").strip()
    low = text.lower()
    checks = []
    for needle in turn.contains:
        checks.append({"name": f"answer contains {needle!r}",
                       "ok": needle.lower() in low})
    if turn.contains_any:
        options = list(turn.contains_any)
        checks.append({"name": f"answer contains any of {options!r}",
                       "ok": any(option.lower() in low for option in options)})
    if turn.max_chars is not None:
        checks.append({"name": f"answer at most {turn.max_chars} chars",
                       "ok": len(text) <= turn.max_chars})
    return checks


def _file_check_name(want: dict) -> str:
    sub = want.get("contains")
    return (f"file matching {want['name']!r} contains {sub!r}"
            if sub is not None else f"file matching {want['name']!r} exists")


async def _check_files(turn: Turn, container) -> list[dict]:
    checks = []
    names = await container.list_files()
    if turn.min_files is not None:
        checks.append({"name": f"at least {turn.min_files} files",
                       "ok": len(names) >= turn.min_files,
                       "detail": f"found {len(names)}: {names}"})
    for want in turn.files:
        matched = [name for name in names if want["name"].lower() in name.lower()]
        if not matched:
            checks.append({"name": _file_check_name(want), "ok": False,
                           "detail": f"no file matches; files: {names}"})
            continue
        contents = [await container.read_file(name) or "" for name in matched]
        sub = want.get("contains")
        ok = True if sub is None else any(sub.lower() in c.lower() for c in contents)
        checks.append({"name": _file_check_name(want), "ok": ok,
                       "detail": "" if ok else "content mismatch"})
    for sub in turn.any_file_contains:
        found = False
        for name in names[:FILE_SCAN_CAP]:
            content = await container.read_file(name)
            if content and sub.lower() in content.lower():
                found = True
                break
        checks.append({"name": f"some file contains {sub!r}", "ok": found})
    for want in turn.bash:
        command, sub = want["command"], want.get("contains", "")
        result = await container.run_bash(command)
        output = (result.get("output") or "")
        ok = sub.lower() in output.lower()
        label = command if len(command) <= 48 else command[:45] + "..."
        checks.append({
            "name": f"bash {label!r} outputs {sub!r}",
            "ok": ok,
            "detail": "" if ok else f"exit={result.get('exit')} output={output[:120]!r}",
        })
    return checks


def _mark_soft(checks: list[dict], soft: tuple[str, ...]) -> list[dict]:
    for check in checks:
        if any(fragment.lower() in check["name"].lower() for fragment in soft):
            check["soft"] = True
    return checks


def _lane_totals(turns: list[dict]) -> dict:
    totals = {
        "turns": len(turns),
        "elapsed_ms": 0, "steps": 0, "direct_jev_steps": 0, "llm_assisted_jev_steps": 0,
        "jev_calls": 0, "authoring_calls": 0, "parameter_authoring_calls": 0, "arbitration_calls": 0,
        "plain_decision_calls": 0, "escalations_upheld": 0, "escalations_overridden": 0,
        "jev_cost_usd": 0.0, "llm_cost_usd": 0.0,
    }
    for record in turns:
        totals["elapsed_ms"] += record.get("elapsed_ms") or 0
        metrics = record.get("metrics") or {}
        routing = metrics.get("routing") or {}
        totals["steps"] += routing.get("decision_steps", 0)
        totals["direct_jev_steps"] += routing.get("direct_jev_steps", 0)
        totals["llm_assisted_jev_steps"] += routing.get("llm_assisted_jev_steps", 0)
        jev = metrics.get("jev") or {}
        totals["jev_calls"] += jev.get("calls", 0)
        totals["jev_cost_usd"] += jev.get("est_cost_usd", 0) or 0
        helper = metrics.get("helper") or {}
        totals["llm_cost_usd"] += helper.get("est_cost_usd", 0) or 0
        for kind, key in (("authoring", "authoring_calls"),
                          ("parameter_authoring", "parameter_authoring_calls"),
                          ("arbitration", "arbitration_calls"),
                          ("plain_decision", "plain_decision_calls")):
            totals[key] += (helper.get("by_kind") or {}).get(kind, {}).get("calls", 0)
        escalations = metrics.get("escalations") or {}
        totals["escalations_upheld"] += escalations.get("upheld", 0)
        totals["escalations_overridden"] += escalations.get("overridden", 0)
    jev_steps = totals["direct_jev_steps"] + totals["llm_assisted_jev_steps"]
    totals["llm_avoidance_rate"] = (
        totals["direct_jev_steps"] / jev_steps if jev_steps else None)
    totals["cost_usd"] = round(totals["jev_cost_usd"] + totals["llm_cost_usd"], 6)
    totals["jev_cost_usd"] = round(totals["jev_cost_usd"], 6)
    totals["llm_cost_usd"] = round(totals["llm_cost_usd"], 6)
    return totals


def _compare(totals: dict) -> dict:
    jev, plain = totals["jev"], totals["baseline"]
    elapsed_delta = plain["elapsed_ms"] - jev["elapsed_ms"]
    cost_delta = round(plain["cost_usd"] - jev["cost_usd"], 6)
    faster = "jev" if jev["elapsed_ms"] < plain["elapsed_ms"] else "baseline"
    cheaper = "jev" if jev["cost_usd"] < plain["cost_usd"] else "baseline"
    return {
        "elapsed_delta_ms": elapsed_delta,
        "elapsed_delta_pct": (round(elapsed_delta / plain["elapsed_ms"] * 100, 1)
                              if plain["elapsed_ms"] else None),
        "cost_delta_usd": cost_delta,
        "cost_delta_pct": (round(cost_delta / plain["cost_usd"] * 100, 1)
                           if plain["cost_usd"] else None),
        "faster_lane": faster,
        "cheaper_lane": cheaper,
        "favor_observed": faster if faster == cheaper else "mixed",
    }


async def _run_scenario(scenario: Scenario, image, ctx: BenchContext) -> dict:
    base = f"bench-{scenario.id}-{ctx.stamp}"
    storage = {lane: f"{base}--{lane}" for lane in LANES}
    scopes = {lane: _cache_scope(base, lane) for lane in LANES}
    containers: dict = {}
    turn_records: dict = {lane: [] for lane in LANES}
    ctx.log(f"== {scenario.id} ({scenario.category}, expect {scenario.favor}): "
            f"{len(scenario.turns)} turns x {len(LANES)} lanes")
    try:
        for lane in LANES:
            containers[lane] = await ctx.start_container(
                image, lane, workspace_key=storage[lane], network_enabled=True)
        for index, turn in enumerate(scenario.turns):
            for item in turn.inject_files:
                await asyncio.gather(*(
                    container.write_file(item["name"], item["content"])
                    for container in containers.values()
                ))
                ctx.log(f"[{scenario.id}] injected {item['name']} into both lanes")
            journal = Journal(f"{base}-t{index + 1}") if ctx.journal else None
            if journal:
                journal.emit({
                    "type": "meta",
                    "params": {
                        "goal": turn.goal,
                        "profile": "paired_shadow",
                        "session_id": base,
                        "source": "bench",
                        "scenario": scenario.id,
                        "turn": index + 1,
                        "suite": ctx.suite_digest,
                        "max_steps": scenario.max_steps,
                        "escalate_threshold": ctx.escalate_threshold,
                        "ambiguity_gate": ctx.ambiguity_gate,
                        "answer_progress_floor": ctx.answer_progress_floor,
                        "min_confidence": ctx.min_confidence,
                    },
                    "created_at": datetime.now().astimezone().isoformat(
                        timespec="seconds"),
                })
                journal.emit({
                    "type": "sandbox_ready",
                    "image_id": getattr(image, "image_id", None),
                    "lanes": list(LANES),
                })
            for lane in LANES:
                ctx.log(f"[{scenario.id}] turn {index + 1}/{len(scenario.turns)} "
                        f"lane={lane}: {turn.goal[:64]}")
                try:
                    record = await asyncio.wait_for(
                        _run_turn(scenario, turn, lane, storage[lane], scopes[lane],
                                  containers[lane], ctx, journal=journal),
                        ctx.turn_timeout)
                except TimeoutError:
                    record = {"goal": turn.goal, "answer": "", "final": "timeout",
                              "elapsed_ms": round(ctx.turn_timeout * 1000),
                              "metrics": None,
                              "error": f"turn exceeded {ctx.turn_timeout:.0f}s"}
                    if journal:
                        journal.emit({"type": "error", "lane": lane,
                                      "message": record["error"]})
                except Exception as error:  # noqa: BLE001 - one lane-turn failure must not stop the suite
                    record = {"goal": turn.goal, "answer": "", "final": "error",
                              "elapsed_ms": None, "metrics": None,
                              "error": f"{type(error).__name__}: {error}"}
                    if journal:
                        journal.emit({"type": "error", "lane": lane,
                                      "message": record["error"]})
                checks = _mark_soft(
                    [*_check_answer(turn, record.get("answer")),
                     *await _check_files(turn, containers[lane])],
                    turn.soft)
                record["lane"] = lane
                record["checks"] = checks
                record["hard_failures"] = sum(
                    1 for check in checks if not check["ok"] and not check.get("soft"))
                turn_records[lane].append(record)
                ctx.log(f"    final={record['final']} "
                        f"hard_failures={record['hard_failures']} "
                        f"elapsed={record['elapsed_ms']}ms")
            if journal:
                journal.emit({"type": "done"})
    finally:
        if containers:
            await asyncio.gather(*(container.close() for container in containers.values()),
                                 return_exceptions=True)
        if not ctx.keep_volumes and ctx.remove_volume:
            for lane in LANES:
                await ctx.remove_volume(workspace_volume_name(storage[lane]))
    totals = {lane: _lane_totals(turn_records[lane]) for lane in LANES}
    records = turn_records["jev"] + turn_records["baseline"]
    status = "passed" if all(
        record["hard_failures"] == 0 and record.get("final") == "completed"
        for record in records) else "failed"
    return {
        "id": scenario.id,
        "category": scenario.category,
        "favor": scenario.favor,
        "description": scenario.description,
        "status": status,
        "lanes": {lane: {"turns": turn_records[lane], "totals": totals[lane]}
                  for lane in LANES},
        "comparison": _compare(totals),
    }


def _aggregate(scenarios: list[dict]) -> dict:
    lanes = {lane: {"turns": 0, "elapsed_ms": 0, "steps": 0, "direct_jev_steps": 0,
                    "llm_assisted_jev_steps": 0, "jev_calls": 0, "authoring_calls": 0,
                    "parameter_authoring_calls": 0, "arbitration_calls": 0, "plain_decision_calls": 0,
                    "escalations_upheld": 0, "escalations_overridden": 0,
                    "jev_cost_usd": 0.0, "llm_cost_usd": 0.0, "cost_usd": 0.0}
             for lane in LANES}
    turn_total = turn_passed = 0
    expected_vs_observed = []
    for scenario in scenarios:
        for lane in LANES:
            totals = scenario["lanes"][lane]["totals"]
            for key, value in totals.items():
                if isinstance(value, (int, float)) and key != "llm_avoidance_rate":
                    lanes[lane][key] += value
        for record in scenario["lanes"]["jev"]["turns"] + scenario["lanes"]["baseline"]["turns"]:
            turn_total += 1
            if record["hard_failures"] == 0 and record.get("final") == "completed":
                turn_passed += 1
        observed = scenario["comparison"]["favor_observed"]
        expected_vs_observed.append({
            "id": scenario["id"],
            "expected": scenario["favor"],
            "observed": observed,
            "match": scenario["favor"] == "either" or scenario["favor"] == observed,
        })
    for lane in LANES:
        jev_steps = lanes[lane]["direct_jev_steps"] + lanes[lane]["llm_assisted_jev_steps"]
        lanes[lane]["llm_avoidance_rate"] = (
            lanes[lane]["direct_jev_steps"] / jev_steps if jev_steps else None)
    return {
        "lanes": lanes,
        "verification": {
            "scenarios": len(scenarios),
            "scenarios_passed": sum(s["status"] == "passed" for s in scenarios),
            "turns": turn_total,
            "turns_passed": turn_passed,
        },
        "expected_vs_observed": expected_vs_observed,
    }


async def run_suite(scenarios: list[Scenario], ctx: BenchContext, *,
                    suite_path, digest: str | None = None) -> dict:
    digest = digest or suite_digest(suite_path)
    ctx.suite_digest = digest
    report = {
        "suite": {
            "path": str(suite_path),
            "digest": digest,
            "runtime_digest": runtime_digest(),
            "name": json.loads(Path(suite_path).read_text(encoding="utf-8")).get("suite"),
            "scenarios": len(scenarios),
            "turns": sum(len(scenario.turns) for scenario in scenarios),
            "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "models": {
                "jev": reported_model(),
                "llm": os.environ.get("TEXT_MODEL", "deepseek-chat"),
            },
            "escalate_threshold": ctx.escalate_threshold,
            "ambiguity_gate": ctx.ambiguity_gate,
            "answer_progress_floor": ctx.answer_progress_floor,
            "min_confidence": ctx.min_confidence,
        },
        "scenarios": [],
    }
    with _sessions_at(ctx.sessions_dir), _runs_at(ctx.runs_dir):
        image = await ctx.build_image()
        try:
            for scenario in scenarios:
                report["scenarios"].append(await _run_scenario(scenario, image, ctx))
        finally:
            await image.close()
    report["aggregate"] = _aggregate(report["scenarios"])
    report["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    return report


def _usd(value) -> str:
    return f"${value:.4f}" if isinstance(value, (int, float)) else "-"


def _pct(value) -> str:
    return f"{value:+.0f}%" if isinstance(value, (int, float)) else "-"


def render_markdown(report: dict) -> str:
    suite = report["suite"]
    aggregate = report["aggregate"]
    lines = [
        "# JevLoop paired benchmark",
        "",
        (
            f"suite `{suite['digest']}` ({suite['name']}) · runtime "
            f"`{suite['runtime_digest']}` · {suite['scenarios']} scenarios · "
            f"{suite['turns']} turns · jev `{suite['models']['jev']}` vs plain "
            f"`{suite['models']['llm']}` · started {suite['started_at'][:19]}"
        ),
        "",
        (
            "Lanes run sequentially per turn; both share one kernel, policy and image, "
            "with per-lane ledgers, prompt-cache namespaces and Docker volumes."
        ),
        "",
    ]
    for scenario in report["scenarios"]:
        verdict = "PASS" if scenario["status"] == "passed" else "FAIL"
        lines.append(f"## {scenario['id']} — {scenario['category']} · "
                     f"expect {scenario['favor']} · {verdict}")
        lines.append("")
        lines.append("| lane | turns | steps | direct | assisted | avoid% | esc ✓/↑ | "
                     "jev $ | llm $ | total $ | wall |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for lane in LANES:
            totals = scenario["lanes"][lane]["totals"]
            avoidance = totals["llm_avoidance_rate"]
            lines.append(
                f"| {lane} | {totals['turns']} | {totals['steps']} "
                f"| {totals['direct_jev_steps']} | {totals['llm_assisted_jev_steps']} "
                f"| {f'{avoidance:.0%}' if avoidance is not None else '–'} "
                f"| {totals['escalations_upheld']}/{totals['escalations_overridden']} "
                f"| {_usd(totals['jev_cost_usd'])} | {_usd(totals['llm_cost_usd'])} "
                f"| {_usd(totals['cost_usd'])} | {totals['elapsed_ms'] / 1000:.1f}s |")
        comparison = scenario["comparison"]
        lines.append("")
        lines.append(
            f"observed **{comparison['favor_observed']}** — "
            f"elapsed {_pct(comparison['elapsed_delta_pct'])} vs baseline, "
            f"cost {_pct(comparison['cost_delta_pct'])} vs baseline "
            f"({_usd(comparison['cost_delta_usd'])})")
        lines.extend(["", "| lane | parameter authoring | content authoring | arbitration | plain decisions |",
                      "|---|---|---|---|---|"])
        for lane in LANES:
            totals = scenario["lanes"][lane]["totals"]
            lines.append(f"| {lane} | {totals.get('parameter_authoring_calls', 0)} "
                         f"| {totals['authoring_calls']} | {totals['arbitration_calls']} "
                         f"| {totals['plain_decision_calls']} |")
        errors = [
            (lane, record)
            for lane in LANES
            for record in scenario["lanes"][lane]["turns"]
            if record.get("error")
        ]
        if errors:
            lines.append("")
            lines.append("turn errors:")
            for lane, record in errors:
                lines.append(f"- `{lane}` turn “{record['goal'][:48]}…” — {record['error']}")
        failures = [
            (lane, record, check)
            for lane in LANES
            for record in scenario["lanes"][lane]["turns"]
            for check in record["checks"]
            if not check["ok"] and not check.get("soft")]
        if failures:
            lines.append("")
            lines.append("failed checks:")
            for lane, record, check in failures:
                lines.append(f"- `{lane}` turn “{record['goal'][:48]}…” — "
                             f"{check['name']} {check.get('detail', '')}")
        lines.append("")
    lanes = aggregate["lanes"]
    verification = aggregate["verification"]
    lines += [
        "## Aggregate",
        "",
        "| lane | turns | steps | direct | assisted | avoid% | jev $ | llm $ | total $ | wall |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for lane in LANES:
        totals = lanes[lane]
        avoidance = totals["llm_avoidance_rate"]
        lines.append(
            f"| {lane} | {verification['turns'] // 2} | {totals['steps']} "
            f"| {totals['direct_jev_steps']} | {totals['llm_assisted_jev_steps']} "
            f"| {f'{avoidance:.0%}' if avoidance is not None else '–'} "
            f"| {_usd(totals['jev_cost_usd'])} | {_usd(totals['llm_cost_usd'])} "
            f"| {_usd(totals['cost_usd'])} | {totals['elapsed_ms'] / 1000:.1f}s |")
    lines += [
        "",
        (
            f"verification: {verification['scenarios_passed']}/{verification['scenarios']} "
            f"scenarios, {verification['turns_passed']}/{verification['turns']} turns passed"
        ),
        "",
        "| scenario | expected | observed | match |",
        "|---|---|---|---|",
    ]
    for row in aggregate["expected_vs_observed"]:
        lines.append(f"| {row['id']} | {row['expected']} | {row['observed']} "
                     f"| {'✓' if row['match'] else '✗'} |")
    lines.append("")
    return "\n".join(lines)


def write_report(report: dict, out_dir) -> tuple[Path, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "report.json"
    md_path = out_dir / "report.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path


# -- repeat runs: medians and denial accounting ------------------------------

MEDIAN_KEYS = ("steps", "direct_jev_steps", "llm_assisted_jev_steps",
               "llm_avoidance_rate", "escalations_upheld", "escalations_overridden",
               "jev_cost_usd", "llm_cost_usd", "cost_usd", "elapsed_ms")


def run_denial_counts(report: dict) -> dict:
    """Typed authoring-failure counts per run, for prompt-change A/Bs."""
    counts = {"dsml_fragment": 0, "operation_echo": 0}
    for scenario in report["scenarios"]:
        for lane in LANES:
            for turn in scenario["lanes"][lane]["turns"]:
                for denial in (turn.get("metrics") or {}).get("denials") or []:
                    if "malformed protocol fragment" in denial or "DSML" in denial:
                        counts["dsml_fragment"] += 1
                    if "operation name" in denial:
                        counts["operation_echo"] += 1
    return counts


def median_report(reports: list[dict]) -> dict:
    def med(values):
        present = [value for value in values if isinstance(value, (int, float))]
        return statistics.median(present) if present else None

    median = {
        "runs": len(reports),
        "lanes": {lane: {
            key: med([report["aggregate"]["lanes"][lane][key] for report in reports])
            for key in MEDIAN_KEYS} for lane in LANES},
        "turns_passed": med([report["aggregate"]["verification"]["turns_passed"]
                             for report in reports]),
        "denials": {kind: med([run_denial_counts(report)[kind] for report in reports])
                    for kind in ("dsml_fragment", "operation_echo")},
    }
    return median


def render_repeat_markdown(combined: dict) -> str:
    suite = combined["suite"]
    lines = [
        "# JevLoop paired benchmark — repeat medians",
        "",
        (f"suite `{suite['digest']}` ({suite['name']}) · runtime "
         f"`{suite['runtime_digest']}` · {combined['median']['runs']} runs · "
         f"jev `{suite['models']['jev']}` vs plain `{suite['models']['llm']}`"),
        "",
        "| run | lane | steps | direct | esc ✓/↑ | jev $ | llm $ | wall | pass | dsml | echo |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for index, report in enumerate(combined["runs"], start=1):
        for lane in LANES:
            totals = report["aggregate"]["lanes"][lane]
            denials = run_denial_counts(report)
            lines.append(
                f"| r{index} | {lane} | {totals['steps']} "
                f"| {totals['direct_jev_steps']} "
                f"| {totals['escalations_upheld']}/{totals['escalations_overridden']} "
                f"| {_usd(totals['jev_cost_usd'])} | {_usd(totals['llm_cost_usd'])} "
                f"| {totals['elapsed_ms'] / 1000:.1f}s "
                f"| {report['aggregate']['verification']['turns_passed']} "
                f"| {denials['dsml_fragment']} | {denials['operation_echo']} |")
    median = combined["median"]
    for lane in LANES:
        totals = median["lanes"][lane]
        lines.append(
            f"| **median** | {lane} | {totals['steps']:.0f} "
            f"| {totals['direct_jev_steps']:.0f} "
            f"| {totals['escalations_upheld']:.0f}/{totals['escalations_overridden']:.0f} "
            f"| {_usd(totals['jev_cost_usd'])} | {_usd(totals['llm_cost_usd'])} "
            f"| {totals['elapsed_ms'] / 1000:.1f}s | {median['turns_passed']:.0f} "
            f"| {median['denials']['dsml_fragment']:.0f} "
            f"| {median['denials']['operation_echo']:.0f} |")
    lines.append("")
    return "\n".join(lines)


def write_repeat_report(combined: dict, out_dir) -> tuple[Path, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "report.json"
    md_path = out_dir / "report.md"
    json_path.write_text(
        json.dumps(combined, ensure_ascii=False, indent=1, default=str),
        encoding="utf-8")
    md_path.write_text(render_repeat_markdown(combined), encoding="utf-8")
    return json_path, md_path
