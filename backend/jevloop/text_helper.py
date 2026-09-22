"""LLM content generation on the ledger: the LLM continues its own transcript
with an instruction turn appended, and its reply IS the deliverable — recorded
verbatim as a genuine author turn (no JSON envelope, no ghost-writing).

Authored values are validated as typed content before anything dispatches: a
malformed protocol fragment, a closed parameter of the wrong name, a truncated
response, or an unexpected tool call fails as a recoverable
MALFORMED_AUTHORED_VALUE observation instead of being written as if it were the
requested value."""

import os
import re
import time

from .guardrails import MalformedAuthoredValue
from .model import post_json

_MAX_LEN = 20000
_DSML_PARAMETER = re.compile(
    r"(?P<open><[^>]*DSML[^>]*\bparameter\b[^>]*>)"
    r"(?P<value>.*?)</[^>]*DSML[^>]*\bparameter\s*>",
    re.IGNORECASE | re.DOTALL,
)
_DSML_TAG = re.compile(r"<[^>]*DSML[^>]*>", re.IGNORECASE)
_DSML_NAME = re.compile(r'\bname\s*=\s*"([^"]*)"', re.IGNORECASE)
_DSML_INVOKE_NAME = re.compile(
    r'<[^>]*DSML[^>]*\binvoke\b[^>]*\bname\s*=\s*"([^"]+)"',
    re.IGNORECASE,
)


def _only_dsml_tags(fragment: str) -> bool:
    """True when nothing but DSML packaging tags remain in the fragment."""
    return _DSML_TAG.sub("", fragment).strip() == ""


def _parameter_name(opening_tag: str):
    found = _DSML_NAME.search(opening_tag)
    return found.group(1) if found else None


def _reject_unterminated_fragment(text: str):
    if _DSML_PARAMETER.search(text) is None and _DSML_TAG.match(text):
        raise MalformedAuthoredValue(
            "Authored value is a malformed protocol fragment (unterminated "
            "DSML element); nothing was dispatched.",
            details={"excerpt": repr(text[:120])},
        )


def _strip_fences(text: str) -> str:
    if not text.startswith("```"):
        return text
    first_newline = text.find("\n")
    text = text[first_newline + 1:] if first_newline != -1 else text[3:]
    if not text.rstrip().endswith("```"):
        raise MalformedAuthoredValue(
            "Authored value is truncated (unclosed code fence); nothing "
            "was dispatched.",
            details={"excerpt": repr(text[:120])},
        )
    return text.rstrip()[:-3]


def _clean(text: str, field: str | None = None,
           operation: str | None = None) -> str:
    """Validate and unwrap one FREE-FORM authored value (an authoring reply).

    A complete provider serialization (DSML tags wrapping the whole response)
    is unwrapped to its parameter value — but only when it carries exactly ONE
    parameter and that closed parameter is the requested `field` (the typed
    envelope this value is for); a multi-parameter serialization is ambiguous
    protocol packaging, and a closed serialization of a DIFFERENT parameter
    never becomes the authored body. DSML-looking text inside a larger authored
    document is data and is kept verbatim. A response that begins with a DSML
    tag yet contains no complete parameter element is an unterminated wire
    fragment: it fails as MALFORMED_AUTHORED_VALUE instead of being accepted
    as the requested value."""
    text = text.strip()
    matches = list(_DSML_PARAMETER.finditer(text))
    serialized = bool(matches) and _only_dsml_tags(
        _DSML_PARAMETER.sub("", text)
    )
    if serialized:
        if len(matches) > 1:
            raise MalformedAuthoredValue(
                "Authored value is a multi-parameter protocol serialization, "
                "not one typed value; nothing was dispatched.",
                details={"parameter_count": len(matches),
                         "expected_field": field},
            )
        if operation is not None:
            invokes = _DSML_INVOKE_NAME.findall(text)
            if len(invokes) != 1 or invokes[0] != operation:
                raise MalformedAuthoredValue(
                    f"Authored value is a protocol call for "
                    f"{invokes[0] if invokes else 'unknown'!r}, not the "
                    f"requested {operation!r}; nothing was dispatched.",
                    details={"closed_operation": invokes[0] if invokes else None,
                             "expected_operation": operation},
                )
        match = matches[0]
        name = _parameter_name(match.group("open"))
        if field is not None and name != field:
            raise MalformedAuthoredValue(
                f"Authored value is a closed protocol parameter {name!r}, not "
                f"the requested {field!r}; nothing was dispatched.",
                details={"closed_parameter": name, "expected_field": field},
            )
        text = match.group("value").strip()
    else:
        _reject_unterminated_fragment(text)
    return _strip_fences(text).strip()


def validate_authored_value(text: str) -> str:
    """Validate a value that arrived through a TYPED envelope (a function-call
    argument). It is data, not packaging: a complete DSML serialization inside
    it is kept verbatim instead of being unwrapped; only unterminated wire
    fragments and truncated fences fail, as recoverable typed errors."""
    text = text.strip()
    _reject_unterminated_fragment(text)
    return _strip_fences(text).strip()


def _truncated_choice(choice) -> bool:
    return (choice or {}).get("finish_reason") == "length"


async def generate_text(transcript, instruction: str, post=None, field=None,
                        operation=None):
    """Append the instruction turn, let the LLM continue the ledger, record its
    author turn. Returns (text, helper_info). Raises MalformedAuthoredValue on
    refusal, unusable or truncated output, an unexpected tool call, or a closed
    parameter that is not the requested `field` — typed, recoverable pre-dispatch
    failures; nothing is appended to the ledger when one fires."""
    key = os.environ.get("DEEPSEEK_API_KEY") or ""
    if not key and post is None:  # injected posts (tests) don't need a real key
        raise ValueError("Text generation needs DEEPSEEK_API_KEY; nothing is hardcoded.")
    transcript.append_user(f"[content request] {instruction}")
    base = os.environ.get("TEXT_MODEL_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
    model = os.environ.get("TEXT_MODEL", "deepseek-chat")
    started = time.perf_counter()
    request = {"model": model, "max_tokens": 8192, "messages": transcript.llm_messages()}
    result = await (post or post_json)(
        base + "/chat/completions",
        key,
        request,
    )
    choice = result["choices"][0]
    message = choice["message"]
    helper_info = {
        "kind": "authoring",
        "model": model,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "usage": result.get("usage", {}),
        "request": request,
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
