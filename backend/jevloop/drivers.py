"""Decision-only strategies for the shared runtime kernel.

Drivers may call models and return one proposal, or raise a typed failure.
They never execute tools, spend budgets, mutate Workspace, or append to the
transcript; RuntimeKernel owns those state transitions for every lane.
"""

import json
import os
import time
from dataclasses import dataclass, field

from .arguments import argument_target, argument_text, validate_arguments
from .config import (
    DEFAULT_AMBIGUITY_GATE,
    DEFAULT_ANSWER_PROGRESS_FLOOR,
    DEFAULT_ESCALATE_THRESHOLD,
)
from .escalation import arbitrate, latest_recoverable, should_escalate
from .guardrails import InvalidProposal, MalformedAuthoredValue
from .model import action_catalog, choose, compile_questions, post_json
from .text_helper import _clean
from .transcript import Transcript, llm_tool_schemas


@dataclass(frozen=True)
class DriverContext:
    goal: str
    workspace: object
    transcript: object
    provider: object
    write_min_confidence: float = 0.0


@dataclass
class DriverProposal:
    decision: dict
    base_decision: dict
    request: object = None
    escalation: dict | None = None
    transcript_note: str | None = None
    assistant_message: dict | None = None
    helper_info: dict | None = None
    arbitration_pick: dict | None = None
    driver_meta: dict = field(default_factory=dict)
    model_calls: list = field(default_factory=list)


class DriverRejected(InvalidProposal):
    """A billed model response that cannot become a safe proposal. Recoverable:
    the kernel records the typed observation and the next bounded iteration
    decides again — never a hidden fallback execution of the rejected action.
    When the response carried a provider-valid tool call, `assistant_message`
    and `pending_call_id` let the kernel commit that turn and answer it with
    the rejection result instead of leaving the ledger protocol-dangling."""

    def __init__(
        self, message, *, helper_info=None, request=None,
        base_decision=None, model_calls=None, assistant_message=None,
        pending_call_id=None, details=None, related=None,
    ):
        super().__init__(message, details=details, related=related)
        self.helper_info = helper_info
        self.request = request
        self.base_decision = base_decision
        self.model_calls = model_calls or []
        self.assistant_message = assistant_message
        self.pending_call_id = pending_call_id


def _target_map(workspace, provider, compiled=None):
    if compiled is None:
        _questions, compiled = compile_questions(workspace, provider)
    by_operation = {}
    for (_phase, operation), criteria in compiled.target_candidates.items():
        by_operation.setdefault(operation, criteria)
    return by_operation


def _target_required(provider, operation):
    spec = next((item for item in provider.specs() if item.name == operation), None)
    return bool(spec and spec.needs_target)


def resolve_target(workspace, provider, operation, target, compiled=None):
    """Resolve one offered target or a bounded unique target set."""
    if target is None:
        return None
    spec = next((item for item in provider.specs() if item.name == operation), None)
    if isinstance(target, (list, tuple)):
        if not spec or spec.multi_target_max <= 1:
            return None
        if not 2 <= len(target) <= spec.multi_target_max:
            return None
        resolved = [
            resolve_target(workspace, provider, operation, item, compiled)
            for item in target
        ]
        if any(item is None or isinstance(item, tuple) for item in resolved):
            return None
        if len(set(resolved)) != len(resolved):
            return None
        return tuple(resolved)
    target = str(target)
    criteria = _target_map(workspace, provider, compiled).get(operation) or {}
    if target in criteria:
        return target
    found = workspace.find_entry(target)
    if found and found[1] in criteria:
        return found[1]
    return None


class JevDriver:
    name = "jev"

    def __init__(self, *, escalate_threshold=DEFAULT_ESCALATE_THRESHOLD,
                 ambiguity_gate=DEFAULT_AMBIGUITY_GATE,
                 answer_progress_floor=DEFAULT_ANSWER_PROGRESS_FLOOR,
                 chooser=None, adjudicator=None):
        self.escalate_threshold = escalate_threshold
        # None disables each guard: ambiguity_gate keeps confidence-only
        # escalation; answer_progress_floor keeps premature ANSWER unguarded.
        self.ambiguity_gate = ambiguity_gate
        self.answer_progress_floor = answer_progress_floor
        self._choose = chooser or choose
        self._adjudicate = adjudicator or arbitrate

    async def decide(self, context: DriverContext) -> DriverProposal:
        workspace, provider = context.workspace, context.provider
        jev_decision = await self._choose(
            workspace, context.goal, workspace.history, provider=provider)
        compiled = jev_decision.pop("compiled", None)
        model_calls = [{
            "kind": "jev_decision",
            "model": (jev_decision.get("request") or {}).get("model", "jev"),
            "latency_ms": jev_decision.get("latency_ms"),
            "usage": jev_decision.get("usage", {}),
            "request": jev_decision.get("request"),
            "response": {
                key: value for key, value in jev_decision.items() if key != "request"
            },
        }]
        threshold = self.escalate_threshold
        selected_spec = next(
            (spec for spec in provider.specs()
             if spec.name == jev_decision["operation"]),
            None,
        )
        if threshold is not None and selected_spec and selected_spec.write:
            threshold = max(threshold, context.write_min_confidence)
        # A recoverable latest observation requires arbitration independently
        # of Jev's confidence. An arbitration-disabled profile (threshold None)
        # still means no arbitration: the observation feeds back to Jev alone.
        recoverable = latest_recoverable(workspace)
        reason = None
        # The ambiguity gate may relax only genuinely read-only choices. A
        # mutating operation (including BASH in INSPECT/VERIFY) keeps the full
        # confidence net regardless of its model-selected phase.
        ambiguity_gate = self.ambiguity_gate
        if selected_spec and (selected_spec.write or selected_spec.mutates_workspace):
            ambiguity_gate = None
        operation_routing = {**jev_decision, "confidence": jev_decision.get(
            "operation_path_confidence", jev_decision.get("confidence"))}
        if should_escalate(operation_routing, threshold, ambiguity_gate):
            reason = "low_confidence"
        elif recoverable is not None and threshold is not None:
            reason = "recoverable_observation"
        elif self._premature_answer(jev_decision, workspace):
            reason = "premature_answer"
        if reason is None:
            if (threshold is not None and jev_decision.get("operation_path_confidence") is not None
                    and jev_decision.get("confidence", 1) < threshold):
                # A weak binding does not require choosing the operation again.
                jev_decision = {**jev_decision, "binding_mode": "llm_parameters",
                                "bound_arguments": {}, "target": None,
                                "confidence": operation_routing["confidence"]}
                # A route change never inherits an executable argument payload.
                jev_decision.pop("arguments", None)
                jev_decision.pop("ledger_content", None)
            return DriverProposal(
                decision=jev_decision,
                base_decision=jev_decision,
                request=jev_decision.get("request"),
                model_calls=model_calls,
            )

        actions = action_catalog(workspace, provider)
        verdict = await self._adjudicate(
            context.transcript, jev_decision, provider, actions,
            **({"recovery": recoverable} if recoverable is not None else {}))
        helper = None
        note = None
        if verdict:
            note = verdict.get("note")
            helper = {
                "kind": "arbitration",
                "model": os.environ.get("TEXT_MODEL", "deepseek-chat"),
                "latency_ms": verdict.get("latency_ms"),
                "usage": verdict.get("usage", {}),
                "request": verdict.get("request"),
                "response": verdict.get("response"),
            }
            model_calls.append(helper)
        if not verdict or not verdict.get("valid"):
            return self._invalid_arbitration_fallback(
                jev_decision, note=note, helper=helper, model_calls=model_calls)

        action = verdict["action"]
        if compiled is None:
            _questions, compiled = compile_questions(workspace, provider)
        selected_phases = [
            phase for phase, operations in compiled.phase_actions.items()
            if action in operations
        ]
        original_phase = jev_decision.get("phase")
        selected_phase = (
            original_phase if original_phase in selected_phases
            else (selected_phases[0] if selected_phases else None)
        )
        chosen_spec = next((spec for spec in provider.specs() if spec.name == action), None)
        canonical = verdict.get("arguments")
        if canonical is not None:
            try:
                canonical = validate_arguments(chosen_spec, canonical, workspace, action)
            except InvalidProposal as error:
                return self._invalid_arbitration_fallback(
                    jev_decision, note=note, helper=helper, model_calls=model_calls, reason=str(error))
            resolved = argument_target(chosen_spec, canonical)
        else:
            resolved = resolve_target(
                workspace, provider, action, verdict.get("target"), compiled)
        if resolved is None and _target_required(provider, action):
            offered = sorted(_target_map(workspace, provider, compiled).get(action) or {})
            return self._invalid_arbitration_fallback(
                jev_decision,
                note=note,
                helper=helper,
                model_calls=model_calls,
                reason=(
                    f"Arbitration selected unavailable target "
                    f"{verdict.get('target')!r} for {action}; offered targets: "
                    f"{offered[:20]!r}."
                ),
            )

        decision = dict(jev_decision)
        decision.pop("bound_arguments", None)
        decision.pop("arguments", None)
        decision.update({
            "operation": action,
            "target": resolved,
            "phase": selected_phase,
            "arbitrated": True,
            # adopt the verdict's call id only when its assistant message (the
            # turn carrying that call) will be committed — otherwise the kernel
            # synthesizes its own call instead of answering a dangling id
            "ledger_call_id": (
                verdict.get("call_id") if verdict.get("message") else None),
            "ledger_content": verdict.get("content"),
        })
        if canonical is not None:
            decision["arguments"] = canonical
            decision["ledger_content"] = argument_text(chosen_spec, canonical, action)
        decision["binding_mode"] = "arbitrated"
        if resolved is None:
            decision["target_confidence"] = None
            decision["target_probabilities"] = {}
        escalation = {
            "reason": reason,
            "from": {
                "action": jev_decision["operation"],
                "confidence": jev_decision["confidence"],
                "probabilities": jev_decision["operation_probabilities"],
                "ambiguity": jev_decision.get("ambiguity"),
                "progress": (jev_decision.get("progress") or {}).get("score"),
            },
            "to": {"phase": selected_phase, "action": action, "target": resolved},
            "agreed": jev_decision["operation"] == action,
        }
        pick = {"action": action, "target": resolved}
        return DriverProposal(
            decision=decision,
            base_decision=jev_decision,
            request=jev_decision.get("request"),
            escalation=escalation,
            transcript_note=note,
            assistant_message=verdict.get("message"),
            helper_info=helper,
            arbitration_pick=pick,
            model_calls=model_calls,
        )

    def _premature_answer(self, decision, workspace):
        """Progress-score guard on premature completion, bounded to the turn's
        first effect: an ANSWER picked before anything was attempted, with a
        progress score below the floor, needs the LLM's judgment before
        delivery. Once the turn has committed work, later ANSWERs are trusted
        (bounded cost: at most one floor arbitration per turn)."""
        floor = self.answer_progress_floor
        progress = (decision.get("progress") or {}).get("score")
        if (floor is None or decision.get("operation") != "ANSWER"
                or not isinstance(progress, (int, float)) or progress >= floor):
            return False
        return not workspace.current_turn_history()

    @staticmethod
    def _invalid_arbitration_fallback(
        jev_decision, *, note, helper, model_calls, reason=None,
    ):
        """Reject an unusable arbitration instead of executing an uncertain guess."""
        message = reason or "Invalid arbitration; no action executed."
        if note:
            message += f" {note}"
        raise DriverRejected(
            message,
            helper_info=helper,
            request=jev_decision.get("request"),
            base_decision=jev_decision,
            model_calls=model_calls,
        )


class PlainLlmDriver:
    """One plain-LLM function-call proposal per shared-kernel step."""

    name = "plain"

    def __init__(self, *, llm=None):
        self._llm_call = llm
        self.turn = 0

    async def decide(self, context: DriverContext) -> DriverProposal:
        self.turn += 1
        valid_actions = action_catalog(context.workspace, context.provider)
        schemas = llm_tool_schemas(context.provider)
        note = (f"[operation request] Currently permitted operations: {', '.join(sorted(valid_actions))}. "
                "Choose your best next permitted action; catalog visibility is not authorization.")
        request_transcript = Transcript.from_messages(context.transcript.dump())
        request_transcript.append_note(note)
        if self._llm_call:
            message, helper, request = await self._llm_call(request_transcript, schemas)
        else:
            message, helper, request = await self._llm(request_transcript, schemas)
        helper = {
            **helper,
            "kind": helper.get("kind", "plain_decision"),
            "request": request,
            "response": message,
        }

        calls = message.get("tool_calls") or []
        assistant_message = None
        call_id = None
        target = None
        content = None
        args = None
        if calls:
            call = calls[0]
            call_id = call.get("id")
            operation = call["function"]["name"]
            # A provider-valid tool call is committed and answered with the
            # rejection result; invalid JSON arguments or typed values never
            # fall back to {} or arbitrary fields.
            assistant_message = {
                "role": "assistant",
                "content": message.get("content"),
                "tool_calls": [call],
            }
            try:
                args = json.loads(call["function"]["arguments"] or "{}")
            except json.JSONDecodeError as error:
                if operation in valid_actions:
                    raise DriverRejected(
                        f"Plain LLM tool call {operation!r} has malformed JSON "
                        "arguments.",
                        helper_info=helper, request=request,
                        assistant_message=assistant_message,
                        pending_call_id=call_id,
                        details={"parse_error": str(error)},
                    ) from error
                raise DriverRejected(
                    f"Plain LLM selected unavailable action {operation!r} with "
                    "malformed arguments.",
                    helper_info=helper, request=request,
                    details={"parse_error": str(error)},
                ) from error
            if not isinstance(args, dict):
                raise DriverRejected(
                    f"Plain LLM tool call {operation!r} arguments are not an object.",
                    helper_info=helper, request=request,
                    assistant_message=assistant_message if operation in valid_actions else None,
                    pending_call_id=call_id if operation in valid_actions else None,
                )
            if operation not in valid_actions:
                raise DriverRejected(
                    f"Plain LLM selected unavailable action {operation!r}.",
                    helper_info=helper, request=request)
            base_decision = {
                "operation": operation, "target": None, "confidence": None,
                "latency_ms": helper.get("latency_ms"),
            }
            if "target" in args and "targets" in args:
                raise DriverRejected(
                    f"Plain LLM selected both target and targets for {operation}.",
                    helper_info=helper,
                    request=request,
                    base_decision=base_decision,
                    assistant_message=assistant_message,
                    pending_call_id=call_id,
                )
            spec = next((item for item in context.provider.specs() if item.name == operation), None)
            try:
                args = validate_arguments(spec, args, context.workspace, operation)
            except (InvalidProposal, MalformedAuthoredValue) as error:
                raise DriverRejected(
                    str(error), helper_info=helper, request=request,
                    base_decision=base_decision, details=getattr(error, "details", None),
                    assistant_message=assistant_message,
                    pending_call_id=call_id,
                ) from error
            target = argument_target(spec, args)
            content = argument_text(spec, args, operation)
        else:
            content = (message.get("content") or "").strip()
            try:
                content = _clean(content) if content else content
            except MalformedAuthoredValue as error:
                raise DriverRejected(
                    str(error), helper_info=helper, request=request,
                    details=error.details,
                ) from error
            operation = "ANSWER" if content else "BLOCKED"

        decision = {
            "operation": operation,
            "target": target,
            "confidence": None,
            "operation_probabilities": {},
            "target_confidence": None,
            "target_probabilities": {},
            "latency_ms": helper.get("latency_ms"),
            "usage": helper.get("usage", {}),
            "arbitrated": True,
            "ledger_call_id": call_id,
            "ledger_content": content,
            "turn": self.turn,
        }
        if args is not None:
            decision["arguments"] = args
        decision["binding_mode"] = "plain_llm"
        return DriverProposal(
            decision=decision,
            base_decision=decision,
            request=request,
            assistant_message=assistant_message,
            helper_info=helper,
            transcript_note=note,
            driver_meta={"returned_tool_calls": len(calls)},
            model_calls=[helper],
        )

    async def _llm(self, transcript, schemas):
        base = os.environ.get("TEXT_MODEL_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
        model = os.environ.get("TEXT_MODEL", "deepseek-chat")
        request = {
            "model": model,
            "max_tokens": 8192,
            "messages": transcript.llm_messages(),
            "tools": schemas,
            "tool_choice": "auto",
            "parallel_tool_calls": False,
        }
        started = time.perf_counter()
        result = await post_json(
            base + "/chat/completions", os.environ["DEEPSEEK_API_KEY"], request)
        choice = result["choices"][0]
        helper = {
            "kind": "plain_decision",
            "model": model,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "usage": result.get("usage", {}),
        }
        if choice.get("finish_reason") == "length":
            raise DriverRejected(
                "Plain LLM decision was truncated by the token limit.",
                helper_info={**helper, "request": request,
                             "response": choice.get("message")},
                request=request,
                details={"finish_reason": "length"},
            )
        return choice["message"], helper, request
