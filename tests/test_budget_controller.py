"""Unit tests for TaskBudgetController (Milestone 6)."""

import time
from fusion_agent.config.schema import DeliberationConfig
from fusion_agent.core.budget import TaskBudgetController


def test_budget_call_limits():
    cfg = DeliberationConfig(max_provider_calls=3)
    budget = TaskBudgetController(cfg)

    # Initial state
    allowed, _ = budget.can_call_provider()
    assert allowed is True

    # Call 1
    budget.record_call("agent_a", 100.0, stage="Initial Implementation")
    assert budget.calls_made == 1

    # Call 2
    budget.record_call("agent_b", 50.0, stage="Review Round 1")
    assert budget.calls_made == 2

    # Call 3
    budget.record_call("agent_a", 80.0, stage="Repair Round 1")
    assert budget.calls_made == 3

    # Call 4 should be rejected by call ceiling
    allowed, reason = budget.can_call_provider()
    assert allowed is False
    assert "Maximum provider calls (3) reached" in reason


def test_budget_repair_limits():
    cfg = DeliberationConfig(max_repair_rounds=2, max_provider_calls=10)
    budget = TaskBudgetController(cfg)

    # Round 0 check (before round 1)
    allowed, _ = budget.can_attempt_repair(0)
    assert allowed is True

    # Round 1 check (before round 2)
    allowed, _ = budget.can_attempt_repair(1)
    assert allowed is True

    # Round 2 check (cannot start round 3)
    allowed, reason = budget.can_attempt_repair(2)
    assert allowed is False
    assert "Maximum repair rounds (2) reached" in reason


def test_budget_token_ceilings():
    cfg = DeliberationConfig(
        max_total_input_tokens=1000,
        max_total_output_tokens=500,
    )
    budget = TaskBudgetController(cfg)

    budget.record_call("agent_a", 100.0, input_tokens=600, output_tokens=200)
    allowed, _ = budget.can_call_provider()
    assert allowed is True

    budget.record_call("agent_b", 100.0, input_tokens=500, output_tokens=100)
    # Total input tokens is now 1100, which exceeds 1000
    allowed, reason = budget.can_call_provider()
    assert allowed is False
    assert "Input token budget (1000) exhausted" in reason


def test_budget_summary():
    cfg = DeliberationConfig(max_provider_calls=5, max_repair_rounds=2)
    budget = TaskBudgetController(cfg)

    budget.record_call("agent_a", 150.0, input_tokens=200, output_tokens=50, stage="impl")
    budget.record_repair_round()

    summary = budget.get_summary()
    assert summary["calls_made"] == 1
    assert summary["max_calls"] == 5
    assert summary["repair_rounds_used"] == 1
    assert summary["total_input_tokens"] == 200
    assert summary["total_output_tokens"] == 50
