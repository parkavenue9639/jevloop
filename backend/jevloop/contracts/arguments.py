"""One typed argument contract for all decision paths.

Binding only copies observed references and declared defaults. This module never
inspects the goal or interprets natural-language user messages.
"""

from copy import deepcopy

from jsonschema import Draft202012Validator

from jevloop.contracts.policy import InvalidProposal
from jevloop.contracts.tools import text_field_for

LLM_PARAMETERS = "LLM_PARAMETERS"


def parameter_schema(spec=None, operation=None):
    operation = operation or spec.name
    if spec and spec.parameters is not None:
        return deepcopy(spec.parameters)
    props, required = {}, []
    schema = {"type": "object", "properties": props, "required": required,
              "additionalProperties": False}
    if spec and spec.needs_target:
        props["target"] = {"type": "string", "minLength": 1,
                           "description": "A known target reference."}
        if spec.multi_target_max > 1:
            props["targets"] = {"type": "array", "items": {"type": "string"},
                                "minItems": 2, "maxItems": spec.multi_target_max,
                                "uniqueItems": True}
            schema["oneOf"] = [{"required": ["target"]}, {"required": ["targets"]}]
        else:
            required.append("target")
    if operation == "ANSWER" or (spec and spec.needs_text):
        field = text_field_for(operation)
        props[field] = {"type": "string", "minLength": 1, "maxLength": 20000,
                        "description": spec.text_instruction if spec else "Final answer to the user."}
        required.append(field)
    return schema


def function_schema(spec=None, operation=None):
    operation = operation or spec.name
    return {"type": "function", "function": {
        "name": operation,
        "description": spec.description if spec else "Deliver the final answer." if operation == "ANSWER"
        else "Declare the goal satisfied.",
        "parameters": parameter_schema(spec, operation),
    }}


def apply_defaults(schema, arguments):
    value = deepcopy(arguments)
    for field, declaration in schema.get("properties", {}).items():
        if field not in value and "default" in declaration:
            value[field] = deepcopy(declaration["default"])
    return value


def arguments_complete(spec, arguments, operation=None):
    schema = parameter_schema(spec, operation)
    return Draft202012Validator(schema).is_valid(apply_defaults(schema, arguments))


def bound_arguments(spec, target):
    args = deepcopy(spec.binding_defaults or {})
    if target is not None and target != LLM_PARAMETERS:
        field = spec.target_parameter
        if isinstance(target, (list, tuple)):
            args["targets" if spec.parameters is None else field] = list(target)
        else:
            args[field] = target
    return args


def argument_target(spec, args):
    target = args.get(spec.target_parameter if spec else "target")
    if spec and spec.parameters is None and "targets" in args:
        target = args["targets"]
    return tuple(target) if isinstance(target, list) else target


def argument_text(spec, args, operation=None):
    operation = operation or spec.name
    field = "command" if spec and spec.parameters is not None and operation == "BASH" else text_field_for(operation)
    return args.get(field)


def validate_arguments(spec, args, workspace=None, operation=None):
    """Normalize values, validate schema and then enforce provider constraints.

Open locators are not restricted to the shortcut candidate set. Legacy domain
target pools remain closed references, with recipient authorization additionally
enforced by the shared policy. A model-authored value is never an authorization.
"""
    operation = operation or spec.name
    if not isinstance(args, dict):
        raise InvalidProposal(f"{operation} arguments must be an object.")
    schema = parameter_schema(spec, operation)
    args = apply_defaults(schema, args)
    errors = sorted(Draft202012Validator(schema).iter_errors(args), key=lambda e: str(e.path))
    if errors:
        raise InvalidProposal(f"Invalid {operation} arguments: {errors[0].message[:500]}",
                              details={"operation": operation, "path": list(errors[0].path)})
    if spec and spec.parameters is None and spec.needs_target:
        targets = args.get("targets", [args.get("target")])
        entries = workspace.pool_entries(spec.target_pool) if workspace else {}
        if spec.target_filter:
            entries = {key: entry for key, entry in entries.items() if spec.target_filter(entry)}
        allowed = set(entries) | {key for key, _label in spec.target_extra}
        for target in targets:
            if target not in allowed:
                raise InvalidProposal(f"{operation} target is not an observed compatible reference: {target!r}.")
    text = argument_text(spec, args, operation)
    if isinstance(text, str):
        from jevloop.contracts.authored import validate_authored_value
        # Validate protocol truncation but do not reinterpret a typed envelope.
        normalized_text = validate_authored_value(text)
        if spec is None or spec.parameters is None:
            args[text_field_for(operation)] = normalized_text
            if not Draft202012Validator(schema).is_valid(args):
                raise InvalidProposal(f"{operation} requires non-empty usable content.")
    if spec and spec.argument_validator:
        try:
            normalized = spec.argument_validator(deepcopy(args))
        except (TypeError, ValueError) as exc:
            raise InvalidProposal(f"Invalid {operation} arguments: {exc}") from exc
        if normalized is not None:
            args = normalized
        if not Draft202012Validator(schema).is_valid(args):
            raise InvalidProposal(f"{operation} argument normalizer violated its schema.")
    return args
