"""Real confined image capture with deterministic offline visual observations."""

import asyncio
import base64
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from jevloop.contracts.tools import ToolContext
from jevloop.storage import assets
from jevloop.tools.sandbox import SPECS, DockerSandboxContainer, SandboxTools, validate_arguments


def png(color="red"):
    output = io.BytesIO()
    Image.new("RGB", (24, 16), color).save(output, format="PNG")
    return output.getvalue()


@pytest.fixture
def fs(tmp_path, monkeypatch):
    location = Path(__file__).resolve().parents[1] / "docker" / "sandbox" / "sandboxfs.py"
    spec = importlib.util.spec_from_file_location("image_sandboxfs", location)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.WORKSPACE = tmp_path / "workspace"
    module.WORKSPACE.mkdir()
    monkeypatch.setenv("JEVLOOP_ASSETS_DIR", str(tmp_path / "assets"))
    return module


class Runtime:
    def __init__(self, fs):
        self.fs, self.calls = fs, []

    async def read_image(self, path):
        self.calls.append(path)
        try:
            return self.fs.read_image(path)
        except (OSError, ValueError) as error:
            return {"status": "error", "error": type(error).__name__, "reason": str(error)}


MOCK_VISUAL_OBSERVATION = "Offline mock visual observation: a solid-color rectangular fixture."


async def mock_visual_read(part):
    assert assets.resolve_image(part) == part
    return MOCK_VISUAL_OBSERVATION


def invoke(runtime, source, workspace=None, *, visual_read=mock_visual_read, **arguments):
    workspace = workspace or SimpleNamespace(files={}, images={})
    return asyncio.run(SandboxTools(runtime).execute("VIEW_IMAGE", ToolContext(
        workspace=workspace, arguments={"source": source, **arguments}, visual_read=visual_read)))


def test_view_image_captures_binary_and_preserves_real_reference(fs):
    (fs.WORKSPACE / "sample.png").write_bytes(png())
    workspace = SimpleNamespace(files={}, images={})
    result = invoke(Runtime(fs), "sample.png", workspace, detail="high")
    assert result["status"] == "ready"
    image = result["images"][0]
    assert image["detail"] == "high" and image["width"] == 24
    assert result["observation"]["references"] == [
        {"kind": "file", "value": "sample.png", "label": "sample.png"}]
    assert MOCK_VISUAL_OBSERVATION in result["observation"]["evidence"]
    assert "data" not in json.dumps(result)
    assert workspace.files == {"sample.png": "sample.png"}
    (fs.WORKSPACE / "sample.png").write_bytes(png("blue"))
    second = invoke(Runtime(fs), "sample.png")["images"][0]
    assert second["asset_id"] != image["asset_id"]
    assert assets.resolve_image(image) == image


def test_asset_requires_observed_reference_and_has_no_fake_file(fs):
    image = assets.ingest_image(png(), "attachment")
    source = "asset:" + image["asset_id"]
    assert invoke(None, source)["status"] == "failed"
    workspace = SimpleNamespace(files={}, images={image["asset_id"]: image})
    result = invoke(None, source, workspace)
    assert result["status"] == "ready" and result["images"] == [image]
    assert result["observation"]["references"] == []
    assert MOCK_VISUAL_OBSERVATION in result["observation"]["evidence"]
    assert workspace.files == {}


@pytest.mark.parametrize("asset_source", [False, True])
def test_visual_read_receives_exact_captured_part_once(fs, asset_source):
    (fs.WORKSPACE / "sample.png").write_bytes(png())
    workspace = SimpleNamespace(files={}, images={})
    source = "sample.png"
    if asset_source:
        image = assets.ingest_image(png(), "attachment")
        workspace.images[image["asset_id"]] = image
        source = "asset:" + image["asset_id"]
    calls = []

    async def visual_read(part):
        calls.append(dict(part))
        assert assets.resolve_image(part) == part  # Persisted before any visual read.
        return "Offline fixture observation from the selected image."

    result = invoke(Runtime(fs), source, workspace, visual_read=visual_read, detail="high")
    assert result["status"] == "ready"
    assert calls == result["images"] and len(calls) == 1
    assert calls[0]["detail"] == "high"
    assert "Offline fixture observation from the selected image." in result["observation"]["evidence"]


def test_missing_visual_reader_does_not_report_capture_as_understanding(fs):
    (fs.WORKSPACE / "sample.png").write_bytes(png())
    result = invoke(Runtime(fs), "sample.png", visual_read=None)
    assert result["status"] == "failed"
    assert result.get("reason")
    assert not result.get("observation", {}).get("evidence")


@pytest.mark.parametrize("observation", ["", "   \n\t", None, {"text": "not a string"}])
def test_visual_reader_requires_nonempty_text(fs, observation):
    (fs.WORKSPACE / "sample.png").write_bytes(png())
    calls = []

    async def visual_read(part):
        calls.append(part)
        return observation

    result = invoke(Runtime(fs), "sample.png", visual_read=visual_read)
    assert result["status"] == "failed"
    assert result.get("reason")
    assert len(calls) == 1
    assert not result.get("observation", {}).get("evidence")


@pytest.mark.parametrize("source", ["../escape", "/etc/passwd", "https://example.com/a.png",
                                    "file:///tmp/a.png", "asset:missing", "foo\\bar", "a\nb"])
def test_source_validation_precedes_runtime(fs, source):
    runtime = Runtime(fs)
    calls = []

    async def visual_read(part):
        calls.append(part)
        return MOCK_VISUAL_OBSERVATION

    assert invoke(runtime, source, visual_read=visual_read)["status"] == "failed"
    assert not runtime.calls
    assert not calls


def test_invalid_missing_and_over_budget_files(fs, monkeypatch):
    runtime = Runtime(fs)
    calls = []

    async def visual_read(part):
        calls.append(part)
        return MOCK_VISUAL_OBSERVATION

    (fs.WORKSPACE / "text").write_text("not an image")
    assert invoke(runtime, "text", visual_read=visual_read)["status"] == "failed"
    assert invoke(runtime, "missing", visual_read=visual_read)["status"] == "blocked"
    monkeypatch.setattr(fs, "MAX_IMAGE_BYTES", 10)
    assert invoke(runtime, "text", visual_read=visual_read)["status"] == "failed"
    assert not calls


def test_binary_helper_rejects_symlinks_and_nonfiles(fs, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.png").write_bytes(png())
    (fs.WORKSPACE / "link.png").symlink_to(outside / "a.png")
    (fs.WORKSPACE / "dir").symlink_to(outside, target_is_directory=True)
    for source in ("link.png", "dir/a.png", "."):
        with pytest.raises((OSError, ValueError)):
            fs.read_image(source)


def test_helper_structured_command_and_container_argv(fs, capsys):
    (fs.WORKSPACE / "a.png").write_bytes(png())
    assert fs.main(["sandboxfs", "read-image", json.dumps({"name": "a.png"})]) == 0
    result = json.loads(capsys.readouterr().out)
    assert base64.b64decode(result["data"]) == png()
    container = DockerSandboxContainer("test-container")
    calls = []

    async def fake_exec(argv, **kwargs):
        calls.append(argv)
        return 0, json.dumps(result).encode(), b""

    container._exec = fake_exec
    assert asyncio.run(container.read_image("a.png")) == result
    assert calls == [["sandboxfs", "read-image", '{"name": "a.png"}']]


def test_schema_is_stable_and_declares_observed_binding():
    spec = next(spec for spec in SPECS if spec.name == "VIEW_IMAGE")
    assert spec.target_pool == "visual_sources" and spec.target_parameter == "source"
    assert spec.binding_defaults == {"detail": "auto"}
    assert validate_arguments("VIEW_IMAGE", {"source": "./foo.png"}) == {
        "source": "foo.png", "detail": "auto"}
