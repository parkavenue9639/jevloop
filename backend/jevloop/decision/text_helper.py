"""LLM content generation on the ledger: the LLM continues its own transcript
with an instruction turn appended, and its reply IS the deliverable — recorded
verbatim as a genuine author turn (no JSON envelope, no ghost-writing).

Authored values are validated as typed content before anything dispatches: a
malformed protocol fragment, a closed parameter of the wrong name, a truncated
response, or an unexpected tool call fails as a recoverable
MALFORMED_AUTHORED_VALUE observation instead of being written as if it were the
requested value."""

import time

from jevloop.contracts.authored import _MAX_LEN, _clean
from jevloop.contracts.policy import MalformedAuthoredValue
from jevloop.decision.llm_transport import model_target, prepare_request
from jevloop.decision.model import post_json


def _truncated_choice(choice) -> bool:
    return (choice or {}).get("finish_reason") == "length"


async def generate_text(transcript, instruction: str, post=None, field=None,
                        operation=None):
    """Append the instruction turn, let the LLM continue the ledger, record its
    author turn. Returns (text, helper_info). Raises MalformedAuthoredValue on
    refusal, unusable or truncated output, an unexpected tool call, or a closed
    parameter that is not the requested `field` — typed, recoverable pre-dispatch
    failures; nothing is appended to the ledger when one fires."""
    target = model_target()
    key = target.key
    if not key and post is None:  # injected posts (tests) don't need a real key
        raise ValueError("Text generation needs DEEPSEEK_API_KEY; nothing is hardcoded.")
    transcript.append_user(f"[content request] {instruction}")
    started = time.perf_counter()
    target, request, logged = prepare_request(transcript)
    result = await (post or post_json)(
        target.url,
        key,
        request,
    )
    choice = result["choices"][0]
    message = choice["message"]
    helper_info = {
        "kind": "authoring",
        "model": target.model,
        "visual": target.visual,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "usage": result.get("usage", {}),
        "request": logged,
        "response": message,
    }
    if _truncated_choice(choice):
        error = MalformedAuthoredValue(
            "Text generation was truncated by the token limit; nothing was "
            "written.",
            details={"finish_reason": "length"},
        )
        error.helper_info = helper_info
        raise error
    if message.get("tool_calls"):
        # never commit an authoring turn carrying tool calls: the kernel
        # synthesizes its own executable call, so these would dangle
        error = MalformedAuthoredValue(
            "Text generation returned tool calls instead of an authored value; "
            "nothing was written.",
            details={"tool_call_ids": [call.get("id")
                                       for call in message["tool_calls"]]},
        )
        error.helper_info = helper_info
        raise error
    try:
        text = _clean(message.get("content") or "", field=field,
                      operation=operation)
    except MalformedAuthoredValue as error:
        error.helper_info = helper_info
        raise
    if not text or len(text) > _MAX_LEN:
        excerpt = repr(text[:120])
        error = MalformedAuthoredValue(
            f"Text generation returned no usable value ({excerpt}); nothing was written.",
            details={"expected": f"non-empty string of at most {_MAX_LEN} chars",
                     "got_length": len(text)},
        )
        error.helper_info = helper_info
        raise error
    transcript.append_assistant(message)
    return text, helper_info
