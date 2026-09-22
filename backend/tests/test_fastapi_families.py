"""Offline suite/probe controls: no Docker, models, server or project writes."""

import importlib.util
import json
import os
import shlex
import subprocess
import sys
import textwrap
from pathlib import PurePosixPath

import pytest

from benchmarks.fastapi_families import seed_files, suite_payload

API_DEPS = all(importlib.util.find_spec(name) is not None for name in ("fastapi", "httpx"))
needs_api = pytest.mark.skipif(not API_DEPS, reason="FastAPI/httpx are not installed; no auto-install")

REFERENCE_APP = textwrap.dedent('''
    import json
    import os
    from pathlib import Path
    from fastapi import FastAPI, HTTPException, Query
    from pydantic import BaseModel, Field

    app = FastAPI()

    def data_path():
        return Path(os.environ.get("DATA_FILE", "data/items.json"))

    def read_items():
        return json.loads(data_path().read_text())

    def read_settings():
        return json.loads(Path(os.environ.get("SETTINGS_FILE", "config/settings.json")).read_text())

    @app.get("/")
    def hello():
        return {"message": "hello world"}

    @app.get("/items")
    def items(q: str = "", limit: int | None = Query(default=None, ge=1)):
        found = [item for item in read_items() if q.casefold() in item["name"].casefold()]
        count = limit if limit is not None else read_settings()["default_limit"]
        return found[:count]

    class Update(BaseModel):
        stock: int = Field(ge=0)

    @app.patch("/items/{item_id}")
    def update(item_id: int, body: Update):
        rows = read_items()
        for item in rows:
            if item["id"] == item_id:
                item["stock"] = body.stock
                data_path().write_text(json.dumps(rows))
                return item
        raise HTTPException(status_code=404)

    @app.get("/snapshot")
    def snapshot():
        settings, items = read_settings(), read_items()
        return {"revision": settings["revision"], "warehouse": settings["warehouse"],
                "item_count": len(items), "total_stock": sum(item["stock"] for item in items)}
''')

REFERENCE_TEST = textwrap.dedent('''
    from fastapi.testclient import TestClient
    from app import app

    def test_hello():
        response = TestClient(app).get("/")
        assert response.status_code == 200
        assert response.json() == {"message": "hello world"}
''')


def write_files(root, files):
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def snapshot(root):
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


def run_probe(probe, cwd):
    argv = shlex.split(probe["command"])
    assert argv[:3] == ["python", "-B", "-c"] and len(argv) == 4
    argv[0] = sys.executable
    result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=45, check=False,
                            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    return result, result.returncode == 0 and probe["contains"] in result.stdout


def test_load_fixed_suite_with_all_twenty_turns(tmp_path):
    from jevloop.bench import load_scenarios

    payload = suite_payload()
    path = tmp_path / "suite.json"
    path.write_text(json.dumps(payload, ensure_ascii=False))
    scenarios = load_scenarios(path)
    assert [(s.category, len(s.turns)) for s in scenarios] == [
        ("observation_reuse", 6), ("generation", 6), ("mixed", 8)]
    assert all(s.max_steps == 20 and s.favor == "either" for s in scenarios)
    assert len({s.id for s in scenarios}) == 3
    assert all(turn.bash for scenario in scenarios for turn in scenario.turns)


def test_families_and_payload_calls_do_not_share_mutable_state():
    first = suite_payload()
    original = suite_payload()
    reuse, generation, mixed = first["scenarios"]
    assert not any(turn.get("inject_files") for turn in generation["turns"])
    assert reuse["turns"][0]["inject_files"] == mixed["turns"][0]["inject_files"]
    reuse["turns"][0]["inject_files"][0]["content"] = "changed"
    assert mixed == original["scenarios"][2]
    assert suite_payload() == original


def test_injections_are_small_safe_relative_project_files():
    payload = suite_payload()
    injections = [(scenario["category"], index, turn["inject_files"])
                  for scenario in payload["scenarios"]
                  for index, turn in enumerate(scenario["turns"], 1)
                  if turn.get("inject_files")]
    assert [(category, index) for category, index, _ in injections] == [
        ("observation_reuse", 1), ("mixed", 1), ("mixed", 5)]
    for _, _, files in injections:
        assert len({item["name"] for item in files}) == len(files)
        assert sum(len(item["content"].encode()) for item in files) < 12_000
        for item in files:
            path = PurePosixPath(item["name"])
            assert not path.is_absolute() and ".." not in path.parts
            assert str(path) == item["name"] and "\\" not in item["name"]
            assert all(ord(char) >= 32 for char in item["name"])


def test_goals_do_not_prescribe_tool_selection_or_expose_observation_answers():
    payload = suite_payload()
    for scenario in payload["scenarios"]:
        for turn in scenario["turns"]:
            assert not any(tool in turn["goal"] for tool in (
                "READ_FILE", "WRITE_FILE", "LIST_FILES", "SEARCH_FILES", "BASH", "LLM_PARAMETERS"))
    observation = payload["scenarios"][0]["turns"]
    for turn in observation[1:]:
        assert not any(fact in turn["goal"] for fact in turn["answer_contains"] if len(fact) > 2)
    fresh = payload["scenarios"][2]["turns"][4]
    assert "同事刚修改" in fresh["goal"]
    assert not any(fact in fresh["goal"] for fact in fresh["answer_contains"] if len(fact) > 2)


def test_all_probes_compile_and_markers_are_unique():
    markers = []
    for scenario in suite_payload()["scenarios"]:
        for turn in scenario["turns"]:
            for probe in turn["bash"]:
                source = shlex.split(probe["command"])[-1]
                compile(source, "fixed-family-probe", "exec")
                assert source.rstrip().endswith(f"print({probe['contains']!r})")
                markers.append(probe["contains"])
    assert len(markers) == len(set(markers))


@pytest.mark.parametrize("family", [0, pytest.param(1, marks=needs_api),
                                   pytest.param(2, marks=needs_api)],
                         ids=["reuse", "generation", "mixed"])
def test_every_probe_accepts_reference_effects_without_mutating_project(tmp_path, family):
    scenario = suite_payload()["scenarios"][family]
    (tmp_path / ".gitkeep").touch()
    for index, turn in enumerate(scenario["turns"]):
        write_files(tmp_path, {item["name"]: item["content"]
                               for item in turn.get("inject_files", [])})
        if (family == 1 and index == 1) or (family == 2 and index == 2):
            if family == 1:
                write_files(tmp_path, seed_files())
            write_files(tmp_path, {"app.py": REFERENCE_APP, "test_app.py": REFERENCE_TEST})
        if family == 2 and index == 7:
            write_files(tmp_path, {"docs/handoff.md": (
                "cedar-v2 west-depot\nPaper Kite: 3\nOrion Thermos: 19\n"
                "amber-shelf OPS-417\n/snapshot reads current values\n")})
        before = snapshot(tmp_path)
        for probe in turn["bash"]:
            result, ok = run_probe(probe, tmp_path)
            assert ok, (scenario["category"], index + 1, result.stdout, result.stderr)
        assert snapshot(tmp_path) == before


def test_seed_checks_reject_changed_evidence(tmp_path):
    write_files(tmp_path, seed_files())
    (tmp_path / "config/settings.json").write_text("{}")
    probe = suite_payload()["scenarios"][0]["turns"][0]["bash"][0]
    result, ok = run_probe(probe, tmp_path)
    assert not ok and result.returncode != 0
    assert probe["contains"] not in result.stdout


@needs_api
@pytest.mark.parametrize("family,turn,old,new", [
    (1, 1, 'return {"message": "hello world"}', 'return {"message": "wrong"}'),
    (1, 3, 'if q.casefold() in item["name"].casefold()', 'if True'),
    (1, 4, 'def read_items():', 'import functools\n@functools.lru_cache()\ndef read_items():'),
    (1, 5, 'data_path().write_text(json.dumps(rows))', 'pass'),
    (1, 5, 'stock: int = Field(ge=0)', 'stock: int'),
    (2, 5, 'def read_settings():', 'import functools\n@functools.lru_cache()\ndef read_settings():'),
], ids=["wrong-hello", "missing-filter", "stale-data", "nonpersistent-patch",
        "negative-stock-accepted", "stale-settings"])
def test_api_probes_reject_wrong_behavior(tmp_path, family, turn, old, new):
    write_files(tmp_path, seed_files())
    assert old in REFERENCE_APP
    write_files(tmp_path, {"app.py": REFERENCE_APP.replace(old, new)})
    probe = suite_payload()["scenarios"][family]["turns"][turn]["bash"][0]
    before = snapshot(tmp_path)
    result, ok = run_probe(probe, tmp_path)
    assert not ok and result.returncode != 0, result.stdout + result.stderr
    assert "AssertionError" in result.stderr
    assert probe["contains"] not in result.stdout
    assert snapshot(tmp_path) == before


@needs_api
def test_test_creation_probe_rejects_no_collected_tests(tmp_path):
    write_files(tmp_path, seed_files())
    write_files(tmp_path, {"app.py": REFERENCE_APP, "test_app.py": "# no tests\n"})
    probe = suite_payload()["scenarios"][1]["turns"][2]["bash"][0]
    result, ok = run_probe(probe, tmp_path)
    assert not ok and result.returncode != 0
    assert probe["contains"] not in result.stdout


def test_handoff_probe_rejects_stale_facts(tmp_path):
    write_files(tmp_path, {"docs/handoff.md": "cedar-v1 east-depot Brass Compass 12"})
    probe = suite_payload()["scenarios"][2]["turns"][7]["bash"][0]
    result, ok = run_probe(probe, tmp_path)
    assert not ok and result.returncode != 0
    assert probe["contains"] not in result.stdout
