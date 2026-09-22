"""One execution loop shared by every decision strategy.

Observation-first contract: every attempt — decided, authored, refused,
denied, dispatched, failed or uncertain — finalizes through one path that
persists a canonical typed observation, projects the same evidence into the
Jev workspace history and the LLM ledger, charges its reserved step, and only
then transitions the run state. Recoverable outcomes return control to the
next ordinary decision iteration (with mandatory recovery arbitration on the
Jev lane); terminal policy/budget denials and UNKNOWN effects stop the run.
"""

import hashlib
import inspect
import uuid

from .drivers import DriverContext
from .guardrails import (
    AttemptFailure,
    Budget,
    DuplicateNoProgress,
    GuardrailDenied,
    InvalidProposal,
    MalformedAuthoredValue,
    WritePolicy,
    review,
)
from .metrics import RunMetrics
from .projection import (
    bounded_error,
    fingerprint,
    intent_fingerprint,
    observation_fingerprint,
    rebuild_workspace,
    record_execution,
)
from .questions import ANSWER_TEXT
from .state import Workspace
from .text_helper import _clean, generate_text, validate_authored_value
from .tools.base import ToolContext, text_field_for, write_actions
from .transcript import Transcript, system_prompt

CORE_ACTIONS = {"ANSWER", "DONE", "BLOCKED"}
NON_LEDGER_ACTIONS = {"BLOCKED"}  # no function schema exists to synthesize a call
DEFAULT_MAX_IDENTICAL_FAILURES = 3  # occurrences of one failure signature per turn
DEFAULT_MAX_STALE_OBSERVATIONS = 3  # consecutive observations without new evidence


class RuntimeKernel:
    """Own state, policy, execution and ledger mutation for all lanes.

    A DecisionDriver can only return one proposal. It cannot execute a tool,
    spend a budget, or mutate the transcript/workspace.
    """

    def __init__(self, driver, provider, policy, *, live=False, max_steps=30,
                 max_writes=None, metrics=None, texter=None, transcript=None,
                 workspace=None, event_sink=None, checkpoint=None, cache_scope=None,
                 auto_acknowledge_unknown=False,
                 max_identical_failures=DEFAULT_MAX_IDENTICAL_FAILURES,
                 max_stale_observations=DEFAULT_MAX_STALE_OBSERVATIONS):
        self.driver = driver
        self.provider = provider
        self.policy = policy or WritePolicy()
        if isinstance(self.policy, WritePolicy) and not self.policy.write_actions:
            self.policy.write_actions = write_actions(provider)
            self.policy.recipient_gated = {
                spec.name for spec in provider.specs() if spec.recipient_gate
            }
        self.budget = Budget(max_steps=max_steps, max_writes=max_writes)
        self.metrics = metrics or RunMetrics()
        self.live = live
        # Non-live runs (external writes dry-run) may auto-acknowledge
        # persisted UNKNOWN effects on continuation; live runs keep the
        # explicit resolution contract — a real external effect may exist.
        self.auto_acknowledge_unknown = auto_acknowledge_unknown
        self._text = texter or generate_text
        self._event_sink = event_sink
        self._checkpoint_hook = checkpoint
        self._cache_scope = cache_scope
        self.workspace = workspace or Workspace()
        self.transcript = transcript
        self.trace = []
        self._spec = {spec.name: spec for spec in provider.specs()}
        self.max_identical_failures = max_identical_failures
        self.max_stale_observations = max_stale_observations

    async def run(self, goal, before_step=None):
        if self.transcript is None:
            self.workspace.begin_turn(goal)
            self.transcript = Transcript(
                system_prompt(self.provider, cache_scope=self._cache_scope), goal)
        else:
            inserted = self.transcript.repair()
            if inserted:
                await self._checkpoint()
                self.workspace = rebuild_workspace(self.transcript)
            # Independent of repair activity: any durable UNKNOWN (a
            # crash-completed dispatch OR a recorded uncertain effect) blocks
            # new automatic turns until explicitly resolved. Non-live runs may
            # auto-acknowledge: no external effect was possible, so the worst
            # case is an uncertain sandbox/workspace state, retained as
            # ledger evidence instead of freezing every later turn.
            pending = self.transcript.unresolved_unknown_calls()
            if pending and self.auto_acknowledge_unknown:
                await self._emit({
                    "type": "unknown_acknowledged",
                    "count": len(pending),
                    "policy": "auto_non_live",
                })
                self.transcript.resolve_unknowns(
                    note="unknown effects auto-acknowledged (isolated local "
                         "sandbox with network disabled)")
                await self._checkpoint()
                self.workspace = rebuild_workspace(self.transcript)
                inserted, pending = 0, []
            if inserted or pending:
                await self._checkpoint()
                self.workspace = rebuild_workspace(self.transcript)
                # This turn never began and produced no answer. Keep the prior
                # answer available as history, but never publish it as if the
                # RESTORE-only turn answered the new goal.
                self.workspace.prior_answer = self.workspace.answer
                self.workspace.answer = ""
                message = ("session has unresolved UNKNOWN effects; the run "
                           "stops before any decision (resolve explicitly to "
                           "continue)")
                outcome = {
                    "status": "stopped",
                    "reason": message,
                    "effect_disposition": "UNKNOWN",
                    "error": {"code": "EFFECT_UNKNOWN", "kind": "execution",
                              "stage": "persistence", "recoverability": "unsafe",
                              "message": message,
                              "details": {"repaired_calls": inserted,
                                          "unresolved_calls": pending},
                              "related_observation_ids": []},
                }
                step = {
                    "decision": self._decision_view({
                        "operation": "RESTORE", "target": None,
                        "confidence": None, "latency_ms": None,
                    }),
                    "outcome": outcome, "termination": "EFFECT_UNKNOWN",
                }
                self.budget.denials.append(message)
                self.metrics.denied(message)
                await self._emit({"type": "run_limit", "code": "EFFECT_UNKNOWN",
                                  "outcome": outcome})
                self.trace.append(step)
                await self._checkpoint()
                yield step
                final = {"final": "stopped", "steps": self.budget.steps,
                         "writes": self.budget.writes,
                         "denials": self.budget.denials}
                self.trace.append(final)
                await self._checkpoint()
                yield final
                return
            self.workspace.begin_turn(goal)
            self.transcript.append_user(goal)

        run_state = "running"
        while run_state == "running":
            if not self.budget.can_start_step():
                async for limit_step in self._terminate_run(
                        *self._limit_reason("STEP_BUDGET_EXHAUSTED")):
                    yield limit_step
                run_state = "stopped"
                break
            # One reserved step per attempt, before any model work: rejected and
            # refused decisions are never free, and the counter never exceeds max_steps.
            self.budget.reserve_step()
            attempt_id = uuid.uuid4().hex
            await self._emit({"type": "attempt_started", "attempt_id": attempt_id,
                              "step": self.budget.steps})
            helper_start = len(self.metrics.helper_calls)
            step = {"model_calls": []}
            proposal = decision = intent = None
            dispatch_started = False
            outcome = None
            stage = "decision"
            try:
                try:
                    proposal = await self.driver.decide(DriverContext(
                        goal=goal,
                        workspace=self.workspace,
                        transcript=self.transcript,
                        provider=self.provider,
                        write_min_confidence=self.policy.min_confidence,
                    ))
                except AttemptFailure:
                    raise
                except ValueError as failure:  # untyped decision-shape errors
                    raise InvalidProposal(str(failure),
                                          helper_info=getattr(failure, "helper_info", None),
                                          request=getattr(failure, "request", None),
                                          base_decision=getattr(failure, "base_decision", None),
                                          model_calls=getattr(failure, "model_calls", None),
                                          assistant_message=getattr(failure, "assistant_message", None),
                                          pending_call_id=getattr(failure, "pending_call_id", None),
                                          ) from failure
                self._record_model_metrics(proposal)
                if proposal.transcript_note:
                    self.transcript.append_note(proposal.transcript_note)
                if proposal.assistant_message:
                    self._commit_assistant(
                        proposal.assistant_message,
                        proposal.decision.get("ledger_call_id"))
                decision = proposal.decision
                step = {
                    "decision": self._decision_view(decision),
                    "request": proposal.request,
                    "model_calls": list(proposal.model_calls),
                }
                if proposal.escalation:
                    step["escalation"] = proposal.escalation
                selected_spec = self._spec.get(decision["operation"])
                needs_authoring = bool(
                    ((selected_spec and selected_spec.needs_text)
                     or decision["operation"] == "ANSWER")
                    and not decision.get("ledger_content")
                )
                await self._emit({
                    "type": "decision_ready",
                    "attempt_id": attempt_id,
                    "operation": decision["operation"],
                    "needs_authoring": needs_authoring,
                })
                stage = "authoring"
                intent = await self._materialize(decision)
            except AttemptFailure as failure:
                if decision is None:
                    base = failure.base_decision or {}
                    if failure.assistant_message is not None and failure.pending_call_id:
                        self._commit_assistant(failure.assistant_message,
                                               failure.pending_call_id)
                        base = {**base, "ledger_call_id": failure.pending_call_id}
                    decision = base or None
                outcome = self._failure_outcome(failure, stage, intent, dispatch_started)
                self._account_failure(failure, step, decision)
            except GuardrailDenied as denied:
                outcome = self._failure_outcome(denied, stage, intent, dispatch_started)
                self._account_failure(denied, step, decision)
            except Exception as error:  # noqa: BLE001 - never silently model-repairable
                outcome = self._unexpected_outcome(error, stage, intent, dispatch_started)

            # Durable region: an intent means complete frozen arguments exist.
            # Persistence failures here escape (fail closed) rather than becoming
            # a fabricated observation.
            if intent is not None:
                if intent.get("helper"):
                    step["model_calls"].append(intent["helper"])
                if decision.get("ledger_call_id") is None and \
                        intent["operation"] not in NON_LEDGER_ACTIONS:
                    # Synthesize the executable tool call BEFORE the pre-dispatch
                    # checkpoint: a crash mid-dispatch restores to a ledger whose
                    # committed call is present (answerable as UNKNOWN), and
                    # refusals answer the same call as executions do.
                    decision["ledger_call_id"] = self.transcript.append_action(
                        intent["operation"], self._ledger_arguments(intent))
                await self._checkpoint()
                await self._emit(self._intent_event(intent))
                step["intent_id"] = intent["intent_id"]

            if outcome is None:
                stage = "preflight"
                try:
                    action = "continue"
                    if before_step:
                        action = before_step(decision)
                        if inspect.isawaitable(action):
                            action = await action
                    if action == "abort":
                        step["aborted"] = True
                        outcome = {
                            "status": "stopped",
                            "reason": "run aborted before execution",
                            "effect_disposition": "NOT_APPLIED",
                            "error": {
                                "code": "RUN_ABORTED", "kind": "policy",
                                "stage": "preflight", "recoverability": "terminal",
                                "message": "run aborted before execution",
                                "details": {}, "related_observation_ids": [],
                            },
                        }
                    else:
                        self._refuse_repeats(intent)
                        review(decision, self.policy, self.budget,
                               self.workspace.recipients)
                        stage = "dispatch"
                        await self._emit({
                            "type": "dispatch_started",
                            "intent_id": intent["intent_id"],
                            "operation": intent["operation"],
                        })
                        dispatch_started = True
                        outcome = await self._dispatch(intent)
                except AttemptFailure as failure:
                    outcome = self._failure_outcome(failure, stage, intent,
                                                    dispatch_started)
                    self._account_failure(failure, step, decision)
                except GuardrailDenied as denied:
                    outcome = self._failure_outcome(denied, stage, intent,
                                                    dispatch_started)
                    self._account_failure(denied, step, decision)
                except Exception as error:  # noqa: BLE001 - as above
                    outcome = self._unexpected_outcome(error, stage, intent,
                                                       dispatch_started)

            outcome = self._normalize_outcome(intent, outcome)
            observation = self._make_observation(
                attempt_id, decision, intent, outcome,
                escalation_reason=(step.get("escalation") or {}).get("reason"),
                dispatched=dispatch_started)
            step["outcome"] = outcome
            error = outcome.get("error")
            if error and (error.get("stage") or "dispatch") != "dispatch":
                step["denied"] = error.get("message")
                self.budget.denials.append(step["denied"])
                self.metrics.denied(step["denied"])
            await self._emit(self._observation_event(observation))
            self._commit_observation(decision, intent, observation)
            self._append_history(decision, step, observation, intent, dispatch_started)
            operation = observation["operation"] or "INVALID_PROPOSAL"
            self.metrics.step(self.driver.name, operation, helper_start)
            self.trace.append(step)
            await self._checkpoint()
            yield step
            run_state = self._next_run_state(observation)
            if run_state == "running":
                limit = self._progress_limit()
                if limit is not None:
                    async for limit_step in self._terminate_run(
                            *self._limit_reason(limit)):
                        yield limit_step
                    run_state = "stopped"

        final = {
            "final": run_state,
            "steps": self.budget.steps,
            "writes": self.budget.writes,
            "denials": self.budget.denials,
        }
        self.trace.append(final)
        await self._checkpoint()
        yield final

    async def accept_unknown_effects(self):
        """The explicit resolution contract: mark every ledger UNKNOWN as
        accepted by the operator and checkpoint, so a later turn may proceed
        with the uncertainty retained as evidence."""
        self.transcript.resolve_unknowns()
        await self._checkpoint()

    # -- attempt accounting ----------------------------------------------------

    def _account_failure(self, failure, step, decision):
        """Record the model work a failed attempt already paid for, exactly once."""
        if getattr(failure, "base_decision", None) and self.driver.name == "jev":
            self.metrics.jev(failure.base_decision)
        helper = getattr(failure, "helper_info", None)
        model_calls = getattr(failure, "model_calls", None)
        if model_calls and not step["model_calls"]:
            step["model_calls"].extend(model_calls)
        if helper:
            kind = ("authoring" if isinstance(failure, MalformedAuthoredValue)
                    else ("arbitration" if self.driver.name == "jev"
                          else "plain_decision"))
            self.metrics.helper(helper, kind=kind)
            if helper not in step["model_calls"]:
                step["model_calls"].append(helper)
        if "decision" not in step:
            view_decision = decision or {
                "operation": "INVALID", "target": None, "confidence": None,
                "latency_ms": (helper or {}).get("latency_ms"),
            }
            step["decision"] = self._decision_view(view_decision)
            step["request"] = getattr(failure, "request", None)

    def _failure_outcome(self, failure, stage, intent, dispatch_started):
        """Normalize one typed refusal/failure into an outcome. Pre-dispatch
        failures are NOT_APPLIED; a typed failure raised after a mutating
        dispatch cannot claim that, so UNKNOWN wins."""
        recoverability = getattr(failure, "recoverability", "terminal")
        code = getattr(failure, "code", "POLICY_DENIED")
        kind = getattr(failure, "kind", "policy")
        error_stage = getattr(failure, "stage", None) or stage
        mutating = bool(intent and (intent["write"] or intent["workspace_mutation"]))
        if dispatch_started and mutating and recoverability != "unsafe":
            recoverability = "unsafe"
            code = "EFFECT_UNKNOWN"
            kind = "execution"
            error_stage = "dispatch"
        status = "stopped" if recoverability == "unsafe" else "rejected"
        error = failure.error_view() if hasattr(failure, "error_view") else {
            "code": code, "kind": kind, "stage": error_stage,
            "recoverability": recoverability, "message": str(failure),
            "details": getattr(failure, "details", {}) or {},
            "related_observation_ids": list(getattr(failure, "related", []) or []),
        }
        error = {**error, "code": code, "kind": kind, "stage": error_stage,
                 "recoverability": recoverability}
        return {
            "status": status,
            "reason": getattr(failure, "message", str(failure)),
            "effect_disposition": "UNKNOWN" if recoverability == "unsafe" else "NOT_APPLIED",
            "error": error,
        }

    def _unexpected_outcome(self, error, stage, intent, dispatch_started):
        """Unexpected code/infra faults are terminal infrastructure failures —
        unless a mutating dispatch may have begun, which is UNKNOWN."""
        reason = f"{type(error).__name__}: {error}"
        mutating = bool(intent and (intent["write"] or intent["workspace_mutation"]))
        if getattr(error, "code", None) == "MODEL_UNAVAILABLE" and not dispatch_started:
            return {
                "status": "failed",
                "reason": reason,
                "effect_disposition": "NOT_APPLIED",
                "error": {
                    "code": "MODEL_UNAVAILABLE",
                    "kind": "infrastructure",
                    "stage": stage or "decision",
                    "recoverability": "recoverable",
                    "message": reason,
                    "details": {},
                    "related_observation_ids": [],
                },
            }
        if dispatch_started and mutating:
            return {
                "status": "stopped", "reason": reason,
                "effect_disposition": "UNKNOWN",
                "error": {"code": "EFFECT_UNKNOWN", "kind": "execution",
                          "stage": "dispatch", "recoverability": "unsafe",
                          "message": reason, "details": {},
                          "related_observation_ids": []},
            }
        return {
            "status": "stopped", "reason": reason,
            "effect_disposition": "NOT_APPLIED",
            "error": {"code": "INTERNAL_ERROR", "kind": "infrastructure",
                      "stage": stage or "decision", "recoverability": "terminal",
                      "message": reason, "details": {},
                      "related_observation_ids": []},
        }

    def _normalize_outcome(self, intent, outcome):
        """Fill the typed classification a provider outcome may have left open.
        UNKNOWN always wins over an optimistic recoverable label."""
        outcome = dict(outcome or {})
        mutating = bool(intent and (intent["write"] or intent["workspace_mutation"]))
        disposition = outcome.get("effect_disposition")
        if disposition is None:
            if outcome.get("dry_run"):
                disposition = "PLANNED"
            elif outcome.get("status") in {"blocked", "failed"}:
                if mutating and outcome.get("effect_proof") != "pre_effect":
                    disposition = "UNKNOWN"
                else:
                    disposition = "NOT_APPLIED"
            elif outcome.get("status") == "stopped":
                disposition = "NOT_APPLIED"
            else:
                disposition = "SUCCEEDED"
        error = outcome.get("error")
        if error is None:
            if disposition == "UNKNOWN":
                error = {"code": "EFFECT_UNKNOWN", "kind": "execution",
                         "stage": "dispatch", "recoverability": "unsafe",
                         "message": outcome.get("reason")
                         or "complete effect could not be established",
                         "details": {}, "related_observation_ids": []}
            elif disposition == "NOT_APPLIED" and outcome.get("status") in {"blocked", "failed"}:
                error = {"code": "EXECUTION_FAILED", "kind": "execution",
                         "stage": "dispatch", "recoverability": "recoverable",
                         "message": outcome.get("reason") or "execution failed",
                         "details": {}, "related_observation_ids": []}
        elif disposition == "UNKNOWN" and isinstance(error, dict):
            error = {**error, "recoverability": "unsafe"}
        outcome["effect_disposition"] = disposition
        outcome["error"] = error
        return outcome

    def _make_observation(self, attempt_id, decision, intent, outcome,
                          escalation_reason=None, dispatched=False):
        decision = decision or {}
        source = (intent or decision).get("operation")
        return {
            "observation_id": uuid.uuid4().hex,
            "attempt_id": attempt_id,
            "intent_id": intent["intent_id"] if intent else None,
            "operation": source,
            "target": (intent or decision).get("target"),
            "phase": decision.get("phase"),
            "disposition": outcome.get("effect_disposition"),
            "dispatched": bool(dispatched and intent),
            "outcome": outcome,
            "fingerprints": {
                "intent": intent_fingerprint(intent) if intent else None,
                "observation": observation_fingerprint(outcome),
            },
            "budget": {"steps": self.budget.steps, "writes": self.budget.writes},
            "provenance": {
                "decision_source": self.driver.name,
                "arbitration_reason": escalation_reason,
            },
        }

    @staticmethod
    def _observation_event(observation):
        return {
            "type": "observation",
            "observation_id": observation["observation_id"],
            "attempt_id": observation["attempt_id"],
            "intent_id": observation["intent_id"],
            "operation": observation["operation"],
            "target": observation["target"],
            "disposition": observation["disposition"],
            "dispatched": observation["dispatched"],
            "outcome": observation["outcome"],
            "fingerprints": observation["fingerprints"],
            "budget": observation["budget"],
            "provenance": observation["provenance"],
        }

    # -- transcript finalization ------------------------------------------------

    def _commit_assistant(self, message, executed_call_id):
        """Append a genuine assistant turn and close every sibling call that a
        single-action loop will not execute."""
        self.transcript.append_assistant(message)
        for call in message.get("tool_calls") or []:
            call_id = call.get("id")
            if call_id not in (None, executed_call_id):
                self.transcript.append_result(call_id, {
                    "action": "superseded",
                    "reason": "only the first tool call of the turn is executed",
                    "effect_disposition": "NOT_APPLIED",
                    "superseded": True,  # replay must not count these as attempts
                })

    def _commit_observation(self, decision, intent, observation):
        """One finalizer for every attempt: answer the committed tool call, or
        append a provenance-safe runtime note when no provider-valid call or
        complete intent exists."""
        outcome = observation["outcome"]
        operation = observation["operation"]
        call_id = (decision or {}).get("ledger_call_id")
        if call_id is not None:
            record_execution(
                self.transcript,
                operation,
                self._ledger_arguments(intent) if intent else {},
                outcome,
                self.workspace,
                call_id=call_id,
                observation=observation,
            )
            return
        error = outcome.get("error") or {}
        note_text = (
            f"[runtime observation] {operation or 'decision'} was not applied: "
            f"{error.get('code', 'not applied')} "
            f"({error.get('stage', 'runtime')}, effect "
            f"{observation['disposition']}): "
            f"{str(outcome.get('reason') or error.get('message') or '')[:300]}"
        )
        self.transcript.append_runtime_note(note_text, meta={
            "runtime": True,
            "observation_id": observation["observation_id"],
            "attempt_id": observation["attempt_id"],
            "operation": operation,
            "target": observation["target"],
            "phase": observation.get("phase"),
            "status": outcome.get("status"),
            "disposition": observation["disposition"],
            "dispatched": observation.get("dispatched", False),
            "error": bounded_error(outcome.get("error")),
        })

    def _append_history(self, decision, step, observation, intent, dispatch_started):
        decision = decision or {}
        confidence = decision.get("confidence")
        confidence_text = (
            f"{confidence:.2f}" if isinstance(confidence, (int, float)) else "n/a"
        )
        outcome = observation["outcome"]
        observable = {
            key: outcome.get(key)
            for key in ("action", "output", "reason", "text", "answer")
            if outcome.get(key) is not None
        }
        self.workspace.append_history({
            "phase": decision.get("phase"),
            "operation": observation["operation"],
            "target": observation["target"],
            "status": outcome.get("status"),
            "exit": outcome.get("exit"),
            "command_excerpt": (
                str(intent.get("text", ""))[:300]
                if intent and observation["operation"] == "BASH" else None
            ),
            "output_excerpt": str(observable)[-700:] if observable else None,
            "intent_fingerprint": observation["fingerprints"]["intent"],
            "observation_fingerprint": observation["fingerprints"]["observation"],
            "disposition": observation["disposition"],
            "dispatched": bool(dispatch_started and intent),
            "read_files": outcome.get("read_files"),
            "changed_files": outcome.get("changed_files"),
            "files_may_have_changed": outcome.get("files_may_have_changed"),
            "error": bounded_error(outcome.get("error")),
            "observation_id": observation["observation_id"],
            "attempt_id": observation["attempt_id"],
            "summary": f"{observation['operation'] or 'decision'}"
                       f"({self.label(observation['target'])}) "
                       f"conf={confidence_text}"
                       + (f" denied: {step.get('denied')}" if step.get("denied") else "")
                       + (" [escalated]" if decision.get("arbitrated") else ""),
        })

    # -- generic loop limits ------------------------------------------------------

    def _refuse_repeats(self, intent):
        """After two identical materialized intents yielded identical substantive
        observations, the next identical intent must not dispatch. Derived from
        the whole current-turn attempt ledger (dispatched entries only), so an
        appended rejection can never re-enable the blocked call."""
        mark = intent_fingerprint(intent)
        dispatched = [
            item for item in self.workspace.current_turn_history()
            if item.get("intent_fingerprint") == mark and item.get("dispatched")
        ]
        if len(dispatched) >= 2:
            observations = [item.get("observation_fingerprint")
                            for item in dispatched[-2:]]
            if observations[0] and observations[0] == observations[1]:
                raise DuplicateNoProgress(
                    f"{intent['operation']} repeated twice with the same arguments "
                    "and observation; refusing a third execution without new "
                    "information.",
                    details={"intent_fingerprint": mark},
                    related=[item.get("observation_id") for item in dispatched[-2:]],
                )

    def _failure_signature(self, item):
        """stage + operation/target anchor + stable error code. Excludes prose,
        ids, timestamps and usage, so nothing observable resets the counter."""
        error = item.get("error") or {}
        if not error.get("code"):
            return None
        anchor = item.get("intent_fingerprint") or fingerprint(
            {"operation": item.get("operation"), "target": item.get("target")})
        return f"{error.get('stage')}|{anchor}|{error.get('code')}"

    def _progress_limit(self):
        """Generic bounded termination: repeated identical failed recovery
        cannot loop forever, and changing-but-unproductive proposals still hit
        the stale-observation bound. max_steps remains the final bound."""
        counts = {}
        for item in self.workspace.current_turn_history():
            signature = self._failure_signature(item)
            if signature is None:
                continue
            counts[signature] = counts.get(signature, 0) + 1
            if counts[signature] >= self.max_identical_failures:
                return "NO_PROGRESS_LIMIT"
        seen, stale = set(), 0
        for item in self.workspace.current_turn_history():
            mark = item.get("observation_fingerprint")
            if mark and mark not in seen:
                seen.add(mark)
                stale = 0
            else:
                stale += 1
                if stale >= self.max_stale_observations:
                    return "NO_PROGRESS_LIMIT"
        return None

    def _limit_reason(self, code):
        if code == "STEP_BUDGET_EXHAUSTED":
            return code, f"Step budget exhausted ({self.budget.max_steps})."
        return code, (
            f"No-progress limit reached (at most "
            f"{self.max_identical_failures - 1} recoveries per identical failure, "
            f"{self.max_stale_observations} observations without new evidence); "
            "stopping."
        )

    async def _terminate_run(self, code, message):
        """A run-limit termination is an appended record, not a free action
        attempt: it charges nothing and invents no policy failure."""
        kind = "budget" if code == "STEP_BUDGET_EXHAUSTED" else "no_progress"
        outcome = {
            "status": "stopped",
            "reason": message,
            "effect_disposition": "NOT_APPLIED",
            "error": {"code": code, "kind": kind, "stage": "preflight",
                      "recoverability": "terminal", "message": message,
                      "details": {}, "related_observation_ids": []},
        }
        step = {
            "decision": self._decision_view({
                "operation": "RUN_LIMIT", "target": None, "confidence": None,
                "latency_ms": None,
            }),
            "denied": message, "outcome": outcome, "termination": code,
        }
        self.workspace.append_history({
            "operation": None,
            "status": "stopped",
            "disposition": "NOT_APPLIED",
            "error": bounded_error(outcome["error"]),
            "dispatched": False,
            "summary": f"run limit: {code}",
        })
        self.budget.denials.append(message)
        self.metrics.denied(message)
        await self._emit({"type": "run_limit", "code": code, "outcome": outcome})
        self.trace.append(step)
        await self._checkpoint()
        yield step

    # -- materialization and dispatch ---------------------------------------------

    async def _materialize(self, decision):
        operation = decision["operation"]
        target = decision.get("target")
        spec = self._spec.get(operation)
        if isinstance(target, (list, tuple)):
            if (
                not spec
                or spec.multi_target_max <= 1
                or not 2 <= len(target) <= spec.multi_target_max
                or len(set(target)) != len(target)
            ):
                raise InvalidProposal(
                    f"{operation} received an invalid multi-target selection.",
                    details={"operation": operation, "target_count": len(target)},
                )
            target = tuple(target)
        if spec and spec.needs_target and not target:
            raise InvalidProposal(
                f"{operation} needs a target",
                details={"operation": operation},
            )
        field = text_field_for(operation)
        text = decision.get("ledger_content")
        helper = None
        if text is not None:
            text = validate_authored_value(text)
        if ((spec and spec.needs_text) or operation == "ANSWER") and not text:
            text, helper = await self._invoke_texter(
                self._text_instruction(operation, target, decision.get("phase")),
                field,
                operation,
            )
            try:
                text = _clean(text, field=field, operation=operation)
            except MalformedAuthoredValue as error:
                error.helper_info = helper
                raise
            self.metrics.helper(helper, kind="authoring")
        decision["ledger_content"] = text
        self._reject_operation_echo(operation, text)
        intent_id = uuid.uuid4().hex
        idempotency_key = hashlib.sha256(intent_id.encode()).hexdigest()[:50]
        return {
            "intent_id": intent_id,
            "idempotency_key": idempotency_key,
            "operation": operation,
            "target": target,
            "phase": decision.get("phase"),
            "text": text,
            "helper": helper,
            "write": bool(spec and spec.write),
            "workspace_mutation": bool(spec and spec.mutates_workspace),
        }

    async def _invoke_texter(self, instruction, field, operation):
        """Call the authoring helper with supported typed-envelope metadata."""
        texter = self._text
        try:
            parameters = inspect.signature(texter).parameters
        except (TypeError, ValueError):
            parameters = {}
        kwargs = {}
        if "field" in parameters:
            kwargs["field"] = field
        if "operation" in parameters:
            kwargs["operation"] = operation
        return await texter(self.transcript, instruction, **kwargs)

    def _intent_event(self, intent):
        text = intent.get("text")
        return {
            "type": "intent",
            "intent_id": intent["intent_id"],
            "operation": intent["operation"],
            "target": intent.get("target"),
            "write": intent["write"],
            "workspace_mutation": intent["workspace_mutation"],
            "live": self.live,
            "text_sha256": (
                hashlib.sha256(text.encode()).hexdigest() if text is not None else None
            ),
            "text_length": len(text) if text is not None else 0,
        }

    async def _emit(self, event):
        if self._event_sink is None:
            return
        result = self._event_sink(event)
        if inspect.isawaitable(result):
            await result

    async def _checkpoint(self):
        if self._checkpoint_hook is None or self.transcript is None:
            return
        result = self._checkpoint_hook(self.transcript)
        if inspect.isawaitable(result):
            await result

    @staticmethod
    def _ledger_arguments(intent):
        arguments = {}
        target = intent.get("target")
        if isinstance(target, (list, tuple)):
            arguments["targets"] = list(target)
        elif target:
            arguments["target"] = target
        text = intent.get("text")
        if text is None:
            return arguments
        operation = intent["operation"]
        if operation == "ANSWER":
            arguments["answer"] = text
        elif operation.startswith("SEARCH"):
            arguments["query"] = text
        elif operation == "BASH":
            arguments["command"] = text
        else:
            arguments["content"] = text
        return arguments

    @staticmethod
    def _next_run_state(observation):
        outcome = observation["outcome"]
        error = outcome.get("error") or {}
        if outcome.get("effect_disposition") == "UNKNOWN":
            if outcome.get("resolved") and outcome.get("continuation") == "allowed":
                return "running"
            return "stopped"
        if error.get("recoverability") in {"terminal", "unsafe"}:
            return "stopped"
        operation = observation["operation"]
        if operation in {"ANSWER", "DONE"} and \
                outcome.get("effect_disposition") == "SUCCEEDED":
            return "completed"
        # BLOCKED is terminal only when it was actually dispatched (no typed
        # error); a rejected proposal that happened to base on BLOCKED is a
        # recoverable INVALID_PROPOSAL and the loop continues.
        if operation == "BLOCKED" and not error:
            return "stopped"
        return "running"

    async def _dispatch(self, intent):
        operation = intent["operation"]
        target = intent.get("target")
        text = intent.get("text")
        helper = intent.get("helper")
        if operation in CORE_ACTIONS and operation != "ANSWER":
            if operation == "DONE":
                return {"status": "done", "action": "done"}
            return {"status": "stopped", "reason": "no supported operation can progress"}

        if operation == "ANSWER":
            self.workspace.answer = text
            return {
                "status": "done", "action": "answer", "answer": text,
                "text": text[:4000], "helper": self._helper_summary(helper),
            }
        try:
            outcome = await self.provider.execute(operation, ToolContext(
                workspace=self.workspace,
                target=target,
                text=text,
                live=self.live,
                intent_id=intent["intent_id"],
                idempotency_key=intent["idempotency_key"],
            ))
            if text:
                outcome.setdefault("text", text[:4000])
        except Exception as error:  # noqa: BLE001 - effect may be indeterminate
            reason = f"{type(error).__name__}: {error}"
            if intent["write"] or intent["workspace_mutation"]:
                # a mutating dispatch that raised cannot prove it did not apply
                return {"status": "stopped", "reason": reason,
                        "effect_disposition": "UNKNOWN"}
            # nonmutating: no effect by the tool's own contract
            return {"status": "failed", "reason": reason}
        if helper:
            outcome.setdefault("helper", self._helper_summary(helper))
        return outcome

    @staticmethod
    def _helper_summary(helper):
        if not helper:
            return None
        return {
            key: helper.get(key)
            for key in ("kind", "model", "latency_ms", "usage")
            if helper.get(key) is not None
        }

    def _text_instruction(self, operation, target, phase=None):
        base = ANSWER_TEXT if operation == "ANSWER" else self._spec[operation].text_instruction
        target_text = f" Target: {self.label(target)}." if target else ""
        purpose = f" Purpose: {phase}." if phase else ""
        return (
            f"For the {operation} action of the user's goal.{purpose}{target_text} {base} "
            f"The user's goal: {self.workspace.goal} "
            "Respond with ONLY the value itself — no commentary, no code fences. "
            f"The value fills one parameter of the {operation} tool call: it is "
            "not a reply to the user and never a repetition of earlier tool "
            "calls or operation names."
        )

    def _reject_operation_echo(self, operation, text):
        """Reject a first-line catalog token such as ``READ_FILE main.py``.

        Matching is deliberately case-sensitive: provider operation names are
        uppercase, while ``bash script.sh`` is a valid shell command.
        """
        first = (text or "").splitlines()[0].strip() if text else ""
        head = first.split(None, 1)[0].strip(";|&(") if first else ""
        if head in self._spec:
            raise MalformedAuthoredValue(
                f"Authored value echoes the operation name {head.upper()!r} "
                f"instead of {operation} content; nothing was dispatched.",
                details={"first_line": repr(first[:80])},
            )

    def _record_model_metrics(self, proposal):
        if self.driver.name == "jev":
            self.metrics.jev(proposal.base_decision)
        if proposal.helper_info:
            kind = "arbitration" if self.driver.name == "jev" else "plain_decision"
            self.metrics.helper(proposal.helper_info, kind=kind)
        if proposal.arbitration_pick:
            self.metrics.escalate(proposal.base_decision, proposal.arbitration_pick)

    def _decision_view(self, decision):
        view = {key: value for key, value in decision.items()
                if key not in {"request", "ledger_call_id", "ledger_content"}}
        view.setdefault("target", None)
        view.setdefault("confidence", None)
        view.setdefault("latency_ms", None)
        view["target_label"] = self.label(decision.get("target"))
        target_probs = sorted(
            (decision.get("target_probabilities") or {}).items(), key=lambda item: -item[1])
        view["target_probabilities_labeled"] = [
            {"key": key, "label": self.label(key), "p": probability}
            for key, probability in target_probs[:6]
        ]
        if decision.get("arbitrated") and self.driver.name == "jev":
            view["escalated"] = True
        return view

    def label(self, target):
        if not target:
            return ""
        if isinstance(target, (list, tuple)):
            return ", ".join(self.label(item) for item in target)
        return self.workspace.entry_label(target) or str(target)[:16]
