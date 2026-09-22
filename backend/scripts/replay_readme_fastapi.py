"""Rerun the six README goals through the original paired dashboard runtime.

Verify original event-file hashes before using their whitelisted run parameters.
Use fresh session volumes/ledgers; never touch the historical run or session.
Completion checks match the README's answered-turn claim, not semantic grading.
"""

import argparse
import asyncio
import hashlib
import json
import os
import uuid
from datetime import datetime
from pathlib import Path

from jevloop import bench, runstore, sessions
from jevloop.cli import load_env_file
from jevloop.server import CACHE_POLICY_ISOLATED, Dashboard, RunState, _cache_scope
from jevloop.tools.sandbox import workspace_volume_name

ROOT = Path(__file__).resolve().parents[2]
PARAMETERS = ("goal", "profile", "max_steps", "max_writes", "sandbox_network",
              "min_confidence", "escalate_threshold", "ambiguity_gate",
              "answer_progress_floor", "step_pause")


def source_turns():
    manifest = json.loads((ROOT / "docs/evidence/fastapi-6turn-20260922/manifest.json").read_text())
    turns = []
    for source in manifest["source_files"]:
        path = ROOT / "backend/artifacts/runs" / source["file"]
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != source["sha256"]:
            raise ValueError(f"Historical evidence hash mismatch: {path.name}")
        events = [json.loads(line)["event"] for line in raw.splitlines()]
        params = next(event["params"] for event in events if event["type"] == "meta")
        if params.get("profile") != "paired_shadow" or params.get("step_pause"):
            raise ValueError("Expected unpaused paired-shadow README source")
        turns.append({"source_run": source["run_id"],
                      "params": {key: params[key] for key in PARAMETERS if key in params}})
    if len(turns) != 6:
        raise ValueError("README evidence must contain exactly six turns")
    return turns


def lane_record(state, lane, goal, timed_out):
    events = [event for _, event in state.log if event.get("lane") == lane]
    finals = [event for event in events if event["type"] == "final"]
    measurements = [event["metrics"] for event in events if event["type"] == "metrics"]
    metrics = measurements[-1] if measurements else {}
    steps = [event["step"] for event in events if event["type"] == "step"]
    terminal = next((step["final"] for step in reversed(steps) if "final" in step), None)
    answer = finals[-1]["final"].get("answer", "") if finals else ""
    answered = terminal == "completed" and bool(answer)
    direct = [step for step in steps
              if step.get("decision", {}).get("operation") not in {None, "ANSWER", "DONE"}
              and step.get("outcome", {}).get("effect_disposition") == "SUCCEEDED"
              and step.get("model_calls")
              and all(call.get("kind") == "jev_decision" for call in step["model_calls"])]
    return {"goal": goal, "answer": answer,
            "final": terminal or ("timeout" if timed_out else "error"),
            "elapsed_ms": metrics.get("elapsed_ms", 0), "metrics": metrics,
            "hard_failures": int(not answered), "soft_failures": 0,
            "checks": [{"name": "completed with an answer (not semantic verification)", "ok": answered}],
            "successful_direct_tools": len(direct) if lane == "jev" else 0,
            "errors": [event.get("message") for event in events if event["type"] == "error"],
            "routing_samples": [{"operation": step.get("decision", {}).get("operation"),
                                 "status": step.get("outcome", {}).get("status"),
                                 "error": step.get("outcome", {}).get("error")}
                                for step in steps if "final" not in step]}


async def rerun(turns, out_dir, timeout):
    out_dir.mkdir(parents=True, exist_ok=False)
    original_runs, original_sessions = runstore.DIR, sessions.DIR
    runstore.DIR, sessions.DIR = out_dir / "runs", out_dir / "sessions"
    session_id = "readme-fastapi-" + uuid.uuid4().hex[:12]
    dashboard = Dashboard()
    records = {lane: [] for lane in bench.LANES}
    replay_runs = []
    source_digest = hashlib.sha256(json.dumps(turns, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
    started = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        for index, source in enumerate(turns, 1):
            params = {**source["params"], "session_id": session_id,
                      "cache_policy": CACHE_POLICY_ISOLATED,
                      "cache_scopes": {lane: _cache_scope(session_id, lane) for lane in bench.LANES}}
            run_id = uuid.uuid4().hex[:12]
            state = RunState(run_id, params)
            state.emit({"type": "meta", "params": params,
                        "source_run": source["source_run"], "created_at": started})
            print(f"turn {index}/6: {params['goal']}", flush=True)
            timed_out = False
            try:
                await asyncio.wait_for(dashboard._execute_async(state), timeout)
            except TimeoutError:
                timed_out = True
            replay_runs.append({"run_id": run_id, "source_run": source["source_run"]})
            for lane in bench.LANES:
                record = lane_record(state, lane, params["goal"], timed_out)
                records[lane].append(record)
                print(f"  {lane}: {record['final']}, steps="
                      f"{record['metrics'].get('routing', {}).get('decision_steps', 0)}, "
                      f"elapsed={record['elapsed_ms']}ms", flush=True)
        totals = {lane: bench._lane_totals(records[lane]) for lane in bench.LANES}
        passed = all(not record["hard_failures"] for lane in records.values() for record in lane)
        scenario = {"id": "readme-fastapi-six-turn", "category": "procedural_build",
                    "favor": "jev", "description": "Exact README session goals, completion-only checks",
                    "status": "passed" if passed else "failed",
                    "lanes": {lane: {"turns": records[lane], "totals": totals[lane]} for lane in bench.LANES},
                    "comparison": bench._compare(totals)}
        report = {"suite": {"path": "docs/evidence/fastapi-6turn-20260922/manifest.json",
                             "digest": source_digest, "runtime_digest": bench.runtime_digest(),
                             "name": "readme-fastapi-6turn-exact-session", "scenarios": 1, "turns": 6,
                             "started_at": started, "models": {
                                 "jev": os.environ.get("TYPESAFE_MODEL", "jev-latest"),
                                 "llm": os.environ.get("TEXT_MODEL", "deepseek-chat")},
                             "source_parameters": turns, "lane_schedule": "parallel within each turn",
                             "turn_watchdog_seconds": timeout},
                  "session_id": session_id, "replay_runs": replay_runs,
                  "image_id": dashboard.image.image_id if dashboard.image else None,
                  "scenarios": [scenario], "aggregate": bench._aggregate([scenario])}
        bench.write_report(report, out_dir)
        # Correct the standard bench renderer's scheduling text for dashboard replay.
        markdown = bench.render_markdown(report).replace(
            "Lanes run sequentially per turn;", "Lanes run concurrently per turn (original dashboard protocol);")
        (out_dir / "report.md").write_text(markdown + "\n\nChecks mean answered turns only, not semantic correctness.\n")
        print(json.dumps({"report": str(out_dir / "report.json"), "totals": totals,
                          "verification": report["aggregate"]["verification"]}), flush=True)
        return passed
    finally:
        for lane in bench.LANES:
            await bench._remove_volume(workspace_volume_name(f"{session_id}--{lane}"))
        if dashboard.image:
            await dashboard.image.close()
        runstore.DIR, sessions.DIR = original_runs, original_sessions


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--turn-timeout", type=float, default=600)
    options = parser.parse_args()
    load_env_file()
    if not all(os.environ.get(key) for key in ("TYPESAFE_API_KEY", "DEEPSEEK_API_KEY")):
        raise SystemExit("Missing model credentials")
    raise SystemExit(0 if asyncio.run(rerun(source_turns(), options.out, options.turn_timeout)) else 1)
