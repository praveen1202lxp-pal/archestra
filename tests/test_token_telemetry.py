"""Tests for refined telemetry terminology, provider overhead calculation, and token accounting."""

from fusion_agent.config.schema import DeliberationConfig
from fusion_agent.core.budget import StageUsageRecord, TaskBudgetController


def test_telemetry_provider_managed_input_overhead():
    """Verify that difference between native input tokens and Fusion context tokens is labeled provider_managed_input_overhead."""
    record = StageUsageRecord(
        stage="Initial Implementation",
        provider_name="Codex CLI",
        duration_ms=150.0,
        input_tokens=18_500,
        output_tokens=1_200,
        fusion_context_tokens=3_500,
        reasoning_tokens=0,
        visible_output_tokens=1_200,
        cached_tokens=12_000,
        raw_usage={"prompt_tokens": 18500, "completion_tokens": 1200},
    )

    # native_input_tokens (18500) - fusion_context_tokens (3500) = 15000 overhead
    assert record.provider_managed_input_overhead == 15_000
    assert record.raw_usage == {"prompt_tokens": 18500, "completion_tokens": 1200}
    assert record.cached_tokens == 12_000


def test_budget_controller_telemetry_aggregation():
    """Verify TaskBudgetController correctly aggregates native, reasoning, cached, and context tokens across calls."""
    config = DeliberationConfig()
    controller = TaskBudgetController(config)

    controller.record_call(
        provider_name="Codex CLI",
        duration_ms=1500.0,
        input_tokens=20_000,
        output_tokens=2_000,
        fusion_context_tokens=4_000,
        reasoning_tokens=500,
        visible_output_tokens=1_500,
        cached_tokens=10_000,
        raw_usage={"turn": 1},
        stage="Initial Implementation",
    )

    controller.record_call(
        provider_name="Antigravity CLI",
        duration_ms=2500.0,
        input_tokens=8_000,
        output_tokens=3_000,
        fusion_context_tokens=1_500,
        reasoning_tokens=2_500,
        visible_output_tokens=500,
        cached_tokens=4_000,
        raw_usage={"turn": 2},
        stage="Review Round 1",
    )

    summary = controller.get_usage_summary()

    assert summary["total_input_tokens"] == 28_000
    assert summary["total_output_tokens"] == 5_000
    assert summary["total_fusion_context_tokens"] == 5_500
    assert summary["total_provider_managed_overhead"] == (28_000 - 5_500)
    assert summary["total_reasoning_tokens"] == 3_000
    assert summary["total_visible_output_tokens"] == 2_000
    assert summary["total_cached_tokens"] == 14_000
