"""Offline bench suite validation: loader, checks, and the paired runner harness.

No Docker, no model keys — image/container/provider/drivers are injected fakes
through BenchContext, the same seam the CLI leaves for the real stack.
"""

import asyncio
import hashlib
import json

import pytest

from jevloop import bench, sessions
from jevloop.drivers import DriverProposal
from jevloop.tools.base import ToolSpec

# --- loader ---------------------------------------------------------------

def _write_suite(tmp_path, payload):
    path = tmp_path / "scenarios.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _minimal_suite(**overrides):
    scenario = {
        "id": "alpha",
        "category": "read_nav_heavy",
        "favor": "jev",
        "turns": [{"goal": "do it", "answer_contains": ["done"]}],
    }
    scenario.update(overrides)
    return {"suite": "test", "scenarios": [scenario]}


def test_load_scenarios_accepts_minimal_suite(tmp_path):
    path = _write_suite(tmp_path, _minimal_suite())
    scenarios = bench.load_scenarios(path)
    assert len(scenarios) == 1
    assert scenarios[0].turns[0].goal == "do it"
    assert bench.suite_digest(path) == bench.suite_digest(path)
    assert len(bench.suite_digest(path)) == 12


@pytest.mark.parametrize("mutate, fragment", [
    ({"id": None}, "string id"),
    ({"favor": "nobody"}, "favor"),
    ({"turns": []}, "at least one turn"),
    ({"turns": [{"answer_contains": ["done"]}]}, "goal"),
    ({"turns": [{"goal": "x", "unknown_key": 1}]}, "unknown keys"),
])
def test_load_scenarios_rejects_bad_scenarios(tmp_path, mutate, fragment):
    path = _write_suite(tmp_path, _minimal_suite(**mutate))
    with pytest.raises(ValueError, match=fragment):
        bench.load_scenarios(path)


def test_load_scenarios_rejects_duplicate_ids(tmp_path):
    suite = _minimal_suite()
    suite["scenarios"].append(suite["scenarios"][0])
    path = _write_suite(tmp_path, suite)
    with pytest.raises(ValueError, match="duplicate scenario ids"):
        bench.load_scenarios(path)


# --- turn checks ------------------------------------------------------------

def _turn(**kwargs):
    return bench.Turn(goal="g", **kwargs)


def test_check_answer_substring_case_insensitive():
    checks = bench._check_answer(_turn(contains=["Done"]), "  it is DONE. ")
    assert checks == [{"name": "answer contains 'Done'", "ok": True}]


def test_parameter_authoring_is_counted_separately_in_lane_totals():
    totals = bench._lane_totals([{"metrics": {"helper": {"by_kind": {
        "parameter_authoring": {"calls": 3}, "authoring": {"calls": 1},
        "arbitration": {"calls": 2}}}}}])
    assert totals["parameter_authoring_calls"] == 3
    assert totals["authoring_calls"] == 1
    assert totals["arbitration_calls"] == 2


def test_check_answer_max_chars_and_any():
    checks = bench._check_answer(
        _turn(contains_any=["sunrise", "ocean"], max_chars=3), "ocean")
    assert [check["ok"] for check in checks] == [True, False]


def test_compare_reads_favor_from_both_dimensions():
    base = {"elapsed_ms": 1000, "cost_usd": 0.01}
    assert bench._compare({
        "jev": base, "baseline": {"elapsed_ms": 2000, "cost_usd": 0.02},
    })["favor_observed"] == "jev"
    assert bench._compare({
        "jev": {"elapsed_ms": 2000, "cost_usd": 0.02}, "baseline": base,
    })["favor_observed"] == "baseline"
    assert bench._compare({
        "jev": base, "baseline": {"elapsed_ms": 2000, "cost_usd": 0.001},
    })["favor_observed"] == "mixed"


def test_mark_soft_downgrades_matching_checks_only():
    checks = [{"name": "file matching 'report' contains '972'", "ok": False},
              {"name": "answer contains 'done'", "ok": False}]
    bench._mark_soft(checks, ("972",))
    assert checks[0].get("soft") is True
    assert "soft" not in checks[1]


# --- paired runner with fakes ----------------------------------------------


class FakeImage:
    async def close(self):
        pass


class FakeContainer:
    def __init__(self, files):
        self.files = dict(files)
        self.closed = False

    async def list_files(self):
        return sorted(self.files)

    async def read_file(self, name):
        return self.files.get(name)

    async def write_file(self, name, content):
        self.files[name] = content

    async def run_bash(self, command, timeout=None):
        return {"exit": 0, "output": command}

    async def close(self):
        self.closed = True


FAKE_SPECS = [
    ToolSpec(name="LIST_FILES", description="list"),
    ToolSpec(name="READ_FILE", description="read", needs_target=True, target_pool="files"),
    ToolSpec(name="WRITE_FILE", description="write", needs_target=True,
             target_pool="files", needs_text=True, text_instruction="w",
             write=True, mutates_workspace=True),
    ToolSpec(name="BASH", description="bash", needs_text=True,
             text_instruction="b", mutates_workspace=True),
]


class FakeProvider:
    def specs(self):
        return FAKE_SPECS

    def available(self, _workspace):
        return {"LIST_FILES", "READ_FILE", "WRITE_FILE", "BASH"}

    async def execute(self, name, _ctx):
        return {"status": "ready", "action": f"{name}"}


class ScriptedDriver:
    def __init__(self, name, decisions):
        self.name = name
        self._decisions = list(decisions)
        self._index = 0

    async def decide(self, _context):
        operation, target, content = self._decisions[self._index]
        self._index += 1
        decision = {
            "operation": operation,
            "target": target,
            "confidence": 0.9,
            "operation_probabilities": {operation: 1.0},
            "target_probabilities": {target: 1.0} if target else {},
            "target_confidence": 0.9 if target else None,
            "latency_ms": 1,
            "usage": {},
            "ledger_content": content,
        }
        return DriverProposal(decision=decision, base_decision=decision)


ALPHA = [
    ("WRITE_FILE", "NEW", "memo.txt\nharbor-2179"),
    ("ANSWER", None, "done"),
]
ALPHA_TURN2 = [
    ("READ_FILE", "memo.txt", None),
    ("ANSWER", None, "harbor-2179"),
]
BRAVO_WRONG = [("ANSWER", None, "a much longer wrong answer")]


def _fake_context(tmp_path, scripts):
    started_containers = []
    removed_volumes = []

    async def start_container(_image, lane, workspace_key=None, network_enabled=False):
        container = FakeContainer({"memo.txt": "harbor-2179"})
        started_containers.append((lane, workspace_key, container))
        return container

    async def remove_volume(name):
        removed_volumes.append(name)

    async def build_image():
        return FakeImage()

    def factory(lane):
        queue = list(scripts[lane])

        def make():
            return ScriptedDriver(lane if lane == "jev" else "plain", queue.pop(0))
        return make

    return bench.BenchContext(
        sessions_dir=tmp_path / "sessions",
        stamp="test",
        build_image=build_image,
        start_container=start_container,
        provider_for=lambda _container: FakeProvider(),
        driver_factories={lane: factory(lane) for lane in ("jev", "baseline")},
        remove_volume=remove_volume,
        runs_dir=tmp_path / "runs",
        log=lambda _line: None,
    ), started_containers, removed_volumes


def _suite_file(tmp_path):
    return _write_suite(tmp_path, {"suite": "test", "scenarios": [
        {
            "id": "alpha",
            "category": "multi_turn_continuity",
            "favor": "jev",
            "turns": [
                {"goal": "create memo", "answer_contains": ["done"],
                 "files": [{"name": "memo", "contains": "harbor-2179"}]},
                {"goal": "recall", "answer_contains": ["harbor-2179"],
                 "answer_max_chars": 40},
            ],
        },
        {
            "id": "bravo",
            "category": "writing_heavy",
            "favor": "plain",
            "turns": [{"goal": "say done", "answer_contains": ["done"]}],
        },
    ]})


def test_run_suite_pairs_lanes_persists_ledgers_and_cleans_volumes(tmp_path):
    suite_path = _suite_file(tmp_path)
    ctx, containers, volumes = _fake_context(tmp_path, {
        "jev": [ALPHA, ALPHA_TURN2, BRAVO_WRONG],
        "baseline": [ALPHA, ALPHA_TURN2, BRAVO_WRONG],
    })
    original_sessions_dir = sessions.DIR

    report = asyncio.run(bench.run_suite(
        bench.load_scenarios(suite_path), ctx, suite_path=suite_path))

    assert sessions.DIR == original_sessions_dir  # rebind restored
    assert [scenario["id"] for scenario in report["scenarios"]] == ["alpha", "bravo"]
    assert report["scenarios"][0]["status"] == "passed"
    assert report["scenarios"][1]["status"] == "failed"

    # both lanes got a container per scenario, all closed, volumes removed
    assert [lane for lane, _key, _c in containers] == ["jev", "baseline"] * 2
    assert all(container.closed for _lane, _key, container in containers)
    storage_ids = {key for _lane, key, _c in containers}
    expected = {f"jevloop-sandbox-workspace-{hashlib.sha256(key.encode()).hexdigest()[:24]}"
                for key in storage_ids}
    assert set(volumes) == expected and len(volumes) == len(expected)

    # per-lane ledgers persisted under the bench sessions dir
    saved = {p.name for p in (tmp_path / "sessions").glob("*.json")}
    assert saved == {f"bench-alpha-{ctx.stamp}--{lane}.json" for lane in ("jev", "baseline")} | {
        f"bench-bravo-{ctx.stamp}--{lane}.json" for lane in ("jev", "baseline")}

    # the jev lane's second turn continued the same ledger (turn count via metrics)
    alpha_jev = report["scenarios"][0]["lanes"]["jev"]
    assert [turn["final"] for turn in alpha_jev["turns"]] == ["completed", "completed"]
    assert alpha_jev["totals"]["steps"] == 4  # 2 turns x 2 steps

    verification = report["aggregate"]["verification"]
    assert verification["turns"] == 6  # (2 + 1) turns x 2 lanes
    assert verification["turns_passed"] == 4
    assert {row["id"]: row["expected"] for row in
            report["aggregate"]["expected_vs_observed"]} == {
        "alpha": "jev", "bravo": "plain"}

    markdown = bench.render_markdown(report)
    assert "## alpha" in markdown and "## bravo" in markdown
    assert "FAIL" in markdown and "failed checks:" in markdown


def test_run_suite_journals_turns_into_runstore_history(tmp_path):
    """Bench turns land in the dashboard's run history, replayable as paired
    conversations: one run file per turn holding both lanes' events."""
    from jevloop import runstore as runstore_module

    suite_path = _suite_file(tmp_path)
    ctx, _containers, _volumes = _fake_context(tmp_path, {
        "jev": [ALPHA, ALPHA_TURN2, BRAVO_WRONG],
        "baseline": [ALPHA, ALPHA_TURN2, BRAVO_WRONG],
    })
    original_runs_dir = runstore_module.DIR

    asyncio.run(bench.run_suite(
        bench.load_scenarios(suite_path), ctx, suite_path=suite_path))

    assert runstore_module.DIR == original_runs_dir  # rebind restored
    files = sorted((tmp_path / "runs").glob("*.jsonl"))
    assert [path.stem for path in files] == [
        "bench-alpha-test-t1", "bench-alpha-test-t2", "bench-bravo-test-t1"]

    with bench._runs_at(tmp_path / "runs"):
        runs = {path.stem: runstore_module.load(path.stem) for path in files}
        summaries = runstore_module.list_runs()
    alpha_t1 = runs["bench-alpha-test-t1"]
    events = [event for _seq, event in alpha_t1]
    assert events[0]["type"] == "meta"
    params = events[0]["params"]
    assert params["profile"] == "paired_shadow"
    assert params["session_id"] == "bench-alpha-test"
    assert params["source"] == "bench"
    assert params["scenario"] == "alpha"
    assert params["turn"] == 1
    assert params["suite"] == bench.suite_digest(suite_path)
    assert events[-1] == {"type": "done"}
    lanes_with_steps = {event["lane"] for event in events if event["type"] == "step"}
    assert lanes_with_steps == {"jev", "baseline"}
    finals = [event for event in events if event["type"] == "final"]
    assert {event["lane"] for event in finals} == {"jev", "baseline"}
    assert all(event["final"]["answer"] for event in finals)

    assert {run["run_id"] for run in summaries} >= set(runs)
    alpha_summary = next(run for run in summaries
                         if run["run_id"] == "bench-alpha-test-t1")
    assert alpha_summary["compare"] is True
    assert alpha_summary["session_id"] == "bench-alpha-test"
    assert alpha_summary["finished"] is True


def test_run_suite_reports_hard_file_failure(tmp_path):
    suite_path = _write_suite(tmp_path, _minimal_suite(
        turns=[{"goal": "create memo", "answer_contains": ["done"],
                "files": [{"name": "missing", "contains": "x"}]}]))
    ctx, _containers, _volumes = _fake_context(tmp_path, {
        "jev": [ALPHA],
        "baseline": [ALPHA],
    })
    report = asyncio.run(bench.run_suite(
        bench.load_scenarios(suite_path), ctx, suite_path=suite_path))
    scenario = report["scenarios"][0]
    assert scenario["status"] == "failed"
    assert any("no file matches" in check.get("detail", "")
               for turn in scenario["lanes"]["jev"]["turns"]
               for check in turn["checks"])


def test_write_report_outputs_json_and_markdown(tmp_path):
    suite_path = _suite_file(tmp_path)
    ctx, _containers, _volumes = _fake_context(tmp_path, {
        "jev": [ALPHA, ALPHA_TURN2, BRAVO_WRONG],
        "baseline": [ALPHA, ALPHA_TURN2, BRAVO_WRONG],
    })
    report = asyncio.run(bench.run_suite(
        bench.load_scenarios(suite_path), ctx, suite_path=suite_path))
    json_path, md_path = bench.write_report(report, tmp_path / "out")
    assert json.loads(json_path.read_text(encoding="utf-8"))["suite"]["digest"]
    assert md_path.read_text(encoding="utf-8").startswith("# JevLoop paired benchmark")


def test_run_suite_evaluates_bash_effect_checks(tmp_path):
    """Bash checks verify live effects (running services); the fake container
    echoes the command so the pass/fail split is fully deterministic."""
    suite_path = _write_suite(tmp_path, {"suite": "t", "scenarios": [{
        "id": "bashprobe", "category": "procedural", "favor": "either",
        "turns": [
            {"goal": "probe up",
             "bash": [{"command": "echo UP:8000:200", "contains": "UP:"}]},
            {"goal": "probe down",
             "bash": [{"command": "echo connection refused", "contains": "UP:"}]},
        ],
    }]})
    ctx, _containers, _volumes = _fake_context(tmp_path, {
        "jev": [[("ANSWER", None, "up")], [("ANSWER", None, "down")]],
        "baseline": [[("ANSWER", None, "up")], [("ANSWER", None, "down")]],
    })
    report = asyncio.run(bench.run_suite(
        bench.load_scenarios(suite_path), ctx, suite_path=suite_path))

    scenario = report["scenarios"][0]
    assert scenario["status"] == "failed"  # turn 2 probe found no service
    turn1 = scenario["lanes"]["jev"]["turns"][0]["checks"]
    turn2 = scenario["lanes"]["jev"]["turns"][1]["checks"]
    assert turn1[0]["ok"] is True
    assert turn2[0]["ok"] is False and "exit=0" in turn2[0]["detail"]


def test_turn_schema_rejects_malformed_bash_checks(tmp_path):
    with pytest.raises(ValueError, match="bash checks need"):
        bench.load_scenarios(_write_suite(tmp_path, _minimal_suite(
            turns=[{"goal": "x", "bash": [{"command": " ", "contains": "y"}]}])))


def test_turn_schema_accepts_injected_files(tmp_path):
    suite = _minimal_suite(turns=[{
        "goal": "read update",
        "inject_files": [{"name": "update.md", "content": "new facts"}],
    }])
    scenario = bench.load_scenarios(_write_suite(tmp_path, suite))[0]

    assert scenario.turns[0].inject_files == (
        {"name": "update.md", "content": "new facts"},
    )


def _mini_report(steps, direct, llm_cost, elapsed, turns_passed, denials=()):
    return {
        "aggregate": {
            "lanes": {
                "jev": {"steps": steps, "direct_jev_steps": direct,
                        "llm_assisted_jev_steps": steps - direct,
                        "llm_avoidance_rate": direct / steps if steps else None,
                        "escalations_upheld": 1, "escalations_overridden": 2,
                        "jev_cost_usd": 0.001, "llm_cost_usd": llm_cost,
                        "cost_usd": 0.001 + llm_cost, "elapsed_ms": elapsed},
                "baseline": {"steps": steps, "direct_jev_steps": 0,
                             "llm_assisted_jev_steps": steps,
                             "llm_avoidance_rate": 0.0,
                             "escalations_upheld": 0, "escalations_overridden": 0,
                             "jev_cost_usd": 0.0, "llm_cost_usd": llm_cost,
                             "cost_usd": llm_cost, "elapsed_ms": elapsed},
            },
            "verification": {"turns_passed": turns_passed},
        },
        "scenarios": [{"lanes": {lane: {"turns": [
            {"metrics": {"denials": list(denials)}}]} for lane in ("jev", "baseline")}}],
    }


def test_median_report_and_denial_counts():
    reports = [
        _mini_report(10, 2, 0.010, 40000, 8),
        _mini_report(20, 4, 0.020, 80000, 10,
                     denials=["Authored value echoes the operation name 'READ_FILE'"]),
        _mini_report(12, 2, 0.012, 50000, 6,
                     denials=[("Authored value is a malformed protocol fragment "
                               "(unterminated DSML element); nothing dispatched.")]),
    ]
    counts = [bench.run_denial_counts(report) for report in reports]
    assert counts == [{"dsml_fragment": 0, "operation_echo": 0},
                      {"dsml_fragment": 0, "operation_echo": 2},
                      {"dsml_fragment": 2, "operation_echo": 0}]
    median = bench.median_report(reports)
    assert median["runs"] == 3
    jev = median["lanes"]["jev"]
    assert jev["steps"] == 12 and jev["direct_jev_steps"] == 2
    assert jev["llm_avoidance_rate"] == 0.2
    assert median["turns_passed"] == 8
    assert median["denials"]["dsml_fragment"] == 0
    assert median["denials"]["operation_echo"] == 0
    markdown = bench.render_repeat_markdown({
        "suite": {
            "digest": "d" * 12,
            "runtime_digest": "r" * 12,
            "name": "t",
            "models": {"jev": "j", "llm": "l"},
        },
        "runs": reports,
        "median": median,
    })
    assert "**median**" in markdown and "dsml" in markdown


def test_gating_experiment_config_has_control_and_unique_arms():
    path = bench.DEFAULT_SCENARIOS.parent / "gating_experiment.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    arms = config["stage_1_ambiguity"]

    assert config["repeats_per_arm"] >= 3
    assert arms[0] == {
        "name": "confidence-only",
        "escalate_threshold": 0.5,
        "ambiguity_gate": None,
        "answer_progress_floor": None,
    }
    assert len({arm["name"] for arm in arms}) == len(arms)
    assert all((bench.DEFAULT_SCENARIOS.parent.parent / suite).is_file()
               for suite in config["suites"])
    assert len(bench.runtime_digest()) == 12
