"""End-to-end visual evidence contracts with deterministic model transport."""

import asyncio
import base64
import io
import json
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from jevloop.context.projection import rebuild_workspace
from jevloop.context.state import Workspace
from jevloop.context.transcript import Transcript
from jevloop.contracts.media import MediaUnavailable
from jevloop.contracts.policy import EventSinkError, WritePolicy
from jevloop.decision.argument_helper import generate_arguments
from jevloop.decision.drivers import DriverContext, JevDriver, PlainLlmDriver
from jevloop.decision.escalation import arbitrate
from jevloop.decision.llm_transport import prepare_request
from jevloop.decision.model import compile_questions
from jevloop.decision.text_helper import generate_text
from jevloop.decision.visual_reader import read_visual
from jevloop.runtime.kernel import RuntimeKernel
from jevloop.runtime.metrics import RunMetrics
from jevloop.storage import assets, sessions
from jevloop.tools.sandbox import SandboxTools


@pytest.fixture(autouse=True)
def forbid_live_model_transport(monkeypatch):
    def forbidden():
        pytest.fail("Multimodal regression tests must inject all model transports")

    monkeypatch.setattr("jevloop.decision.model.client", forbidden)


@pytest.fixture
def image(tmp_path, monkeypatch):
    monkeypatch.setenv("JEVLOOP_ASSETS_DIR", str(tmp_path / "assets"))
    monkeypatch.setenv("VISION_MODEL", "fixture-vision")
    monkeypatch.setenv("VISION_MODEL_BASE_URL", "https://fixture.invalid/v1")
    monkeypatch.setenv("VISION_MODEL_API_KEY", "fixture-not-a-secret")
    for name in ("IN", "OUT", "CACHE_HIT"):
        monkeypatch.delenv(f"VISION_PRICE_{name}_PER_MTOK", raising=False)
    monkeypatch.setattr(sessions, "DIR", tmp_path / "sessions")
    data = io.BytesIO()
    Image.new("RGB", (20, 10), "red").save(data, format="PNG")
    return assets.ingest_image(data.getvalue(), "screen.png")


def completion(name="ANSWER", args=None):
    return {"choices": [{"finish_reason": "tool_calls", "message": {
        "role": "assistant", "content": None, "tool_calls": [{
            "id": "fixture-call", "type": "function", "function": {
                "name": name, "arguments": json.dumps(args or {"answer": "A red image."})}}]}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 4,
                  "prompt_tokens_details": {"cached_tokens": 5}}}


def native_images(request):
    return [part for message in request["messages"] if isinstance(message.get("content"), list)
            for part in message["content"] if part.get("type") == "image_url"]


def bound(operation, arguments):
    return {"operation": operation, "confidence": 1, "binding_mode": "bound",
            "bound_arguments": arguments, "latency_ms": 1, "usage": {}}


def visual_completion(text="Visible fixture: a red rectangle, with no readable labels."):
    return {"choices": [{"finish_reason": "stop", "message": {
        "role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 4,
                  "prompt_tokens_details": {"cached_tokens": 5}}}


def no_vision(monkeypatch):
    for name in ("VISION_MODEL", "VISION_MODEL_BASE_URL", "VISION_MODEL_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def assert_complete_tool_history(messages):
    pending = set()
    for message in messages:
        if message.get("tool_calls"):
            assert not pending
            pending = {call["id"] for call in message["tool_calls"]}
        elif message["role"] == "tool":
            assert message["tool_call_id"] in pending
            pending.remove(message["tool_call_id"])
        else:
            assert not pending
    assert not pending


@pytest.mark.parametrize("route", ["arguments", "arbitration", "plain", "text"])
def test_ordinary_llm_routes_use_metadata_and_observations_without_vision(image, monkeypatch, route):
    no_vision(monkeypatch)
    monkeypatch.setenv("TEXT_MODEL", "fixture-text")
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "https://text.invalid/v1")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "text-fixture-key")
    ledger = Transcript("system", "Plan an implementation consistent with the supplied context", images=[image])
    call = ledger.append_action("VIEW_IMAGE", {"source": "asset:" + image["asset_id"]})
    ledger.append_result(call, {"status": "ready", "images": [image],
        "observation": {"scope": "fixture", "evidence": "Previous observation: red rectangle.",
                        "references": [], "truncated": False}})
    original = ledger.dump()
    requests = []

    async def post(url, key, request):
        assert url == "https://text.invalid/v1/chat/completions"
        assert key == "text-fixture-key"
        assert request["model"] == "fixture-text"
        assert not native_images(request)
        assert image["asset_id"] in json.dumps(request)
        assert "Previous observation: red rectangle." in json.dumps(request)
        requests.append(deepcopy(request))
        if route == "text":
            return {"choices": [{"finish_reason": "stop", "message": {
                "role": "assistant", "content": "A red image."}}]}
        return completion()

    monkeypatch.setattr("jevloop.decision.drivers.post_json", post)
    provider = SandboxTools()

    async def run():
        if route == "arguments":
            _, info = await generate_arguments(ledger, provider, "ANSWER", post=post)
        elif route == "arbitration":
            info = await arbitrate(ledger, {"operation": "ANSWER", "confidence": .2},
                                   provider, {"ANSWER"}, post=post)
            assert info["valid"]
        elif route == "plain":
            info = (await PlainLlmDriver().decide(DriverContext(
                "goal", Workspace(), ledger, provider))).helper_info
        else:
            _, info = await generate_text(ledger, "Describe the image", post=post)
        assert "data:image" not in json.dumps(info)
        assert image["asset_id"] in json.dumps(info)

    asyncio.run(run())
    assert len(requests) == 1 and not native_images(requests[0])
    assert ledger.dump()[:len(original)] == original
    assert "fixture-not-a-secret" not in json.dumps(ledger.dump())


def test_tool_result_images_keep_call_pairing_prefix_and_restore(image):
    ledger = Transcript("system", "Look at this file")
    call = ledger.append_action("VIEW_IMAGE", {"source": "screen.png", "detail": "auto"})
    ledger.append_result(call, {"status": "ready", "images": [image]})
    prefix = ledger.llm_messages()
    _, wire, logged = prepare_request(ledger)
    assert wire["messages"][2]["tool_calls"][0]["id"] == call
    assert wire["messages"][3]["role"] == "tool"
    assert wire["messages"][3]["tool_call_id"] == call
    assert len(wire["messages"]) == 4
    assert json.loads(wire["messages"][3]["content"])["image_references"] == [image]
    assert not native_images(wire)
    assert len(ledger.dump()) == 4
    ledger.append_user("Now inspect a different detail")
    assert ledger.llm_messages()[:len(prefix)] == prefix
    assert prepare_request(ledger)[1]["messages"][:len(wire["messages"])] == wire["messages"]
    sessions.save("fixture", ledger)
    restored = sessions.load("fixture")
    assert restored.dump() == ledger.dump()
    assert restored.llm_messages() == ledger.llm_messages()
    assert rebuild_workspace(restored).images == {image["asset_id"]: image}
    assert "data:image" not in json.dumps(logged)
    assert "data:image" not in sessions.path_for("fixture").read_text()


def test_multi_tool_results_keep_pairing_and_only_explicit_selection_gets_pixels(image):
    ledger = Transcript("system", "goal")
    ledger.append_assistant({"role": "assistant", "tool_calls": [
        {"id": key, "type": "function", "function": {"name": "VIEW_IMAGE", "arguments": "{}"}}
        for key in ("a", "b")]})
    for key in ("a", "b"):
        ledger.append_result(key, {"status": "ready", "images": [image]})
    _, request, _ = prepare_request(ledger)
    assert [m["role"] for m in request["messages"]] == ["system", "user", "assistant", "tool", "tool"]
    assert not native_images(request)
    _, selected, logged = prepare_request(ledger, selected_images=[image])
    assert [m["role"] for m in selected["messages"]] == [
        "system", "user", "assistant", "tool", "tool", "user"]
    assert len(native_images(selected)) == 1
    assert_complete_tool_history(selected["messages"])
    assert "data:image" not in json.dumps(logged)


def test_picture_metadata_does_not_become_text_inference_or_authority(image):
    ledger = Transcript("system", json.dumps({"images": [image]}))
    assert ledger.images() == []
    call = ledger.append_action("VIEW_IMAGE", {"source": "x"})
    ledger.append_result(call, {"status": "failed", "images": [image]})
    assert ledger.images() == []
    ledger.append_user("Actual attachment", images=[image])
    ws = rebuild_workspace(ledger)
    state = ws.state()
    assert state["image_visibility"].startswith("metadata only")
    assert "data:image" not in json.dumps(state)
    questions, compiled = compile_questions(ws, SandboxTools())
    candidates = compiled.target_candidates[("INSPECT", "VIEW_IMAGE")]
    assert "asset:" + image["asset_id"] in candidates
    assert "LLM_PARAMETERS" in candidates
    assert questions


def test_capability_missing_or_asset_corrupt_never_silently_drops_image(image, monkeypatch):
    ledger = Transcript("system", "goal", images=[image])
    monkeypatch.delenv("VISION_MODEL", raising=False)
    with pytest.raises(MediaUnavailable, match="VISION_MODEL"):
        prepare_request(ledger, selected_images=[image])
    assert not native_images(prepare_request(ledger)[1])
    monkeypatch.setenv("VISION_MODEL", "fixture-vision")
    monkeypatch.setattr(assets, "read_image", lambda _id: (_ for _ in ()).throw(assets.AssetError("corrupt")))
    with pytest.raises(MediaUnavailable, match="missing or corrupt"):
        prepare_request(ledger, selected_images=[image])
    assert not native_images(prepare_request(ledger)[1])


@pytest.mark.parametrize("usage", [
    pytest.param("missing", id="missing-usage"),
    pytest.param({"total_tokens": 14}, id="total-only"),
    pytest.param({"prompt_tokens": None, "completion_tokens": 4}, id="null-prompt"),
    pytest.param({"prompt_tokens": 10, "completion_tokens": None}, id="null-completion"),
    pytest.param({"prompt_tokens": "10", "completion_tokens": 4}, id="string-prompt"),
    pytest.param({"prompt_tokens": 10, "completion_tokens": "4"}, id="string-completion"),
    pytest.param({"prompt_tokens": True, "completion_tokens": 4}, id="boolean-prompt"),
    pytest.param({"prompt_tokens": 10, "completion_tokens": -1}, id="negative-completion"),
])
def test_visual_helper_keeps_complete_task_history_and_reports_unknown_usage(image, monkeypatch, usage):
    ledger = Transcript("system", "Match the implementation to the supplied background context", images=[image])
    prior = ledger.append_action("READ_FILE", {"path": "notes.txt"})
    ledger.append_result(prior, {"status": "ready", "observation": {
        "scope": "notes.txt", "evidence": "Constraint: preserve accessible navigation.",
        "references": [{"kind": "file", "value": "notes.txt", "label": "notes.txt"}], "truncated": False}})
    original = ledger.dump()
    requests = []

    async def post(_url, _key, request):
        requests.append(deepcopy(request))
        assert_complete_tool_history(request["messages"])
        rendered = json.dumps(request)
        assert "Constraint: preserve accessible navigation." in rendered
        assert "supplied background context" in rendered
        assert "not a final user answer" in rendered
        assert len(native_images(request)) == 1
        result = visual_completion("Visible fixture: red navigation panel; text cannot be read.")
        if usage == "missing":
            result.pop("usage")
        else:
            result["usage"] = usage
        return result

    monkeypatch.setattr("jevloop.decision.visual_reader.post_json", post)
    text, helper = asyncio.run(read_visual(ledger, image))
    assert "text cannot be read" in text
    assert len(requests) == 1
    assert ledger.dump() == original
    assert helper["usage_unknown"] is True
    assert "data:image" not in json.dumps(helper)
    assert "asset:" + image["asset_id"] in json.dumps(helper)
    for name in ("IN", "OUT", "CACHE_HIT"):
        monkeypatch.setenv(f"VISION_PRICE_{name}_PER_MTOK", "1")
    metrics = RunMetrics()
    metrics.helper(helper, kind="visual_read")
    assert metrics.summary()["cost_complete"] is False
    assert metrics.summary()["helper"]["unpriced_calls"] == 1


@pytest.mark.parametrize("failed_event", ["llm_started", "llm_completed"])
def test_visual_event_sink_failure_propagates_without_tool_observation(image, failed_event):
    events, choices, reads = [], [], []
    sink_error = EventSinkError(f"Fixture cannot persist {failed_event}")
    assert isinstance(sink_error, OSError)

    async def chooser(*_args, **_kwargs):
        choices.append("VIEW_IMAGE")
        return bound("VIEW_IMAGE", {"source": "asset:" + image["asset_id"], "detail": "auto"})

    async def visual_reader(_transcript, part):
        reads.append(part)
        return "Offline fixture observation.", {"kind": "visual_read", "visual": True,
                                                  "usage": {"prompt_tokens": 10, "completion_tokens": 4}}

    def sink(event):
        events.append(event)
        if event["type"] == failed_event and event.get("kind") == "visual_read":
            raise sink_error

    kernel = RuntimeKernel(JevDriver(chooser=chooser, escalate_threshold=None), SandboxTools(),
                           WritePolicy(), max_steps=5, event_sink=sink, visual_reader=visual_reader)

    async def run():
        return [step async for step in kernel.run("Use the supplied background context", images=[image])]

    with pytest.raises(EventSinkError) as caught:
        asyncio.run(run())
    assert caught.value is sink_error
    assert choices == ["VIEW_IMAGE"]
    assert reads == ([] if failed_event == "llm_started" else [image])
    assert events[-1]["type"] == failed_event
    assert not any(event["type"] == "observation" for event in events)
    assert not any(message["role"] == "tool" for message in kernel.transcript.dump())
    assert kernel.workspace.history == []


def test_reattached_old_image_reenters_current_jev_window_and_restores_order(monkeypatch):
    no_vision(monkeypatch)
    # Schema-valid references suffice: normal decision state must perform no
    # image reads, so this regression deliberately creates no assets on disk.
    parts = [{"type": "image", "asset_id": f"{index + 1:064x}", "mime_type": "image/png",
              "width": 1, "height": 1, "detail": "auto", "name": f"fixture-{index}.png"}
             for index in range(21)]
    ledger = Transcript("system", "Earlier turn", images=parts[:8])
    ledger.append_user("More context", images=parts[8:16])
    ledger.append_user("Last context", images=parts[16:])
    workspace = rebuild_workspace(ledger)
    oldest = "asset:" + parts[0]["asset_id"]
    assert oldest not in workspace.pool_entries("visual_sources")
    expected = [part["asset_id"] for part in parts[1:] + parts[:1]]
    seen = []

    async def chooser(current, goal, _history, **_kwargs):
        seen.append(goal)
        assert list(current.images) == expected
        assert current.state()["image_evidence"][-1] == parts[0]
        assert next(iter(current.pool_entries("visual_sources"))) == oldest
        _, compiled = compile_questions(current, SandboxTools())
        assert oldest in compiled.target_candidates[("INSPECT", "VIEW_IMAGE")]
        return bound("ANSWER", {"answer": "The reattached context is available for this turn."})

    kernel = RuntimeKernel(JevDriver(chooser=chooser, escalate_threshold=None, answer_progress_floor=None),
                           SandboxTools(), WritePolicy(), transcript=ledger, workspace=workspace, max_steps=2)

    async def run():
        return [step async for step in kernel.run("Use this earlier context again", images=[parts[0]])]

    steps = asyncio.run(run())
    assert steps[-1]["final"] == "completed"
    assert seen == ["Use this earlier context again"]
    restored = rebuild_workspace(Transcript.from_messages(kernel.transcript.dump()))
    assert list(restored.images) == list(kernel.workspace.images) == expected
    assert restored.state()["image_evidence"] == kernel.workspace.state()["image_evidence"]
    assert list(restored.pool_entries("visual_sources")) == list(kernel.workspace.pool_entries("visual_sources"))


@pytest.mark.parametrize("case", ["empty", "whitespace", "truncated", "tool_call"])
def test_visual_helper_rejects_unusable_perception_responses(image, monkeypatch, case):
    async def post(_url, _key, _request):
        if case == "tool_call":
            return completion("READ_FILE", {"path": "invented.txt"})
        response = visual_completion("" if case == "empty" else "  \n" if case == "whitespace" else "partial")
        if case == "truncated":
            response["choices"][0]["finish_reason"] = "length"
        return response

    monkeypatch.setattr("jevloop.decision.visual_reader.post_json", post)
    ledger = Transcript("system", "Use supplied background context", images=[image])
    before = ledger.dump()
    with pytest.raises(MediaUnavailable, match="empty, truncated or non-observational") as caught:
        asyncio.run(read_visual(ledger, image))
    assert caught.value.helper_info["kind"] == "visual_read"
    assert ledger.dump() == before


def test_jev_contextual_visual_read_then_normal_read_and_answer(image, monkeypatch):
    calls, events, visual_requests = [], [], []
    goal = "Prepare the matching implementation using the supplied context and project notes."
    other_data = io.BytesIO()
    Image.new("RGB", (12, 12), "blue").save(other_data, format="PNG")
    other = assets.ingest_image(other_data.getvalue(), "other.png")
    source = "asset:" + image["asset_id"]
    observation = "Visible fixture: the primary surface is red; no readable labels."

    class Runtime:
        async def read_range(self, path, offset, limit):
            calls.append("read_file")
            assert path == "notes.txt"
            return {"content": "Project note: preserve the existing layout.", "truncated": False}

    async def chooser(workspace, current_goal, _history, **_kwargs):
        calls.append("jev")
        assert current_goal == goal
        assert image["asset_id"] in json.dumps(workspace.state())
        assert "data:image" not in json.dumps(workspace.state())
        step = calls.count("jev")
        if step == 1:
            assert observation not in json.dumps(workspace.state())
            return bound("VIEW_IMAGE", {"source": source, "detail": "auto"})
        assert observation in json.dumps(workspace.state())
        if step == 2:
            return bound("READ_FILE", {"path": "notes.txt", "offset": 0, "limit": 200})
        assert "Project note:" in json.dumps(workspace.state())
        return bound("ANSWER", {"answer": "Use the observed red surface and preserve the existing layout."})

    async def post(url, key, request):
        calls.append("visual_read")
        visual_requests.append(deepcopy(request))
        assert url == "https://fixture.invalid/v1/chat/completions"
        assert key == "fixture-not-a-secret"
        assert goal in json.dumps(request)
        assert "background" in json.dumps(request)
        assert_complete_tool_history(request["messages"])
        # Neither the current pending VIEW_IMAGE call nor another attachment's
        # pixels is sent to the tool's perception-only helper.
        assert not any(message.get("tool_calls") for message in request["messages"])
        pictures = native_images(request)
        assert len(pictures) == 1
        selected = base64.b64decode(pictures[0]["image_url"]["url"].split(",", 1)[1])
        assert selected == assets.read_image(image["asset_id"])[0]
        assert selected != assets.read_image(other["asset_id"])[0]
        return visual_completion(observation)

    monkeypatch.setattr("jevloop.decision.visual_reader.post_json", post)
    provider = SandboxTools(Runtime())
    kernel = RuntimeKernel(JevDriver(chooser=chooser, escalate_threshold=None, answer_progress_floor=None),
                           provider, WritePolicy(), max_steps=4, event_sink=events.append)

    async def run():
        return [step async for step in kernel.run(goal, images=[image, other])]

    steps = asyncio.run(run())
    assert calls == ["jev", "visual_read", "jev", "read_file", "jev"]
    assert [step["decision"]["operation"] for step in steps if "decision" in step] == [
        "VIEW_IMAGE", "READ_FILE", "ANSWER"]
    assert steps[-1]["final"] == "completed"
    summary = kernel.metrics.summary()
    assert summary["jev"]["calls"] == 3
    assert summary["routing"]["visual_steps"] == 0
    assert summary["routing"]["direct_jev_steps"] == 2
    assert summary["routing"]["llm_assisted_jev_steps"] == 1
    assert summary["cost_complete"] is False
    assert summary["helper"]["by_kind"]["visual_read"]["cache_hit_tokens"] == 5
    assert any(call["kind"] == "visual_read" for call in steps[0]["model_calls"])
    dispatch = next(i for i, event in enumerate(events) if event["type"] == "dispatch_started")
    started = next(i for i, event in enumerate(events)
                   if event["type"] == "llm_started" and event.get("kind") == "visual_read")
    completed = next(i for i, event in enumerate(events)
                     if event["type"] == "llm_completed" and event.get("kind") == "visual_read")
    recorded = next(i for i, event in enumerate(events) if event["type"] == "observation")
    assert dispatch < started < completed < recorded
    assert events[started]["intent_id"] == events[dispatch]["intent_id"]
    assert events[started]["attempt_id"] == events[completed]["attempt_id"]
    assert "data:image" not in json.dumps([steps, events, kernel.transcript.dump()])
    restored = Transcript.from_messages(kernel.transcript.dump())
    assert observation in json.dumps(restored.dump())
    assert rebuild_workspace(restored).images == kernel.workspace.images
    assert len(visual_requests) == 1


def test_irrelevant_attachment_keeps_jev_normal_tool_choice_without_vision(image, monkeypatch):
    no_vision(monkeypatch)
    calls = []

    class Runtime:
        async def read_range(self, path, offset, limit):
            calls.append("READ_FILE")
            return {"content": "version=7", "truncated": False}

    async def chooser(workspace, goal, _history, **_kwargs):
        calls.append("jev")
        assert image["asset_id"] in json.dumps(workspace.state())
        return (bound("READ_FILE", {"path": "config.txt", "offset": 0, "limit": 200})
                if calls.count("jev") == 1 else bound("ANSWER", {"answer": "Version is 7."}))

    async def forbidden(*_args, **_kwargs):
        pytest.fail("Irrelevant attachment must not trigger visual or helper inference")

    kernel = RuntimeKernel(JevDriver(chooser=chooser, escalate_threshold=None, answer_progress_floor=None),
                           SandboxTools(Runtime()), WritePolicy(), max_steps=3, visual_reader=forbidden,
                           arguer=forbidden, texter=forbidden)

    async def run():
        return [step async for step in kernel.run("Read the version from config.txt", images=[image])]

    steps = asyncio.run(run())
    assert calls == ["jev", "READ_FILE", "jev"]
    assert steps[-1]["final"] == "completed"
    assert kernel.metrics.summary()["helper"]["calls"] == 0


@pytest.mark.parametrize("kind", ["visual_read", "visual_decision"])
def test_visual_costs_use_explicit_prices_not_deepseek_defaults(image, monkeypatch, kind):
    metrics = RunMetrics()
    info = {"visual": True, "usage": {"prompt_tokens": 1000, "completion_tokens": 100}}
    metrics.helper(info, kind=kind)
    assert not metrics.summary()["cost_complete"]
    for name, value in (("IN", "2"), ("OUT", "8"), ("CACHE_HIT", "1")):
        monkeypatch.setenv(f"VISION_PRICE_{name}_PER_MTOK", value)
    metrics = RunMetrics()
    metrics.helper(info, kind=kind)
    assert metrics.summary()["cost_complete"]
    assert metrics.summary()["est_cost_usd"] == .0028


def test_repeated_visual_readings_preserve_more_than_eight_observations(image):
    observations = []

    async def chooser(_workspace, _goal, _history, **_kwargs):
        return (bound("VIEW_IMAGE", {"source": "asset:" + image["asset_id"], "detail": "auto"})
                if len(observations) < 10 else bound("ANSWER", {"answer": "Ten contextual readings retained."}))

    async def visual_reader(ledger, part):
        assert part == image
        assert_complete_tool_history(ledger.llm_messages())
        observation = f"Contextual reading {len(observations) + 1}: newly examined fixture detail."
        observations.append(observation)
        return observation, {"kind": "visual_read", "visual": True,
                             "usage": {"prompt_tokens": 10, "completion_tokens": 4}}

    kernel = RuntimeKernel(JevDriver(chooser=chooser, escalate_threshold=None, answer_progress_floor=None),
                           SandboxTools(), WritePolicy(), max_steps=12, visual_reader=visual_reader)

    async def run():
        return [step async for step in kernel.run("Examine additional details as needed", images=[image])]

    steps = asyncio.run(run())
    assert steps[-1]["final"] == "completed"
    assert len(observations) == 10
    restored = Transcript.from_messages(kernel.transcript.dump())
    results = [json.loads(message["content"]) for message in restored.dump() if message["role"] == "tool"]
    readings = [result for result in results if result.get("images")]
    assert len(readings) == 10
    assert all(text in result["observation"]["evidence"] for text, result in zip(observations, readings, strict=True))
    assert all(result["images"] == [image] for result in readings)
    assert len(restored.images()) == 11
    assert rebuild_workspace(restored).images == {image["asset_id"]: image}
    assert kernel.metrics.summary()["helper"]["by_kind"]["visual_read"]["calls"] == 10
    assert not native_images(prepare_request(restored)[1])


def test_image_limits_apply_to_batches_and_explicit_requests_not_history(image, monkeypatch):
    ledger = Transcript("system", "goal", images=[image] * 8)
    ledger.append_user("another turn", images=[image])
    assert len(ledger.images()) == 9
    before = ledger.dump()
    with pytest.raises(MediaUnavailable, match="at most 8"):
        ledger.append_user("oversized batch", images=[image] * 9)
    assert ledger.dump() == before
    with pytest.raises(MediaUnavailable, match="at most 8"):
        prepare_request(ledger, selected_images=[image] * 9)
    monkeypatch.setattr(assets, "MAX_REQUEST_IMAGE_BYTES", 1)
    with pytest.raises(MediaUnavailable, match="byte budget"):
        prepare_request(ledger, selected_images=[image])
    assert not native_images(prepare_request(ledger)[1])


def test_visual_http_error_never_persists_echoed_pixels(image, monkeypatch):
    from jevloop.decision import model
    from jevloop.storage import runstore

    monkeypatch.setattr(runstore, "DIR", sessions.DIR.parent / "runs")
    journal = runstore.Journal("vision-error")

    async def reject(_url, **kwargs):
        echoed = native_images(kwargs["json"])[0]["image_url"]["url"]
        return httpx.Response(400, text="Invalid input: " + echoed)

    monkeypatch.setattr(model, "client", lambda: SimpleNamespace(post=reject))

    async def chooser(*_args, **_kwargs):
        return bound("VIEW_IMAGE", {"source": "asset:" + image["asset_id"], "detail": "auto"})

    kernel = RuntimeKernel(JevDriver(chooser=chooser, escalate_threshold=None), SandboxTools(),
                           WritePolicy(), max_steps=1,
                           event_sink=journal.emit,
                           checkpoint=lambda ledger: sessions.save("vision-error", ledger))

    async def run():
        return [step async for step in kernel.run("inspect", images=[image])]

    steps = asyncio.run(run())
    saved = sessions.load("vision-error").dump()
    rendered = json.dumps([steps, saved, runstore.load("vision-error")])
    assert "HTTP 400" in rendered
    assert "data:image" not in rendered
    assert "base64," not in rendered
    assert kernel.metrics.summary()["routing"]["direct_jev_steps"] == 0
    assert kernel.metrics.summary()["cost_complete"] is False
    assert kernel.metrics.summary()["helper"]["by_kind"]["visual_read"]["unpriced_calls"] == 1
    assert any(call["kind"] == "visual_read" for call in steps[0]["model_calls"])


def test_asset_http_routes_and_body_limits(image):
    from jevloop.apps.server import Handler

    handler = object.__new__(Handler)
    handler.path = "/api/assets"
    data, _ = assets.read_image(image["asset_id"])
    body = json.dumps({"name": "upload.png", "data": base64.b64encode(data).decode()}).encode()
    handler.headers = {"Content-Length": str(len(body))}
    handler.rfile = io.BytesIO(body)
    handler._json = lambda payload, status=200: (status, payload)
    status, payload = handler.do_POST()
    assert status == 200
    uploaded = payload["image"]
    handler.path += "/" + uploaded["asset_id"]
    responses, headers = [], {}
    handler.send_response = responses.append
    handler.send_header = lambda key, value: headers.update({key: value})
    handler.end_headers = lambda: None
    handler.wfile = io.BytesIO()
    handler.do_GET()
    assert responses == [200]
    assert headers["Content-Type"] == "image/png"
    assert handler.wfile.getvalue() == assets.read_image(uploaded["asset_id"])[0]
    handler.path = "/api/assets/../../env"
    assert handler.do_GET()[0] == 404
    handler.path = "/api/assets"
    handler.headers = {"Content-Length": str(20 * 1024 * 1024)}
    assert handler.do_POST()[0] == 400
    assert handler.close_connection


def test_dashboard_pair_has_identical_real_images_and_separate_ledgers(image, monkeypatch):
    from jevloop.apps.server import Dashboard, _session_storage_id

    no_vision(monkeypatch)
    events, requests, choices = [], [], []
    params = {"goal": "describe", "images": [image], "profile": "paired_shadow",
              "session_id": "image-pair", "max_steps": 1, "sandbox_network": False}
    state = SimpleNamespace(params=params, emit=events.append, abort=False, step_pause=False)

    async def post(_url, _key, request):
        requests.append(request)
        assert not native_images(request)
        return completion()

    monkeypatch.setattr("jevloop.decision.drivers.post_json", post)

    async def chooser(workspace, goal, _history, **_kwargs):
        choices.append((deepcopy(workspace.images), goal))
        return bound("ANSWER", {"answer": "Attachment remains available if needed."})

    monkeypatch.setattr("jevloop.apps.server.JevDriver", lambda **_kwargs: JevDriver(
        chooser=chooser, escalate_threshold=None, answer_progress_floor=None))

    async def run():
        dashboard = Dashboard()
        for lane in ("jev", "baseline"):
            await dashboard._run_lane(state, None, lane)

    asyncio.run(run())
    assert not [event for event in events if event["type"] == "error"]
    assert len(requests) == 1  # Baseline's ordinary LLM driver; Jev still used its chooser.
    assert choices == [({image["asset_id"]: image}, "describe")]
    assert image["asset_id"] in json.dumps(requests[0])
    for lane in ("jev", "baseline"):
        assert sessions.load(_session_storage_id(params, lane)).images() == [image]
    assert _session_storage_id(params, "jev") != _session_storage_id(params, "baseline")


def test_cli_accepts_repeated_images():
    from jevloop.cli import build_parser

    args = build_parser().parse_args(["run", "compare", "--image", "a.png", "--image", "b.png"])
    assert args.image == ["a.png", "b.png"]


def test_animation_is_not_silently_reduced_to_one_frame(image):
    data = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(data, format="GIF", save_all=True,
        append_images=[Image.new("RGB", (8, 8), "blue")], duration=100, loop=0)
    with pytest.raises(assets.AssetError, match="animated"):
        assets.ingest_image(data.getvalue(), "motion.gif")


def test_jev_image_metadata_view_is_detached(image):
    workspace = Workspace()
    workspace.images[image["asset_id"]] = image
    state = workspace.state()
    state["image_evidence"][0]["name"] = "mutated"
    assert workspace.images[image["asset_id"]]["name"] == "screen.png"


def test_missing_vision_capability_is_terminal_not_model_repair(image, monkeypatch):
    monkeypatch.delenv("VISION_MODEL")
    choices = []

    async def chooser(*_args, **_kwargs):
        choices.append("VIEW_IMAGE")
        return bound("VIEW_IMAGE", {"source": "asset:" + image["asset_id"], "detail": "auto"})

    async def forbidden(*_args, **_kwargs):
        pytest.fail("Missing vision must fail before transport or parameter repair")

    monkeypatch.setattr("jevloop.decision.visual_reader.post_json", forbidden)
    kernel = RuntimeKernel(JevDriver(chooser=chooser, escalate_threshold=None), SandboxTools(),
                           WritePolicy(), max_steps=30, arguer=forbidden, texter=forbidden)

    async def run():
        return [step async for step in kernel.run("inspect", images=[image])]

    steps = asyncio.run(run())
    assert kernel.budget.steps == 1
    assert steps[0]["outcome"]["error"]["recoverability"] == "terminal"
    assert "VISION_MODEL" in steps[0]["outcome"]["reason"]
    assert choices == ["VIEW_IMAGE"]
    assert kernel.metrics.summary()["jev"]["calls"] == 1
    assert kernel.metrics.summary()["routing"]["direct_jev_steps"] == 0
