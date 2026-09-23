"""Guardrails: writes are refuse-by-default, gated on confidence and recipient policy."""

import pytest

from jevloop.contracts.policy import Budget, BudgetDenied, GuardrailDenied, WritePolicy, review

WRITES = {"SEND_MESSAGE", "REPLY_MESSAGE", "CREATE_DOC", "WRITE_DOC"}
GATED = {"SEND_MESSAGE", "REPLY_MESSAGE"}


def policy(**kwargs):
    return WritePolicy(write_actions=WRITES, recipient_gated=GATED, **kwargs)


def decision(operation, target=None, confidence=0.9):
    return {"operation": operation, "target": target, "confidence": confidence}


def test_read_operation_needs_no_write_budget():
    budget = Budget(max_steps=5, max_writes=1)
    review(decision("OPEN_CHAT", "c1"), policy(), budget)
    assert budget.writes == 0


def test_write_below_confidence_is_denied():
    with pytest.raises(GuardrailDenied, match="confidence"):
        review(decision("SEND_MESSAGE", "me", confidence=0.5), policy(min_confidence=0.8), Budget())


def test_write_to_unlisted_recipient_is_denied():
    with pytest.raises(GuardrailDenied, match="not in the allowed set"):
        review(decision("SEND_MESSAGE", "全公司大群", confidence=0.99),
               policy(allowed_recipients={"我自己"}, min_confidence=0.5), Budget())


def test_recipient_allowed_by_resolved_name():
    # Jev picks the chat by id; the policy allows display names.
    p = policy(allowed_recipients={"我自己"}, min_confidence=0.5)
    budget = Budget(max_steps=5, max_writes=5)
    recipients = {"oc_self": "我自己", "oc_big": "全公司大群"}
    review(decision("SEND_MESSAGE", "oc_self", confidence=0.9), p, budget, recipients)
    with pytest.raises(GuardrailDenied, match="not in the allowed set"):
        review(decision("SEND_MESSAGE", "oc_big", confidence=0.9), p, budget, recipients)


def test_allowed_write_passes_and_spends_budget():
    p = policy(allowed_recipients={"我自己"}, min_confidence=0.5)
    budget = Budget(max_steps=5, max_writes=1)
    review(decision("SEND_MESSAGE", "我自己", confidence=0.9), p, budget)
    assert budget.writes == 1
    with pytest.raises(GuardrailDenied, match="Write budget"):
        review(decision("SEND_MESSAGE", "我自己", confidence=0.9), p, budget)


def test_external_write_budget_is_unlimited_by_default():
    p = policy(allowed_recipients={"我自己"}, min_confidence=0.5)
    budget = Budget(max_steps=10)
    for _ in range(6):
        review(decision("SEND_MESSAGE", "我自己", confidence=0.9), p, budget)
    assert budget.writes == 6


def test_step_reservation_checks_before_incrementing():
    budget = Budget(max_steps=1, max_writes=5)
    budget.reserve_step()
    with pytest.raises(BudgetDenied, match="Step budget") as excinfo:
        budget.reserve_step()
    assert budget.steps == 1  # an attempted over-spend never moves the counter
    assert excinfo.value.code == "STEP_BUDGET_EXHAUSTED"
    assert excinfo.value.recoverability == "terminal"


def test_zero_step_budget_means_unlimited():
    budget = Budget(max_steps=0)
    for _ in range(100):
        budget.reserve_step()
    assert budget.steps == 100


def test_denials_carry_typed_terminal_metadata():
    with pytest.raises(GuardrailDenied) as excinfo:
        review(decision("SEND_MESSAGE", "me", confidence=0.5),
               policy(min_confidence=0.8), Budget())
    assert excinfo.value.code == "WRITE_CONFIDENCE_DENIED"
    assert excinfo.value.recoverability == "terminal"
    assert excinfo.value.stage == "preflight"


def test_recipient_denial_code_is_stable():
    with pytest.raises(GuardrailDenied) as excinfo:
        review(decision("SEND_MESSAGE", "stranger", confidence=0.99),
               policy(allowed_recipients={"我自己"}, min_confidence=0.5), Budget())
    assert excinfo.value.code == "RECIPIENT_DENIED"
    assert excinfo.value.error_view()["recoverability"] == "terminal"
