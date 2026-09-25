"""Laya decision target and local runtime selection.

The Jev client keeps sending one `/v1/systemone` request. This module only
decides where that request goes, and how a local MLX or CUDA server should
size the context window. Model weights stay in the optional uv extras.
"""

import os

JEV_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_MAX_LEN = 8192
DEFAULT_HEAD_MAX_LEN = 1024
# Recorded JevLoop requests stay under 22 heads and 256 KiB. The local server
# accepts that shape; it does not inherit laya-serve's tighter 64-question cap.
MAX_QUESTIONS = 128
MAX_BODY_BYTES = 512 * 1024

CHECKPOINTS = ("english", "multilingual", "typed-decisions")
_ALIASES = {
    "en": "english",
    "laya": "english",
    "multi": "multilingual",
    "ml": "multilingual",
    "laya-multilingual": "multilingual",
    "typed": "typed-decisions",
    "typed_decisions": "typed-decisions",
    "laya-typed-decisions": "typed-decisions",
}
_LAYA_PROVIDERS = {"laya", "mlx", "cuda"}
_INSTALL = {
    "mlx": "cd backend && uv sync --extra laya-mlx",
    "cuda": "cd backend && uv sync --extra laya-cuda",
}


class LayaRuntimeError(RuntimeError):
    """The requested local runtime is not installed or not usable on this machine."""


def provider_name():
    return os.environ.get("DECISION_PROVIDER", "jev").strip().lower() or "jev"


def using_laya(backend=None):
    """True when this call should use the local Laya server.

    An explicit backend wins over DECISION_PROVIDER, so one dashboard process
    can run either model.
    """
    if backend is None:
        return provider_name() in _LAYA_PROVIDERS
    return str(backend).strip().lower() in _LAYA_PROVIDERS


def normalize_decision_provider(value):
    """Run parameter: jev or laya. Empty follows the process default."""
    if value is None or value == "":
        return "laya" if using_laya() else "jev"
    key = str(value).strip().lower()
    if key not in {"jev", "laya"}:
        raise ValueError("decision_provider must be 'jev' or 'laya'")
    return key


def required_credentials():
    """Env vars a live run must have. Laya does not use the TypeSafe key."""
    keys = ["DEEPSEEK_API_KEY"]
    if not using_laya():
        keys.insert(0, "TYPESAFE_API_KEY")
    return tuple(keys)


def reported_model():
    """Name recorded in bench reports for the decision backend."""
    if using_laya():
        return "laya:" + (os.environ.get("LAYA_MODEL", "").strip() or "router")
    return os.environ.get("TYPESAFE_MODEL", "jev-latest")


def request_model(backend=None):
    """Model id placed in the request body. This does not read any API key."""
    if using_laya(backend):
        return os.environ.get("LAYA_MODEL", "").strip() or None
    return os.environ.get("TYPESAFE_MODEL", "jev-latest")


def client_target(backend=None):
    """URL, bearer token, model id and timeout for one decision call."""
    if using_laya(backend):
        base = os.environ.get("LAYA_BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/")
        return {
            "provider": "laya",
            "url": base + "/v1/systemone",
            "key": os.environ.get("LAYA_API_KEY", ""),
            "model": request_model(backend),
            "timeout": 120.0,
        }
    return {
        "provider": "jev",
        "url": JEV_ENDPOINT,
        "key": os.environ["TYPESAFE_API_KEY"],
        "model": request_model(backend),
        "timeout": 25.0,
    }


def resolve_checkpoint(model):
    """Map a request model id onto a Laya checkpoint, or None to auto-route.

    JevLoop's default `jev-latest` is not a Laya checkpoint. The router then
    picks english or multilingual from the state.
    """
    if model is None:
        return None
    key = str(model).strip().lower()
    if not key:
        return None
    key = _ALIASES.get(key, key)
    return key if key in CHECKPOINTS else None


def parse_models(value):
    raw = value if value is not None else "english,multilingual"
    names = []
    for part in str(raw).split(","):
        part = part.strip()
        if not part:
            continue
        checkpoint = resolve_checkpoint(part)
        if checkpoint is None:
            raise LayaRuntimeError(
                f"Unknown Laya checkpoint {part!r}. Choose from {', '.join(CHECKPOINTS)}."
            )
        if checkpoint not in names:
            names.append(checkpoint)
    if not names:
        raise LayaRuntimeError("LAYA_MODELS did not name a checkpoint.")
    return names


def fit_context_budget(position_limit, max_len, head_max_len):
    """Largest window that still leaves state room inside the encoder limit.

    Both published encoders advertise 8192 positions. The shipped configs use
    a shorter default, which cuts the states already recorded in this runtime.
    """
    target_max = int(max_len)
    if position_limit is not None:
        target_max = min(target_max, int(position_limit))
    head_cap = target_max - 256 if target_max > 260 else target_max - 1
    target_head = min(int(head_max_len), head_cap)
    if not 4 < target_head < target_max:
        return None
    return target_max, target_head


def position_limit(agent):
    config = getattr(agent, "encoder_cfg", None)
    if isinstance(config, dict):
        value = config.get("max_position_embeddings")
    else:
        value = getattr(config, "max_position_embeddings", None)
    return int(value) if value else None


def apply_context_budget(agent, max_len, head_max_len):
    """Raise one loaded agent's context window in place. MLX reads cfg per call."""
    cfg = getattr(agent, "cfg", None)
    if not isinstance(cfg, dict):
        return None
    fitted = fit_context_budget(position_limit(agent), max_len, head_max_len)
    if fitted is None:
        return None
    cfg["max_len"], cfg["head_max_len"] = fitted
    return fitted


def route_text(state):
    """Text the router should read. A JevLoop state is mostly English contract
    text, so routing on the whole document sends a Chinese goal to the English
    checkpoint. The goal is the user-authored part."""
    if isinstance(state, dict):
        goal = state.get("goal")
        if isinstance(goal, str) and goal.strip():
            return goal
    return state


def infer(router, state, questions, *, runtime, model, max_len, head_max_len):
    """One routed decision. CUDA passes the budget per call; MLX stores it on the agent."""
    checkpoint = resolve_checkpoint(model)
    routed = None
    if runtime == "mlx" or hasattr(router, "route"):
        routed = router.route(route_text(state), questions, model=checkpoint)
        checkpoint = routed.get("model") or checkpoint
    if runtime == "mlx":
        agent = router.load(checkpoint)
        apply_context_budget(agent, max_len, head_max_len)
        result = dict(agent.system_one(state, questions))
        result["routing"] = dict(routed)
        return result
    kwargs = {"max_len": max_len, "head_max_len": head_max_len}
    if checkpoint:
        kwargs["model"] = checkpoint
    result = router.predict(state, questions, **kwargs)
    if isinstance(result, dict) and routed is not None:
        result.setdefault("routing", dict(routed))
    return result


def resolve_runtime(requested, *, system, machine, mlx_importable, cuda_available):
    """Pick mlx or cuda. Apple Silicon prefers MLX when that extra is installed."""
    requested = (requested or "auto").strip().lower() or "auto"
    if requested == "auto":
        if system == "darwin" and machine == "arm64" and mlx_importable:
            return "mlx"
        if cuda_available:
            return "cuda"
        if mlx_importable:
            return "mlx"
        raise LayaRuntimeError(
            "No Laya runtime is installed. On Apple Silicon: "
            f"{_INSTALL['mlx']}. On NVIDIA: {_INSTALL['cuda']}."
        )
    if requested == "mlx":
        if not mlx_importable:
            raise LayaRuntimeError(f"MLX runtime is not installed. {_INSTALL['mlx']}.")
        return "mlx"
    if requested == "cuda":
        if not cuda_available:
            raise LayaRuntimeError(
                "CUDA runtime is not available. Install it with "
                f"{_INSTALL['cuda']} on a machine with an NVIDIA driver."
            )
        return "cuda"
    raise LayaRuntimeError(
        f"Unknown LAYA_RUNTIME {requested!r}. Choose auto, mlx, or cuda."
    )


