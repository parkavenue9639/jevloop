"""validate_choice must refuse anything that is not a legal argmax over a full distribution."""

import pytest

from jevloop.decision.model import validate_choice

IDS = {"search": "Search", "open": "Open", "done": "Done"}


def answer(choice="open", p=(0.1, 0.9, 0.0), confidence=0.8):
    probs = dict(zip(IDS, p))
    return {"choice": choice, "probabilities": probs, "confidence": confidence}


def test_valid_answer_passes():
    result = validate_choice(answer(), IDS)
    assert result["choice"] == "open"


@pytest.mark.parametrize(
    "bad",
    [
        answer(choice="unknown"),                       # choice not in candidates
        answer(p=(0.1, 0.9)),                           # missing candidate probability
        answer(p=(0.3, 0.9, -0.2)),                     # negative probability
        answer(p=(0.5, 0.6, 0.0)),                      # sums above 1 (tolerance 0.02)
        answer(choice="search", p=(0.1, 0.9, 0.0)),     # chosen is not the argmax
        {"choice": "open"},                             # missing fields entirely
        {"choice": "open", "probabilities": {"search": "x", "open": 1.0, "done": 0.0}, "confidence": 0.5},
    ],
)
def test_invalid_answers_are_refused(bad):
    with pytest.raises(ValueError):
        validate_choice(bad, IDS)


def test_sum_tolerance_accepts_float_noise():
    assert validate_choice(answer(p=(0.1, 0.895, 0.005)), IDS)["choice"] == "open"
