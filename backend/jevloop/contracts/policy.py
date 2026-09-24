"""Guardrails: typed attempt failures, confidence gates, write policy, budgets.

Writes are refuse-by-default. Every refusal carries stable typed metadata
(code / kind / stage / recoverability) so the runtime decides — without string
matching — whether the next iteration may redecide (recoverable), must stop
(terminal), or must preserve uncertainty (unsafe / UNKNOWN). Recoverability is
supplied by runtime and provider code only: neither model confidence nor
natural-language tool output can grant permission to retry.
"""

from dataclasses import dataclass, field

ERROR_KINDS = ("validation", "execution", "no_progress", "policy", "budget", "infrastructure")
ERROR_STAGES = ("decision", "authoring", "preflight", "dispatch", "persistence")
RECOVERABILITIES = ("recoverable", "terminal", "unsafe")


class EventSinkError(OSError):
    """Durable event recording failed; never optional or model-repairable."""


class AttemptFailure(Exception):
    """A typed failure of one loop attempt. The observation-first runtime turns
    this into the same durable, append-only observation as a successful step;
    only the classification changes the next run-state transition."""

    code = "EXECUTION_FAILED"
    kind = "execution"
    stage = "preflight"
    recoverability = "recoverable"

    def __init__(self, message, *, details=None, related=None, helper_info=None,
                 request=None, base_decision=None, model_calls=None,
                 assistant_message=None, pending_call_id=None):
        super().__init__(message)
        self.message = message
        self.details = details if details is not None else {}
        self.related = related or []
        self.helper_info = helper_info
        self.request = request
        self.base_decision = base_decision
        self.model_calls = model_calls or []
        self.assistant_message = assistant_message
        self.pending_call_id = pending_call_id

    def error_view(self):
        """The canonical error envelope persisted with the observation."""
        return {
            "code": self.code,
            "kind": self.kind,
            "stage": self.stage,
            "recoverability": self.recoverability,
            "message": self.message,
            "details": self.details,
            "related_observation_ids": list(self.related),
        }


class InvalidProposal(AttemptFailure):
    """A billed decision response that cannot become a safe proposal."""
    code = "INVALID_PROPOSAL"
    kind = "validation"
    stage = "decision"


class MalformedAuthoredValue(AttemptFailure):
    """Authored content failed typed validation before any dispatch."""
    code = "MALFORMED_AUTHORED_VALUE"
    kind = "validation"
    stage = "authoring"


class DuplicateNoProgress(AttemptFailure):
    """An exact repeat of an already-executed, already-observed attempt."""
    code = "DUPLICATE_NO_PROGRESS"
    kind = "no_progress"
    stage = "preflight"


class GuardrailDenied(Exception):
    """The action was refused before execution by policy or budget. Nothing
    was sent; the refusal is terminal for this run."""

    code = "POLICY_DENIED"
    kind = "policy"
    stage = "preflight"
    recoverability = "terminal"

    def __init__(self, message, *, code=None, details=None):
        super().__init__(message)
        self.message = message
        self.details = details if details is not None else {}
        if code is not None:
            self.code = code

    def error_view(self):
        return {
            "code": self.code,
            "kind": self.kind,
            "stage": self.stage,
            "recoverability": self.recoverability,
            "message": self.message,
            "details": self.details,
            "related_observation_ids": [],
        }


class BudgetDenied(GuardrailDenied):
    """A budget maximum was reached; counters never exceed the maxima."""

    kind = "budget"


@dataclass
class Budget:
    max_steps: int | None = 30
    max_writes: int | None = None
    steps: int = 0
    writes: int = 0
    denials: list = field(default_factory=list)

    def can_start_step(self) -> bool:
        return (
            self.max_steps is None
            or self.max_steps <= 0
            or self.steps < self.max_steps
        )

    def reserve_step(self):
        """Charge one decision attempt before any model work, so rejected and
        refused attempts are never free. Checks before incrementing: an
        attempted over-spend raises without moving the counter."""
        if not self.can_start_step():
            raise BudgetDenied(f"Step budget exhausted ({self.max_steps}).",
                               code="STEP_BUDGET_EXHAUSTED")
        self.steps += 1

    def spend_write(self, what):
        if (
            self.max_writes is not None
            and self.max_writes > 0
            and self.writes >= self.max_writes
        ):
            raise BudgetDenied(
                f"Write budget exhausted ({self.max_writes}); refused {what}.",
                code="WRITE_BUDGET_EXHAUSTED")
        self.writes += 1


@dataclass
class WritePolicy:
    """Write actions need confidence + budget; recipient-gated ones also need an
    explicitly allowed recipient. Both action sets come from the mounted tools."""

    allowed_recipients: set = field(default_factory=set)  # names, configured per run
    min_confidence: float = 0.6
    write_actions: set = field(default_factory=set)      # filled from ToolSpec.write
    recipient_gated: set = field(default_factory=set)    # filled from ToolSpec.recipient_gate

    def check(self, decision, recipients=None):
        operation = decision["operation"]
        if operation not in self.write_actions:
            return
        # an adjudicated decision carries the LLM's explicit endorsement for the
        # confidence gate; budgets and recipient allowlists still apply in full
        arbitrated = bool(decision.get("arbitrated"))
        if not arbitrated and decision["confidence"] < self.min_confidence:
            raise GuardrailDenied(
                f"Write {operation} confidence {decision['confidence']:.2f} < "
                f"{self.min_confidence}; refusing.",
                code="WRITE_CONFIDENCE_DENIED",
            )
        if operation not in self.recipient_gated:
            return
        target = decision.get("target")
        # A target is allowed by id or by the display name it resolves to.
        resolved = (recipients or {}).get(target, target)
        if resolved not in self.allowed_recipients:
            raise GuardrailDenied(
                f"Recipient '{resolved}' is not in the allowed set; refusing to send.",
                code="RECIPIENT_DENIED",
            )


def review(decision, policy, budget, recipients=None):
    """Raise GuardrailDenied instead of executing when a policy or write-budget
    rule fails. Step reservation is owned by the kernel loop (charged before any
    model work); this checks the action itself and spends the external-write
    allowance immediately before dispatch."""
    policy.check(decision, recipients)
    if decision["operation"] in policy.write_actions:
        budget.spend_write(decision["operation"])
