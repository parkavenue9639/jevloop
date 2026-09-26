"""Run metrics: per-call timings and token accounting for every model and lark-cli call."""

import math
import os
import statistics
import time

JEV_INPUT_PRICE_PER_MTOK = 0.042  # USD; Jev output tokens are free
# DeepSeek list prices per million tokens (env-overridable; check current pricing)
DEEPSEEK_INPUT_PRICE_PER_MTOK = float(os.environ.get("DEEPSEEK_PRICE_IN_PER_MTOK", "0.27"))
DEEPSEEK_CACHE_HIT_PRICE_PER_MTOK = float(
    os.environ.get("DEEPSEEK_PRICE_CACHE_HIT_PER_MTOK", "0.07"))
DEEPSEEK_OUTPUT_PRICE_PER_MTOK = float(os.environ.get("DEEPSEEK_PRICE_OUT_PER_MTOK", "1.10"))


def _p95(values):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))]


class RunMetrics:
    """Collected by the agent loop and the adapter; summarized for the UI after each step."""

    def __init__(self, *, cache_policy="natural_shared", cache_scope_hash=None):
        self.started_at = time.perf_counter()
        self.cache_policy = cache_policy
        self.cache_scope_hash = cache_scope_hash
        self.jev_calls = []  # {operation, latency_ms, input_tokens, output_tokens}
        self.lark_calls = []  # {cmd, ms, dry_run, ok}
        self.helper_calls = []  # token/cost records classified by runtime role
        self.step_profiles = []  # one entry per accepted driver proposal
        self.denials = []
        self.escalations = []  # {from:{action,confidence}, to:{action,target}, agreed}

    def jev(self, decision):
        usage = decision.get("usage", {})
        self.jev_calls.append({
            "operation": decision.get("operation"),
            "latency_ms": decision.get("latency_ms"),
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
        })

    def lark(self, cmd, ms, dry_run=False, ok=True):
        self.lark_calls.append({"cmd": cmd, "ms": round(ms), "dry_run": dry_run, "ok": ok})

    def helper(self, info, *, kind=None):
        usage = (info or {}).get("usage", {})
        input_tokens = int(usage.get("prompt_tokens", 0) or 0)
        output_tokens = int(usage.get("completion_tokens", 0) or 0)
        cache_hit = min(input_tokens, int(usage.get("prompt_cache_hit_tokens",
                        (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)) or 0))
        reported_miss = usage.get("prompt_cache_miss_tokens")
        if reported_miss is None:
            cache_miss = input_tokens - cache_hit
            cache_unknown = 0
            miss_source = "derived"
        else:
            cache_miss = min(
                input_tokens - cache_hit, max(0, int(reported_miss or 0)))
            cache_unknown = input_tokens - cache_hit - cache_miss
            miss_source = "reported"
        prices = [DEEPSEEK_INPUT_PRICE_PER_MTOK, DEEPSEEK_OUTPUT_PRICE_PER_MTOK,
                  DEEPSEEK_CACHE_HIT_PRICE_PER_MTOK]
        if (info or {}).get("visual"):
            try:
                prices = [float(os.environ[name]) for name in (
                    "VISION_PRICE_IN_PER_MTOK", "VISION_PRICE_OUT_PER_MTOK", "VISION_PRICE_CACHE_HIT_PER_MTOK")]
                if any(not math.isfinite(price) or price < 0 for price in prices):
                    prices = None
            except (KeyError, ValueError):
                prices = None
        if (info or {}).get("usage_unknown"):
            prices = None  # Unknown usage is not confirmed-free inference.
        self.helper_calls.append({
            "kind": kind or (info or {}).get("kind") or "unknown",
            "model": (info or {}).get("model"),
            "latency_ms": (info or {}).get("latency_ms"),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_hit_tokens": cache_hit,
            "cache_miss_tokens": cache_miss,
            "cache_unknown_tokens": cache_unknown,
            "cache_miss_source": miss_source,
            "prices": prices,
        })

    def step(self, driver, operation, helper_start):
        calls = self.helper_calls[helper_start:]
        self.step_profiles.append({
            "driver": driver,
            "operation": operation,
            "llm_call_kinds": [call["kind"] for call in calls],
        })

    def denied(self, reason):
        self.denials.append(reason)

    def escalate(self, jev_decision, verdict):
        self.escalations.append({
            "from": {"action": jev_decision.get("operation"),
                     "confidence": jev_decision.get("confidence"),
                     "ambiguity": jev_decision.get("ambiguity"),
                     "progress": (jev_decision.get("progress") or {}).get("score")},
            "to": {"action": verdict.get("action"), "target": verdict.get("target")},
            "agreed": jev_decision.get("operation") == verdict.get("action"),
        })

    @staticmethod
    def _helper_totals(calls):
        input_tokens = sum(call["input_tokens"] for call in calls)
        output_tokens = sum(call["output_tokens"] for call in calls)
        cache_hit = sum(call["cache_hit_tokens"] for call in calls)
        cache_miss = sum(call["cache_miss_tokens"] for call in calls)
        cache_unknown = sum(call["cache_unknown_tokens"] for call in calls)
        cost = 0
        for call in calls:
            prices = call.get("prices", [DEEPSEEK_INPUT_PRICE_PER_MTOK,
                                        DEEPSEEK_OUTPUT_PRICE_PER_MTOK, DEEPSEEK_CACHE_HIT_PRICE_PER_MTOK])
            if prices is not None:
                cost += ((call["cache_miss_tokens"] + call["cache_unknown_tokens"]) * prices[0]
                         + call["output_tokens"] * prices[1] + call["cache_hit_tokens"] * prices[2]) / 1e6
        return {
            "calls": len(calls),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_hit_tokens": cache_hit,
            "cache_miss_tokens": cache_miss,
            "cache_unknown_tokens": cache_unknown,
            "cache_miss_reported_calls": sum(
                call["cache_miss_source"] == "reported" for call in calls),
            "cache_miss_derived_calls": sum(
                call["cache_miss_source"] == "derived" for call in calls),
            "est_cost_usd": round(cost, 6),
            "cost_complete": all(call.get("prices", []) is not None for call in calls),
            "unpriced_calls": sum(call.get("prices", []) is None for call in calls),
        }

    def summary(self):
        jev_ms = [c["latency_ms"] for c in self.jev_calls if c["latency_ms"] is not None]
        lark_ms = [c["ms"] for c in self.lark_calls if not c["dry_run"]]
        helper_ms = [c["latency_ms"] for c in self.helper_calls if c["latency_ms"] is not None]
        in_tok = sum(c["input_tokens"] for c in self.jev_calls)
        out_tok = sum(c["output_tokens"] for c in self.jev_calls)
        helper = self._helper_totals(self.helper_calls)
        kinds = sorted({call["kind"] for call in self.helper_calls})
        by_kind = {
            kind: self._helper_totals(
                [call for call in self.helper_calls if call["kind"] == kind])
            for kind in kinds
        }
        jev_steps = [
            step for step in self.step_profiles if step["driver"] == "jev"
        ]
        direct_steps = [
            step for step in jev_steps if not step["llm_call_kinds"]
        ]
        llm_assisted_steps = len(jev_steps) - len(direct_steps)
        helper_cost = helper["est_cost_usd"]
        cost = in_tok / 1e6 * JEV_INPUT_PRICE_PER_MTOK + helper_cost
        return {
            "elapsed_ms": round((time.perf_counter() - self.started_at) * 1000),
            "jev": {
                "calls": len(self.jev_calls),
                "median_ms": round(statistics.median(jev_ms)) if jev_ms else None,
                "p95_ms": _p95(jev_ms),
                "max_ms": max(jev_ms) if jev_ms else None,
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "est_cost_usd": round(in_tok / 1e6 * JEV_INPUT_PRICE_PER_MTOK, 6),
            },
            "helper": {
                **helper,
                "median_ms": round(statistics.median(helper_ms)) if helper_ms else None,
                "total_ms": sum(helper_ms),
                "by_kind": by_kind,
            },
            "routing": {
                "decision_steps": len(self.step_profiles),
                "jev_steps": len(jev_steps),
                "direct_jev_steps": len(direct_steps),
                "llm_assisted_jev_steps": llm_assisted_steps,
                "llm_avoidance_rate": (
                    len(direct_steps) / len(jev_steps) if jev_steps else None
                ),
                "plain_steps": sum(
                    step["driver"] == "plain" for step in self.step_profiles),
                "visual_steps": sum(step["driver"] == "visual" for step in self.step_profiles),
            },
            "est_cost_usd": round(cost, 6),
            "cost_complete": helper["cost_complete"],
            "pricing": {
                "jev_input_per_mtok": JEV_INPUT_PRICE_PER_MTOK,
                "llm_input_per_mtok": DEEPSEEK_INPUT_PRICE_PER_MTOK,
                "llm_output_per_mtok": DEEPSEEK_OUTPUT_PRICE_PER_MTOK,
                "llm_cache_hit_per_mtok": DEEPSEEK_CACHE_HIT_PRICE_PER_MTOK,
            },
            "cache": {
                "policy": self.cache_policy,
                "scope_hash": self.cache_scope_hash,
            },
            "lark": {
                "calls": len(self.lark_calls),
                "executed_ms": sum(lark_ms),
                "avg_ms": round(sum(lark_ms) / len(lark_ms)) if lark_ms else None,
                "dry_run_calls": sum(1 for c in self.lark_calls if c["dry_run"]),
                "failed": sum(1 for c in self.lark_calls if not c["ok"]),
            },
            "denials": list(self.denials),
            "escalations": {
                "count": len(self.escalations),
                "upheld": sum(1 for e in self.escalations if e["agreed"]),
                "overridden": sum(1 for e in self.escalations if not e["agreed"]),
                "detail": self.escalations,
            },
        }
