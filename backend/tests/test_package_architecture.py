"""AST dependency boundaries and stable paths after the backend package move.

These are architecture checks, not a sandbox: dynamic execution can evade
static import analysis. Imports nested in functions and conditional blocks are
intentionally inspected just like module-level imports.
"""

import ast
import json
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath

import pytest

BACKEND = Path(__file__).resolve().parents[1]
PACKAGE = BACKEND / "jevloop"
ROOT_FILES = {"__init__.py", "cli.py", "config.py", "paths.py"}
LAYERS = {"contracts", "context", "decision", "runtime", "tools", "storage", "apps", "evaluation"}
ALLOWED = {
    "contracts": {"contracts"},
    "context": {"contracts", "context"},
    "decision": {"decision", "contracts", "context", "config"},
    "runtime": {"runtime", "decision", "contracts", "context", "config"},
    "tools": {"tools", "contracts", "context", "paths"},
    "storage": {"storage", "context", "paths"},
    "apps": (LAYERS - {"evaluation"}) | {"config", "paths"},
    "evaluation": LAYERS | {"config", "paths"},
}
REQUIRED_MODULES = {
    "context/transcript.py", "context/projection.py", "context/observations.py",
    "context/state.py", "context/llm_context.py",
    "contracts/tools.py", "contracts/arguments.py", "contracts/policy.py",
    "contracts/authored.py", "contracts/schemas.py",
    "decision/model.py", "decision/questions.py", "decision/drivers.py",
    "decision/argument_helper.py", "decision/text_helper.py", "decision/escalation.py",
    "runtime/kernel.py", "runtime/metrics.py", "tools/sandbox.py", "tools/lark.py",
    "tools/adapter/lark_cli.py", "storage/sessions.py", "storage/runstore.py",
    "apps/server.py", "evaluation/bench.py", "evaluation/smoke.py",
}
LEGACY_ROOT_MODULES = {
    "transcript", "projection", "observations", "state", "llm_context",
    "arguments", "guardrails", "model", "questions", "drivers", "argument_helper",
    "text_helper", "escalation", "kernel", "metrics", "sessions", "runstore",
    "server", "bench", "smoke", "adapter",
}


def dependencies(relative_path, source):
    """Yield (line, resolved module) for absolute and relative AST imports."""
    relative_path = PurePosixPath(relative_path)
    package = ("jevloop", *relative_path.parts[:-1])
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            module = tuple((node.module or "").split(".")) if node.module else ()
            if node.level:
                keep = len(package) - node.level + 1
                if keep < 1:
                    yield node.lineno, "<outside-package>"
                    continue
                module = (*package[:keep], *module)
            base = ".".join(module)
            # Include imported members to catch `from jevloop import cli` and
            # `from ..tools import base`; the layer still comes from segment 2.
            for alias in node.names:
                yield node.lineno, base if alias.name == "*" else base + "." + alias.name


def violations(relative_path, source):
    parts = PurePosixPath(relative_path).parts
    layer = parts[0] if len(parts) > 1 else None
    failures = []
    for line, module in dependencies(relative_path, source):
        reason = None
        if module == "<outside-package>":
            reason = "relative import escapes the package"
        elif module == "jevloop" or module.startswith("jevloop."):
            components = module.split(".")
            target = components[1] if len(components) > 1 else None
            if layer and target == "cli":
                reason = "subpackage must not import the root CLI"
            elif target in LEGACY_ROOT_MODULES or module.startswith("jevloop.tools.base"):
                reason = "obsolete flat/adapter module import"
            elif layer and target not in ALLOWED[layer]:
                reason = f"{layer} must not depend on {target or 'package root'}"
            elif layer == "storage" and module.startswith("jevloop.context.llm_context"):
                reason = "storage must persist durable records, not the LLM projector"
        elif layer == "contracts":
            external = module.split(".")[0]
            if external not in sys.stdlib_module_names and external != "jsonschema":
                reason = "contracts only permit stdlib and jsonschema dependencies"
        if reason:
            failures.append(f"{relative_path}:{line}: {module}: {reason}")
    return failures


def storage_projection_calls(source):
    forbidden = {"messages", "llm_messages", "project_llm_messages"}
    return [node.lineno for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call) and (
                isinstance(node.func, ast.Attribute) and node.func.attr in forbidden
                or isinstance(node.func, ast.Name) and node.func.id in forbidden)]


def test_package_has_explicit_owners_and_no_root_forwarding_modules():
    actual_root = {path.name for path in PACKAGE.glob("*.py")}
    assert actual_root == ROOT_FILES, {
        "unexpected_root_modules": sorted(actual_root - ROOT_FILES),
        "missing_root_modules": sorted(ROOT_FILES - actual_root),
    }
    actual_modules = {path.relative_to(PACKAGE).as_posix() for path in PACKAGE.rglob("*.py")}
    assert REQUIRED_MODULES <= actual_modules, sorted(REQUIRED_MODULES - actual_modules)
    assert "tools/base.py" not in actual_modules
    assert not list((PACKAGE / "adapter").rglob("*.py")), "obsolete root adapter source remains"
    for layer in LAYERS | {"tools/adapter"}:
        assert (PACKAGE / layer / "__init__.py").is_file(), layer
    for module in actual_modules:
        parts = PurePosixPath(module).parts
        assert len(parts) == 1 or parts[0] in LAYERS, f"Unowned subpackage: {module}"


def test_every_backend_import_respects_its_layer_including_local_imports():
    failures = []
    for path in sorted(PACKAGE.rglob("*.py")):
        relative = path.relative_to(PACKAGE).as_posix()
        # Report unexpected owners in the dedicated shape test, not KeyError.
        if "/" in relative and relative.split("/")[0] not in ALLOWED:
            continue
        failures.extend(violations(relative, path.read_text(encoding="utf-8")))
    assert not failures, "\n".join(failures)


def test_package_initializers_are_inert_not_import_or_alias_registries():
    for path in sorted(PACKAGE.rglob("__init__.py")):
        for statement in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant) \
                    and isinstance(statement.value.value, str):
                continue
            if isinstance(statement, ast.Pass):
                continue
            if (path == PACKAGE / "__init__.py" and isinstance(statement, ast.Assign)
                    and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name)
                    and statement.targets[0].id == "__version__"
                    and isinstance(statement.value, ast.Constant)
                    and isinstance(statement.value.value, str)):
                continue
            pytest.fail(f"{path.relative_to(PACKAGE)}:{statement.lineno}: "
                        "package initializer must be inert (docstring/version only)")


def test_storage_never_persists_the_llm_projection():
    directory = PACKAGE / "storage"
    assert directory.is_dir(), "storage migration has not landed"
    for path in directory.rglob("*.py"):
        lines = storage_projection_calls(path.read_text(encoding="utf-8"))
        assert not lines, f"{path.relative_to(PACKAGE)}:{lines}: persist dump(), not LLM messages"
    path = directory / "sessions.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    save = next(node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "save")
    assert any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
               and node.func.attr == "dump" for node in ast.walk(save)), "sessions.save must use dump()"


@pytest.mark.parametrize("path,source", [
    ("context/state.py", "def lazy():\n    from ..decision.model import choose\n"),
    ("decision/model.py", "if False:\n    import jevloop.tools.sandbox as provider\n"),
    ("runtime/kernel.py", "def lazy():\n    from ..storage import sessions\n"),
    ("tools/adapter/lark_cli.py", "from ...apps.server import serve\n"),
    ("storage/sessions.py", "from ..context.llm_context import project_llm_messages as view\n"),
    ("contracts/policy.py", "from jsonschema import validate\nimport httpx\n"),
    ("context/transcript.py", "from jevloop import cli as entry\n"),
    ("evaluation/bench.py", "def run():\n    from .. import cli\n"),
    ("apps/server.py", "import jevloop.cli\n"),
    ("apps/server.py", "from jevloop import model\n"),
    ("apps/server.py", "from ..evaluation import bench\n"),
    ("tools/lark.py", "from .base import ToolSpec\n"),
    ("contracts/tools.py", "from ... import escaped\n"),
])
def test_architecture_checker_rejects_forbidden_import_forms(path, source):
    assert violations(path, source)


@pytest.mark.parametrize("path,source", [
    ("contracts/policy.py", "from dataclasses import dataclass\nfrom .tools import ToolSpec\n"),
    ("contracts/arguments.py", "from jsonschema import Draft202012Validator\n"),
    ("context/projection.py", "def restore():\n    from .state import Workspace\n"),
    ("decision/model.py", "from ..contracts import schemas\nfrom .. import config\n"),
    ("runtime/kernel.py", "from ..decision.drivers import DriverContext\n"),
    ("tools/adapter/lark_cli.py", "from ...contracts.policy import InvalidProposal\n"),
    ("storage/sessions.py", "from ..context.transcript import Transcript\nfrom ..paths import BACKEND_ROOT\n"),
    ("apps/server.py", "from ..runtime.kernel import RuntimeKernel\nfrom ..tools.sandbox import SandboxTools\n"),
    ("evaluation/bench.py", "from ..apps.server import cache_scope\n"),
    ("cli.py", "from .evaluation import smoke\n"),
])
def test_architecture_checker_accepts_owned_dependency_forms(path, source):
    assert violations(path, source) == []


@pytest.mark.parametrize("method", ["messages", "llm_messages", "project_llm_messages"])
def test_storage_checker_rejects_projection_calls_inside_save(method):
    assert storage_projection_calls(f"def save(ledger):\n    return ledger.{method}()\n") == [2]
    assert storage_projection_calls("def save(ledger):\n    return ledger.dump()\n") == []


def run_path_probe(extra_env=None):
    environment = {key: value for key, value in os.environ.items()
                   if key not in {"JEVLOOP_SESSIONS_DIR", "JEVLOOP_RUNS_DIR"}}
    environment.update(PYTHONPATH=str(BACKEND), PYTHONDONTWRITEBYTECODE="1")
    environment.update(extra_env or {})
    source = """
import json
from jevloop.paths import BACKEND_ROOT, REPO_ROOT
from jevloop.evaluation.bench import DEFAULT_SCENARIOS
from jevloop.apps.server import WEB_DIST
from jevloop.tools.sandbox import DOCKER_DIR, DEFAULT_SEED
from jevloop.storage import sessions, runstore
print(json.dumps({name: str(path.resolve()) for name, path in {
    'backend': BACKEND_ROOT, 'repo': REPO_ROOT, 'scenarios': DEFAULT_SCENARIOS,
    'web': WEB_DIST, 'docker': DOCKER_DIR, 'seed': DEFAULT_SEED,
    'sessions': sessions.DIR, 'runs': runstore.DIR,
}.items()}))
"""
    result = subprocess.run([sys.executable, "-B", "-c", source], cwd=BACKEND,
                            env=environment, capture_output=True, text=True,
                            timeout=30, check=False)
    assert result.returncode == 0, result.stderr[-3000:]
    return json.loads(result.stdout)


def test_moved_modules_keep_original_repository_and_artifact_locations():
    expected = {
        "backend": BACKEND, "repo": BACKEND.parent,
        "scenarios": BACKEND / "benchmarks/scenarios.json",
        "web": BACKEND.parent / "frontend/dist",
        "docker": BACKEND / "docker/sandbox", "seed": BACKEND / "docker/sandbox/seed",
        "sessions": BACKEND / "artifacts/sessions", "runs": BACKEND / "artifacts/runs",
    }
    assert run_path_probe() == {name: str(path.resolve()) for name, path in expected.items()}
    assert expected["scenarios"].is_file()
    assert (expected["docker"] / "Dockerfile").is_file()
    assert (expected["seed"] / ".gitkeep").is_file()
    assert (BACKEND.parent / "frontend").is_dir()


def test_storage_path_overrides_remain_supported_without_creating_directories(tmp_path):
    session_dir, runs_dir = tmp_path / "custom-sessions", tmp_path / "custom-runs"
    paths = run_path_probe({"JEVLOOP_SESSIONS_DIR": str(session_dir),
                            "JEVLOOP_RUNS_DIR": str(runs_dir)})
    assert paths["sessions"] == str(session_dir.resolve())
    assert paths["runs"] == str(runs_dir.resolve())
    assert not session_dir.exists() and not runs_dir.exists()
