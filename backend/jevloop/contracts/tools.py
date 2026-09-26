"""Mountable tool providers: the framework's action catalog comes from here.

A tool provider advertises ToolSpecs (what Jev sees as choices), decides which
tools are currently valid given the workspace, and executes a chosen tool.
The loop, guardrails and escalation layer are tool-agnostic.

Outcome contract: `execute` returns the step outcome dict (status/action/...).
Providers own truthful effect classification — a mutating tool (ToolSpec.write
or mutates_workspace) that fails after starting must NOT claim
effect_disposition="NOT_APPLIED" unless it affirmatively proves nothing ran,
by setting effect_proof="pre_effect". Otherwise leave the disposition unset and
the kernel conservatively records UNKNOWN and stops. Nonmutating tools and
provider-validated pre-effect failures are recoverable NOT_APPLIED.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolSpec:
    """One executable argument contract shared by every driver.

    Observed references and declared defaults provide optional bindings;
    LLM_PARAMETERS always allows the selected operation's open arguments.
    needs_target/needs_text adapt legacy providers, not canonical schemas.
    """
    name: str                      # action name Jev chooses, e.g. "OPEN_CHAT"
    description: str               # criteria text shown to Jev
    needs_text: bool = False       # LLM writes text before execution
    text_instruction: str = ""     # what the LLM should write (when needs_text)
    phases: tuple[str, ...] = ()    # semantic purposes; compiler derives a safe default
    consumes: tuple = ()           # context keys the text generation may use
    needs_target: bool = False     # requires a target from the workspace
    target_pool: str = ""          # candidate pool the target draws from
    target_filter: object = None   # callable(PoolEntry) -> bool compatibility filter
    target_extra: tuple = ()       # ((key, description), ...) synthetic candidates
    multi_target_max: int = 1     # >1 enables bounded target-set selection
    write: bool = False            # external mutation: policy/confidence/write budget
    mutates_workspace: bool = False  # isolated session-local mutation, step budget only
    recipient_gate: bool = False   # write whose target must be an allowed recipient
    parameters: dict | None = None  # canonical JSON-schema; None adapts legacy target/text
    target_parameter: str = "target"  # field bound from an observed target_pool reference
    binding_defaults: dict | None = None  # safe defaults, not task-dependent inference
    argument_validator: object = None  # pure, idempotent pre-dispatch validation/normalization
    observation_kinds: tuple[str, ...] = ()  # registered result-reference kinds, never raw text


@dataclass
class ToolContext:
    workspace: object
    target: str | tuple[str, ...] | None = None
    text: str | None = None
    live: bool = False
    intent_id: str | None = None
    idempotency_key: str | None = None
    arguments: dict | None = None  # complete, validated, frozen canonical arguments
    visual_read: object = None  # async(part) -> observation; injected, no decision authority


def text_field_for(operation: str) -> str:
    """The function-calling parameter that carries an action's authored text
    (mirrors transcript.full_tool_schemas: ANSWER -> answer, SEARCH* -> query,
    everything else -> content). Drivers decode arguments through this mapping
    instead of arbitrary fallback fields."""
    if operation == "ANSWER":
        return "answer"
    if operation.startswith("SEARCH"):
        return "query"
    return "content"


class ToolProvider:
    """Subclass and mount into RuntimeKernel. All methods are async."""

    def specs(self) -> list[ToolSpec]:
        return []

    def available(self, workspace) -> set[str]:
        """Which tool names are valid choices for the current state."""
        return {spec.name for spec in self.specs()}

    async def execute(self, name: str, ctx: ToolContext) -> dict:
        """Run a tool; return the step outcome dict (status/action/...)."""
        raise NotImplementedError(f"tool {name} not implemented")

    def label(self, name: str, target) -> str:
        """Human-readable label for traces and prompts (defaults to the raw key)."""
        return str(target or "")


def write_actions(provider) -> set[str]:
    return {spec.name for spec in provider.specs() if spec.write}


class CompositeProvider:
    """Mounts several providers as one. Earlier providers take priority: their
    specs come first in the action catalog Jev sees, and name conflicts resolve
    to the earliest mount."""

    def __init__(self, *providers):
        self.providers = providers

    def specs(self):
        seen, merged = set(), []
        for provider in self.providers:
            for spec in provider.specs():
                if spec.name not in seen:
                    seen.add(spec.name)
                    merged.append(spec)
        return merged

    def available(self, workspace):
        names = {spec.name for spec in self.specs()}
        valid = set()
        for provider in self.providers:
            valid |= provider.available(workspace)
        return names & valid

    async def execute(self, name, ctx):
        for provider in self.providers:
            if any(spec.name == name for spec in provider.specs()):
                return await provider.execute(name, ctx)
        raise KeyError(f"unknown tool {name}")
