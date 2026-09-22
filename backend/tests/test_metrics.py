"""Model token and cost accounting remains separated by role."""

from jevloop.metrics import RunMetrics


def test_summary_separates_jev_and_llm_tokens_and_costs():
    metrics = RunMetrics()
    metrics.jev({
        "operation": "BASH",
        "latency_ms": 100,
        "usage": {"input_tokens": 1000, "output_tokens": 250},
    })
    metrics.helper({
        "model": "text",
        "latency_ms": 200,
        "usage": {
            "prompt_tokens": 1000,
            "completion_tokens": 100,
            "prompt_cache_hit_tokens": 800,
        },
    })

    summary = metrics.summary()

    assert summary["jev"]["input_tokens"] == 1000
    assert summary["jev"]["output_tokens"] == 250
    assert summary["jev"]["est_cost_usd"] == 0.000042
    assert summary["helper"]["input_tokens"] == 1000
    assert summary["helper"]["output_tokens"] == 100
    assert summary["helper"]["cache_hit_tokens"] == 800
    assert summary["helper"]["cache_miss_tokens"] == 200
    assert summary["helper"]["est_cost_usd"] == 0.00022
    assert summary["est_cost_usd"] == 0.000262
    assert summary["pricing"] == {
        "jev_input_per_mtok": 0.042,
        "llm_input_per_mtok": 0.27,
        "llm_output_per_mtok": 1.10,
        "llm_cache_hit_per_mtok": 0.07,
    }
    assert summary["cache"] == {
        "policy": "natural_shared",
        "scope_hash": None,
    }


def test_summary_reports_call_roles_cache_provenance_and_jev_avoidance():
    metrics = RunMetrics()

    metrics.jev({
        "operation": "LIST_FILES",
        "latency_ms": 10,
        "usage": {"input_tokens": 100, "output_tokens": 10},
    })
    metrics.step("jev", "LIST_FILES", helper_start=0)

    helper_start = len(metrics.helper_calls)
    metrics.jev({
        "operation": "ANSWER",
        "latency_ms": 10,
        "usage": {"input_tokens": 100, "output_tokens": 10},
    })
    metrics.helper({
        "kind": "authoring",
        "model": "text",
        "latency_ms": 20,
        "usage": {
            "prompt_tokens": 1000,
            "completion_tokens": 100,
            "prompt_cache_hit_tokens": 600,
            "prompt_cache_miss_tokens": 300,
        },
    })
    metrics.step("jev", "ANSWER", helper_start=helper_start)

    summary = metrics.summary()

    assert summary["routing"] == {
        "decision_steps": 2,
        "jev_steps": 2,
        "direct_jev_steps": 1,
        "llm_assisted_jev_steps": 1,
        "llm_avoidance_rate": 0.5,
        "plain_steps": 0,
    }
    assert summary["helper"]["cache_hit_tokens"] == 600
    assert summary["helper"]["cache_miss_tokens"] == 300
    assert summary["helper"]["cache_unknown_tokens"] == 100
    assert summary["helper"]["cache_miss_reported_calls"] == 1
    assert summary["helper"]["cache_miss_derived_calls"] == 0
    assert summary["helper"]["by_kind"]["authoring"]["calls"] == 1
    assert summary["helper"]["by_kind"]["authoring"]["est_cost_usd"] == 0.00026


def test_summary_exposes_effective_cache_isolation():
    summary = RunMetrics(
        cache_policy="isolated_session_lane",
        cache_scope_hash="abcdef",
    ).summary()

    assert summary["cache"] == {
        "policy": "isolated_session_lane",
        "scope_hash": "abcdef",
    }
