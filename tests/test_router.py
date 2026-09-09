"""Tests for deterministic TaskRouter and strategy selection."""

from fusion_agent.config.schema import OptimizationMode
from fusion_agent.core.router import TaskRouter
from fusion_agent.models.strategy import StrategyType
from fusion_agent.models.task import Complexity, TaskType
from fusion_agent.providers.mock import MockProvider


def test_classify_task():
    router = TaskRouter()

    # Bug investigation
    t_type, comp = router.classify_task("Find why this C++ application deadlocks intermittently")
    assert t_type == TaskType.BUG_INVESTIGATION
    assert comp == Complexity.HIGH

    # Architecture design
    t_type, comp = router.classify_task("Design a new modular plugin architecture for Fusion Agent")
    assert t_type == TaskType.ARCHITECTURE_DESIGN
    assert comp == Complexity.HIGH

    # Code modification
    t_type, comp = router.classify_task("Implement a thread-safe LRU cache in Python")
    assert t_type == TaskType.CODE_MODIFICATION
    assert comp == Complexity.MEDIUM

    # Simple query
    t_type, comp = router.classify_task("Explain what RAII means in C++")
    assert t_type == TaskType.SIMPLE_QUERY
    assert comp == Complexity.LOW


def test_routing_with_two_providers():
    router = TaskRouter()
    providers = {
        "agent_a": MockProvider(name="agent_a"),
        "agent_b": MockProvider(name="agent_b"),
    }

    # Bug -> INDEPENDENT_INVESTIGATION
    decision = router.route("Investigate crash in thread pool", providers, OptimizationMode.BALANCED)
    assert decision.strategy == StrategyType.INDEPENDENT_INVESTIGATION
    assert decision.primary_provider == "agent_a"
    assert decision.secondary_provider == "agent_b"

    # Architecture -> PROPOSE_CRITIQUE_REFINE
    decision = router.route("Design database schema for analytics", providers, OptimizationMode.BALANCED)
    assert decision.strategy == StrategyType.PROPOSE_CRITIQUE_REFINE

    # Simple query -> DIRECT
    decision = router.route("What is a semaphore?", providers, OptimizationMode.BALANCED)
    assert decision.strategy == StrategyType.DIRECT


def test_routing_optimization_modes():
    router = TaskRouter()
    providers = {
        "agent_a": MockProvider(name="agent_a"),
        "agent_b": MockProvider(name="agent_b"),
    }

    # FASTEST mode simplifies multi-round to EXECUTE_AND_REVIEW or DIRECT
    decision_fast = router.route("Investigate crash in worker", providers, OptimizationMode.FASTEST)
    assert decision_fast.strategy == StrategyType.EXECUTE_AND_REVIEW

    # LOWEST_COST mode downgrades to DIRECT when possible
    decision_cost = router.route("Implement helper function", providers, OptimizationMode.LOWEST_COST)
    assert decision_cost.strategy == StrategyType.DIRECT


def test_routing_with_single_provider():
    router = TaskRouter()
    providers = {"agent_a": MockProvider(name="agent_a")}

    # Always DIRECT if only 1 provider is available
    decision = router.route("Design enterprise distributed system", providers, OptimizationMode.BEST_QUALITY)
    assert decision.strategy == StrategyType.DIRECT
    assert decision.secondary_provider is None
