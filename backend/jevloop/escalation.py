"""Path A escalation on the conversation ledger.

Jev speculates; when its calibrated confidence is low — or when the latest
committed observation is a recoverable refusal/failure — the LLM continues its
own transcript: the exact prefix it has "been driving all along", with a note
appended as the only new turn. Prefix stability means provider prompt caching
carries across arbitrations (the system prompt is never rewritten; there is no
separate adjudicator persona — the note turn carries all arbitration
semantics, preserving the LLM's first-person continuity).

Every arbitration is recorded: (jev distribution, llm pick) pairs are the
calibration evidence this framework exists to produce. A recovery arbitration
is triggered by the latest observation's typed recoverability, independently
of Jev's confidence.
"""

import json
import os

from .arguments import argument_target, argument_text, arguments_complete, validate_arguments
from .guardrails import InvalidProposal, MalformedAuthoredValue
from .model import post_json
from .text_helper import validate_authored_value
from .transcript import tool_schemas


def should_escalate(decision, threshold, ambiguity_gate=None):
    """Low routing confidence escalates only when the Noul ambiguity signal
    also marks the choice contested. The relaxation covers read/verify
    decisions only: ACT (mutations) and RESPOND (terminal delivery) keep the
    full confidence net, where a wrong unambiguous-looking pick is most
    costly. Without a gate (or without the signal in the decision) low
    confidence alone escalates, exactly as before."""
    confidence = decision.get("confidence")
    if threshold is None or confidence is None:
        return False
    if confidence >= threshold:
        return False
    if ambiguity_gate is None or decision.get("ambiguity") is None:
        return True
    if decision.get("phase") in {"ACT", "RESPOND"}:
        return True
    return decision["ambiguity"] > ambiguity_gate


def latest_recoverable(workspace):
    """The immediately preceding observation of the CURRENT turn when it is
    recoverable: the next decision must be arbitrated regardless of confidence.
    Derived from committed history and the generic turn boundary, not from any
    workspace mode flag; prior-turn failures do not force arbitration."""
    current = workspace.current_turn_history()
    if not current:
        return None
    latest = current[-1]
    error = latest.get("error") or {}
    return latest if error.get("recoverability") == "recoverable" else None


def _note_text(jev_decision):
    phase_ranked = sorted(
        (jev_decision.get("phase_probabilities") or {}).items(),
        key=lambda item: -item[1],
    )[:4]
    action_ranked = sorted(
        (jev_decision.get("operation_probabilities") or {}).items(),
        key=lambda item: -item[1],
    )[:5]
    phase_dist = ", ".join(
        f"{phase} {probability:.2f}" for phase, probability in phase_ranked)
    action_dist = ", ".join(
        f"{action} {probability:.2f}" for action, probability in action_ranked)
    meta = ""
    ambiguity = jev_decision.get("ambiguity")
    progress = (jev_decision.get("progress") or {}).get("score")
    if ambiguity is not None or progress is not None:
        parts = []
        if ambiguity is not None:
            parts.append(f"ambiguity {ambiguity:.2f}")
        if progress is not None:
            parts.append(f"progress {progress:.1f}/3")
        meta = f" Meta signals: {', '.join(parts)}."
    return (
        "[fast-decision note] A fast decision model selected "
        f"phase {jev_decision.get('phase')} ({phase_dist}) and action "
        f"{jev_decision.get('operation')} ({action_dist}); the consumed-path "
        f"confidence is {jev_decision.get('confidence'):.2f}.{meta} "
        "Continue with your best next tool call."
    )


def _recovery_note_text(observation):
    error = observation.get("error") or {}
    operation = observation.get("operation") or "the previous action"
    return (
        "[recovery note] The previous attempt was refused or failed without "
        f"being applied: {operation} (observation {observation.get('observation_id')}) "
        f"ended with {error.get('code')} at stage {error.get('stage')}; effect: "
        f"{observation.get('disposition') or 'unknown'}. Using this evidence, "
        "choose the best next permitted action — inspect what happened, correct "
        "the attempt, change approach, or answer with a grounded limitation. "
        "Do not repeat the refused attempt unchanged."
    )


def _scoped_tool_schemas(provider, jev_decision, valid_actions):
    """Stable catalog: current allowed actions are validated separately."""
    # The observed shortcut window is not the tool's complete argument space.
    # Runtime validation owns closed domain references and authorization.
    return tool_schemas(provider)


async def arbitrate(transcript, jev_decision, provider, valid_actions, post=None,
                   recovery=None):
    """Ask the LLM for one typed proposal without mutating the ledger.

    The caller owns transcript changes. The returned note and genuine assistant
    message let the shared runtime append exactly the turns it commits; invalid
    replies retain the note but never introduce a dangling tool call. When
    `recovery` (the latest recoverable observation) is given, the note cites its
    observation id, code, stage and effect instead of the confidence digest."""
    note = _recovery_note_text(recovery) if recovery else _note_text(jev_decision)
    note += f" Currently permitted operations: {', '.join(sorted(valid_actions))}."
    import time

    started = time.perf_counter()
    base = os.environ.get("TEXT_MODEL_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
    key = os.environ.get("DEEPSEEK_API_KEY") or ""
    request = {
        "model": os.environ.get("TEXT_MODEL", "deepseek-chat"),
        "max_tokens": 8192,
        "messages": [*transcript.messages(), {"role": "user", "content": note}],
        "tools": _scoped_tool_schemas(provider, jev_decision, valid_actions),
        "tool_choice": "auto",
        "parallel_tool_calls": False,
    }
    result = await (post or post_json)(
        base + "/chat/completions",
        key,
        request,
    )
    message = result["choices"][0]["message"]
    usage = result.get("usage", {})
    action, target, call_id, content = _parse(message)
    common = {
        "note": note,
        "usage": usage,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "request": request,
        "response": message,
    }
    if (result["choices"][0] or {}).get("finish_reason") == "length":
        return {**common, "valid": False}  # truncated proposal, usage retained
    if action is None or action not in valid_actions:
        return {**common, "valid": False}
    specs = {spec.name: spec for spec in provider.specs()}
    calls = message.get("tool_calls") or []
    if calls:
        try:
            args = json.loads(calls[0]["function"]["arguments"])
            # Legacy targets need the actual Workspace and are validated by
            # JevDriver. Canonical schemas can already be checked here.
            spec = specs.get(action)
            if not arguments_complete(spec, args, action):
                return {**common, "valid": False}
            if spec is None or spec.parameters is not None:
                args = validate_arguments(spec, args, operation=action)
                target = argument_target(spec, args)
                content = argument_text(spec, args, action)
            return {**common, "valid": True, "action": action, "target": target,
                    "call_id": call_id, "content": content, "arguments": args,
                    "message": message}
        except (InvalidProposal, MalformedAuthoredValue, ValueError, TypeError, KeyError):
            return {**common, "valid": False}
    if isinstance(target, list):
        if not target or any(not isinstance(item, str) for item in target):
            return {**common, "valid": False}
    elif target is not None:
        target = str(target)
    if content is not None and not isinstance(content, str):
        return {**common, "valid": False}
    if isinstance(content, str):
        try:
            # native typed content is data: validated, never DSML-unwrapped
            content = validate_authored_value(content)
        except MalformedAuthoredValue:
            return {**common, "valid": False}
    needs_text = action == "ANSWER" or (specs.get(action) and specs[action].needs_text)
    if needs_text and not content:
        return {**common, "valid": False}
    return {
        **common,
        "valid": True,
        "action": action,
        "target": target,
        "call_id": call_id,
        "content": content,
        "message": message,
    }


def _parse(message):
    for call in message.get("tool_calls") or []:
        try:
            args = json.loads(call["function"]["arguments"] or "{}")
        except json.JSONDecodeError:
            return None, None, None, None
        if not isinstance(args, dict):
            return None, None, None, None
        name = call["function"]["name"]
        if name == "ANSWER":
            return "ANSWER", None, call.get("id"), args.get("answer")
        if name == "DONE":
            return "DONE", None, call.get("id"), None
        if "target" in args and "targets" in args:
            return None, None, None, None
        content = args.get("query") if name.startswith("SEARCH") else args.get("content")
        target = args.get("targets") if "targets" in args else args.get("target")
        return name, target, call.get("id"), content
    try:  # fallback: plain json answer
        payload = json.loads(message.get("content") or "")
        if not isinstance(payload, dict):
            return None, None, None, None
        target = payload.get("targets") if "targets" in payload else payload.get("target")
        return payload.get("action"), target, None, payload.get("content")
    except (json.JSONDecodeError, AttributeError, TypeError):
        return None, None, None, None
