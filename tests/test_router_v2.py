"""Unit tests for Dynamic Provider Routing and Role Reversal (Milestone 6)."""

import pytest

from fusion_agent.config.schema import OptimizationMode
from fusion_agent.core.router import TaskRouter
from fusion_agent.models.strategy import StrategyType
from fusion_agent.models.task import Complexity, TaskType
from fusion_agent.providers.capabilities import (
    CapabilityStrength,
    CostTier,
    ProviderCapabilities,
)
from fusion_agent.providers.mock import MockProvider


def test_dynamic_role_reversal():
    """Verify that provider roles dynamically reverse based on capabilities and task type."""
    router = TaskRouter()

    # Agent Codex: Expert coding strength, high review strength
    codex_caps = ProviderCapabilities(
        reasoning_strength=CapabilityStrength.HIGH,
        coding_strength=CapabilityStrength.EXPERT,
        review_strength=CapabilityStrength.HIGH,
        structured_output=True,
        cost_tier=CostTier.SUBSCRIPTION,
        preferred_task_types=["CODE_MODIFICATION", "CODE_REVIEW"],
    )
    codex_agent = MockProvider(name="Codex CLI Agent", capabilities=codex_caps)

    # Agent Antigravity: High reasoning strength, high coding strength, high review strength
    antigravity_caps = ProviderCapabilities(
        reasoning_strength=CapabilityStrength.HIGH,
        coding_strength=CapabilityStrength.HIGH,
        review_strength=CapabilityStrength.HIGH,
        structured_output=True,
        cost_tier=CostTier.SUBSCRIPTION,
        preferred_task_types=["ARCHITECTURE_DESIGN", "CODE_REVIEW", "BUG_INVESTIGATION"],
    )
    antigravity_agent = MockProvider(name="Antigravity CLI Agent", capabilities=antigravity_caps)

    providers = {
        "antigravity_cli": antigravity_agent,
        "codex_cli": codex_agent,
    }

    # Scenario 1: Complex code modification task
    # Codex has EXPERT coding strength (1.0) vs Antigravity HIGH (0.75)
    # Router must assign Codex as implementer and Antigravity as reviewer!
    decision_code = router.route(
        "Implement a thread-safe high-performance concurrent queue in Python",
        providers,
        OptimizationMode.BALANCED,
    )
    assert decision_code.strategy == StrategyType.AUTONOMOUS_EDIT
    assert decision_code.role_assignments["implementer"] == "codex_cli"
    assert decision_code.role_assignments["reviewer"] == "antigravity_cli"
    assert decision_code.primary_provider == "codex_cli"
    assert decision_code.secondary_provider == "antigravity_cli"

    # Scenario 2: High-level architectural design task
    # Antigravity has preferred ARCHITECTURE_DESIGN and equal/high reasoning
    # Router assigns Antigravity as lead/proposer and Codex as peer critic
    decision_arch = router.route(
        "Design distributed multi-region consensus architecture",
        providers,
        OptimizationMode.BALANCED,
    )
    assert decision_arch.strategy == StrategyType.PROPOSE_CRITIQUE_REFINE
    assert decision_arch.role_assignments["lead"] == "antigravity_cli"
    assert decision_arch.role_assignments["reviewer"] == "codex_cli"


def test_optimization_mode_fastest_single_agent():
    """In FASTEST mode, autonomous edit should skip second-model review for speed."""
    router = TaskRouter()
    providers = {
        "agent_a": MockProvider(name="agent_a"),
        "agent_b": MockProvider(name="agent_b"),
    }

    decision = router.route("Implement quick utility function", providers, OptimizationMode.FASTEST)
    assert decision.strategy == StrategyType.DIRECT or decision.secondary_provider is None


def test_optimization_mode_local_private():
    """LOCAL_PRIVATE mode must exclude non-local providers and raise if none exist."""
    router = TaskRouter()
    cloud_caps = ProviderCapabilities(local=False)
    local_caps = ProviderCapabilities(local=True)

    cloud_provider = MockProvider(name="Cloud Agent", capabilities=cloud_caps)
    local_provider = MockProvider(name="Local Agent", capabilities=local_caps)

    providers = {
        "cloud": cloud_provider,
        "local": local_provider,
    }

    decision = router.route("Process private data", providers, OptimizationMode.LOCAL_PRIVATE)
    assert decision.primary_provider == "local"
    assert decision.secondary_provider is None

    # Error when no local provider exists
    with pytest.raises(ValueError, match="No local providers available"):
        router.route("Private task", {"cloud_only": cloud_provider}, OptimizationMode.LOCAL_PRIVATE)


def test_scoring_breakdown_transparency():
    """Verify that RoutingDecision contains detailed scoring breakdowns."""
    router = TaskRouter()
    providers = {
        "agent_a": MockProvider(name="agent_a"),
        "agent_b": MockProvider(name="agent_b"),
    }

    decision = router.route("Implement parser", providers, OptimizationMode.BALANCED)
    assert "agent_a" in decision.scoring_breakdown
    assert "implementer_score" in decision.scoring_breakdown["agent_a"]
    assert "reviewer_score" in decision.scoring_breakdown["agent_a"]
