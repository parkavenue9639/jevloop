"""Canonical model tool schemas, independent of transcript storage."""

CORE_TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "ANSWER",
        "description": "Deliver the final answer/summary to the user; ends the run.",
        "parameters": {"type": "object",
                       "properties": {"answer": {"type": "string"}},
                       "required": ["answer"]}}},
    {"type": "function", "function": {
        "name": "DONE",
        "description": "Declare the goal satisfied; nothing left to report.",
        "parameters": {"type": "object", "properties": {},
                       "required": []}}},
]


def tool_schemas(provider) -> list:
    """Schemas for arbitration. Same full-argument surface as the baseline:
    when the LLM adjudicates a text-bearing action it authors the content in
    the SAME turn (a native agent decides and writes together) — one call
    instead of two, and the ledger's every-tool_call-answered invariant holds
    without a dangling generation step in between.
    """
    return llm_tool_schemas(provider)


def llm_tool_schemas(provider) -> list:
    """Stable proposal catalog; availability and operation locks are NOT schemas.

    CANNOT_BIND is a non-executable refusal, never a provider capability.
    Return fresh canonical schemas so no caller can inject candidate bindings.
    """
    schemas = [schema for schema in full_tool_schemas(provider)
               if schema["function"]["name"] != "DONE"]
    if any(schema["function"]["name"] == "CANNOT_BIND" for schema in schemas):
        raise ValueError("CANNOT_BIND is reserved for non-executable parameter refusal.")
    schemas.append({"type": "function", "function": {
        "name": "CANNOT_BIND",
        "description": "Decline parameter generation without execution when evidence is insufficient. "
                       "Use only in response to a parameter request.",
        "parameters": {"type": "object", "properties": {
            "reason": {"type": "string", "minLength": 1}},
            "required": ["reason"], "additionalProperties": False},
    }})
    return sorted(schemas, key=lambda schema: schema["function"]["name"])


def full_tool_schemas(provider) -> list:
    """Function-calling schemas with complete arguments — the baseline's surface
    (the LLM authors queries/content/arguments itself, as in a native loop)."""
    from jevloop.contracts.arguments import function_schema

    return [function_schema(spec) for spec in provider.specs()] + [
        function_schema(operation="ANSWER"), function_schema(operation="DONE")]
