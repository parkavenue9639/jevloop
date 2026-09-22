"""Canonical sandbox arguments and grounded, bounded observation evidence."""

import asyncio
import importlib.util
from pathlib import Path

import pytest

from jevloop.state import Workspace
from jevloop.tools.base import ToolContext
from jevloop.tools.sandbox import SPECS, DockerSandboxContainer, SandboxTools, validate_arguments


@pytest.fixture
def fs(tmp_path):
    path = Path(__file__).resolve().parents[1] / "docker" / "sandbox" / "sandboxfs.py"
    spec = importlib.util.spec_from_file_location("observation_sandboxfs", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.WORKSPACE = tmp_path
    return module


class Runtime:
    def __init__(self, fs):
        self.fs = fs
        self.calls = []

    async def list_entries(self, path, offset, limit):
        self.calls.append(("list", path, offset, limit))
        return self.fs.list_entries(path, offset, limit)

    async def read_range(self, path, offset, limit):
        self.calls.append(("read", path, offset, limit))
        try:
            return self.fs.read_range(path, offset, limit)
        except OSError as error:
            return {"status": "error", "error": type(error).__name__, "reason": str(error)}

    async def search_files(self, path, pattern, glob, limit):
        self.calls.append(("search", path, pattern, glob, limit))
        return self.fs.search_files(path, pattern, glob, limit)

    async def write_file(self, path, content):
        self.calls.append(("write", path, content))
        target = self.fs.confined(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    async def run_bash(self, command):
        self.calls.append(("bash", command))
        return {"exit": 0, "output": "invented.txt\nfolder/\nnot a file listing"}


def invoke(runtime, operation, arguments, workspace=None, **legacy):
    return asyncio.run(SandboxTools(runtime).execute(operation, ToolContext(
        workspace=workspace or Workspace(), arguments=arguments, **legacy)))


def test_canonical_smoke_assertions_cover_the_provider_contract(fs):
    from jevloop.smoke import check_canonical_tools

    class SmokeRuntime(Runtime):
        async def read_file(self, path):
            return self.fs.confined(path).read_text()

    runtime = SmokeRuntime(fs)
    checks = asyncio.run(check_canonical_tools(runtime))
    assert checks == ["write_path_content", "list_scoped_pagination", "read_line_range",
                      "read_multiple", "search_references", "bash_raw_no_references"]
    assert {call[0] for call in runtime.calls} == {"write", "list", "read", "search", "bash"}


def test_all_tools_available_without_observed_candidates():
    assert SandboxTools().available(Workspace()) == {spec.name for spec in SPECS}
    for spec in SPECS:
        assert spec.parameters["additionalProperties"] is False
        assert callable(spec.argument_validator)
    read = next(spec for spec in SPECS if spec.name == "READ_FILE")
    assert not read.needs_target
    assert read.target_parameter == "path"


def test_listing_is_scoped_page_with_bounded_typed_references(fs):
    folder = fs.WORKSPACE / "folder"
    folder.mkdir()
    (folder / "hidden-in-child.txt").write_text("secret")
    for i in range(30):
        (fs.WORKSPACE / f"a{i:02}.txt").write_text("x")
    runtime = Runtime(fs)
    result = invoke(runtime, "LIST_FILES", {"limit": 100})
    obs = result["observation"]
    assert len(obs["references"]) == 20
    assert obs["references_truncated"] is True
    assert obs["truncated"] is True
    assert "hidden-in-child" not in obs["evidence"]
    page = invoke(runtime, "LIST_FILES", {"offset": 30, "limit": 1})["observation"]
    assert page["references"] == [{"kind": "directory", "value": "folder", "label": "folder"}]
    child = invoke(runtime, "LIST_FILES", {"path": "folder"})["observation"]
    assert child["references"][0]["value"] == "folder/hidden-in-child.txt"
    assert child["scope"] == "folder"


def test_listing_scan_budget_is_explicit(fs, monkeypatch):
    monkeypatch.setattr(fs, "MAX_SCAN_ENTRIES", 3)
    for i in range(5):
        (fs.WORKSPACE / str(i)).write_text("x")
    result = fs.list_entries(limit=2)
    assert result["scan_truncated"] is True
    assert result["truncated"] is True
    assert len(result["entries"]) == 2


def test_open_read_uses_zero_based_lines_and_preserves_unicode(fs):
    (fs.WORKSPACE / "直接.txt").write_text("首行\n第二行🙂\n第三行\n")
    runtime = Runtime(fs)
    result = invoke(runtime, "READ_FILE", {"path": "直接.txt", "offset": 1, "limit": 1})
    assert result["file_results"][0]["content"] == "第二行🙂\n"
    assert result["observation"]["unit"] == "lines"
    assert result["observation"]["truncated"] is True
    assert result["file_results"][0]["next_offset"] == 2
    assert runtime.calls == [("read", "直接.txt", 1, 1)]


def test_multi_read_caps_output_and_reports_missing(fs):
    (fs.WORKSPACE / "a").write_text("a" * 6000)
    (fs.WORKSPACE / "b").write_text("b" * 6000)
    result = invoke(Runtime(fs), "READ_FILE", {"path": ["a", "missing", "b"]})
    assert result["partial"] is True
    assert sum(item.get("returned_chars", 0) for item in result["file_results"]) == 9000
    assert result["observation"]["truncated"] is True
    assert result["read_files"] == ["a", "b"]


def test_search_produces_only_observed_file_references(fs):
    (fs.WORKSPACE / "a.md").write_text("nothing\nneedle 中文\n")
    (fs.WORKSPACE / "b.txt").write_text("needle")
    runtime = Runtime(fs)
    result = invoke(runtime, "SEARCH_FILES", {"pattern": "needle", "glob": "*.md"})
    assert result["matches"] == [{"path": "a.md", "line": 2, "snippet": "needle 中文",
                                  "snippet_truncated": False}]
    assert result["observation"]["references"] == [{"kind": "file", "value": "a.md", "label": "a.md"}]
    assert result["observation"]["pattern"] == "needle"
    assert result["observation"]["truncated"] is False
    no_match = invoke(runtime, "SEARCH_FILES", {"pattern": "absent"})
    assert no_match["status"] == "ready"
    assert no_match["matches"] == []
    invalid = invoke(runtime, "SEARCH_FILES", {"pattern": "["})
    assert invalid["status"] == "failed"
    assert "invalid_regex" in invalid["reason"]
    assert len(runtime.calls) == 2  # malformed regex never dispatches


def test_search_enforces_scan_and_output_budgets(fs, monkeypatch):
    (fs.WORKSPACE / "a").write_text("needle\n" * 100)
    monkeypatch.setattr(fs, "MAX_SEARCH_BYTES", 20)
    result = fs.search_files(pattern="needle", limit=100)
    assert result["truncated"] is True
    assert result["scanned_bytes"] <= 20
    monkeypatch.setattr(fs, "MAX_SEARCH_BYTES", 10000)
    monkeypatch.setattr(fs, "MAX_SEARCH_OUTPUT", 220)
    bounded = fs.search_files(pattern="needle", limit=100)
    assert bounded["truncated"] is True
    assert len(bounded["matches"]) <= 2
    invalid = fs.search_files(pattern="[")
    assert invalid["status"] == "error" and invalid["error"] == "invalid_regex"


@pytest.mark.parametrize(("operation", "arguments"), [
    ("READ_FILE", {"path": "../escape"}),
    ("READ_FILE", {"path": "/etc/passwd"}),
    ("READ_FILE", {"path": "a\\b"}),
    ("READ_FILE", {"path": "a\nb"}),
    ("READ_FILE", {"path": ["a", "a"]}),
    ("READ_FILE", {"path": "a", "offset": -1}),
    ("LIST_FILES", {"path": ".", "limit": 201}),
    ("SEARCH_FILES", {"pattern": "a", "recursive": True}),
    ("WRITE_FILE", {"path": "../x", "content": "text"}),
    ("BASH", {"command": "   "}),
])
def test_invalid_arguments_fail_before_execution(fs, operation, arguments):
    runtime = Runtime(fs)
    result = invoke(runtime, operation, arguments)
    assert result["status"] == "failed"
    assert result["effect_proof"] == "pre_effect"
    assert runtime.calls == []


def test_canonical_write_preserves_nested_path_and_content(fs):
    runtime = Runtime(fs)
    result = invoke(runtime, "WRITE_FILE", {"path": "nested/new.md", "content": "body\n"},
                    target="ignored.md", text="ignored legacy text")
    assert runtime.calls == [("write", "nested/new.md", "body\n")]
    assert result["changed_files"] == ["nested/new.md"]
    assert (fs.WORKSPACE / "nested/new.md").read_text() == "body\n"


def test_bash_stdout_is_evidence_not_a_file_catalog(fs):
    runtime, workspace = Runtime(fs), Workspace()
    result = invoke(runtime, "BASH", {"command": "printf anything"}, workspace)
    assert result["observation"]["evidence"].startswith("invented.txt")
    assert result["observation"]["references"] == []
    assert workspace.files == {}


def test_symlink_escape_rejected_and_not_listed(fs, tmp_path):
    link = fs.WORKSPACE / "outside"
    link.symlink_to(tmp_path.parent)
    assert fs.list_entries()["entries"] == []
    with pytest.raises(ValueError, match="escapes"):
        fs.read_range("outside/file")
    assert fs.search_files(pattern="x")["matches"] == []


def test_canonical_range_never_falls_back_to_legacy_runtime():
    class Legacy:
        async def read_file(self, _path):
            raise AssertionError("must not silently ignore range arguments")

    with pytest.raises(AttributeError, match="read_range"):
        invoke(Legacy(), "READ_FILE", {"path": "a", "offset": 3, "limit": 1})
    assert validate_arguments("LIST_FILES", {}) == {"path": ".", "offset": 0, "limit": 100}


def test_path_normalization_happens_before_freezing_arguments():
    assert validate_arguments("READ_FILE", {"path": "./folder//a"})["path"] == "folder/a"
    with pytest.raises(ValueError, match="unique normalized"):
        validate_arguments("READ_FILE", {"path": ["a", "./a"]})


def test_unbindable_observed_filename_remains_evidence(fs):
    (fs.WORKSPACE / "not\na\nbinding").write_text("data")
    result = invoke(Runtime(fs), "LIST_FILES", {})
    assert "not\na\nbinding" in result["observation"]["evidence"]
    assert result["observation"]["references"] == []
    assert result["observation"]["references_rejected"] == 1


def test_long_line_cap_does_not_claim_lossless_next_page(fs):
    (fs.WORKSPACE / "a").write_text("界" * 30000 + "\n")
    result = fs.read_range("a", limit=1)
    assert result["content"] == "界" * 20000
    assert result["line_truncated"] is True
    assert result["next_offset"] is None


def test_regex_execution_deadline_is_an_error_not_zero_matches(fs, monkeypatch):
    (fs.WORKSPACE / "a").write_text("a" * 1000 + "!")
    original_timer = fs.signal.setitimer
    monkeypatch.setattr(fs.signal, "setitimer",
                        lambda which, seconds: original_timer(which, min(seconds, 0.03)))
    result = fs.search_files(pattern="(a+)+$")
    assert result["error"] == "search_timeout"
    assert result["truncated"] is True


def test_range_transport_preserves_explicit_parameters(monkeypatch):
    import json

    runtime = DockerSandboxContainer("test")
    calls = []

    async def execute(argv, **kwargs):
        calls.append((argv, kwargs))
        return 0, b'{"content":"ok","truncated":false}', b""

    monkeypatch.setattr(runtime, "_exec", execute)
    result = asyncio.run(runtime.read_range("nested/a", offset=17, limit=3))
    assert result["content"] == "ok"
    assert calls[0][0][:2] == ["sandboxfs", "read-range"]
    assert json.loads(calls[0][0][2]) == {"name": "nested/a", "offset": 17, "limit": 3}
    assert calls[0][1]["timeout"] == 10
