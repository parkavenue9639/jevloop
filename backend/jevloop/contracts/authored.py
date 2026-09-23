"""Pure typed/free-form authored-value validation; no model transport."""

import re

from jevloop.contracts.policy import MalformedAuthoredValue

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
