"""Unit tests for Orchestration Cost Discipline and Deliberation Escalation (Phase C)."""

from fusion_agent.core.router import TaskRouter
from fusion_agent.models.assessment import ExecutionIntent, ReviewRisk, ScopeEstimate
from fusion_agent.models.strategy import StrategyType
from fusion_agent.models.task import Complexity, TaskType
from fusion_agent.providers.mock import MockProvider


def test_localized_obvious_repair_uses_minimum_deliberation():
    """A localized obvious repair should start with single implementation and not force upfront review."""
    router = TaskRouter()
    providers = {
        "claude": MockProvider(name="claude"),
        "codex": MockProvider(name="codex"),
    }

    prompt = "Fix off-by-one error in calculate_total in src/billing.py"
    assessment = router.assess_task(prompt)

    assert assessment.execution_intent == ExecutionIntent.CODE_EDIT
    assert assessment.implementation_required is True
    assert assessment.estimated_scope == ScopeEstimate.SINGLE_FILE
    assert assessment.review_risk == ReviewRisk.LOW
    assert assessment.second_model_benefit is False

    decision = router.route(prompt, available_providers=providers)
    assert decision.strategy == StrategyType.AUTONOMOUS_EDIT
    assert decision.primary_provider is not None
    # Upfront reviewer is omitted to enforce cost discipline on obvious local fix
    assert decision.role_assignments["reviewer"] is None
    assert "Single-agent autonomous edit selected because second-model benefit is LOW" in decision.rationale


def test_broad_architecture_refactor_escalates_deliberation():
    """A broad architecture refactor across multiple files warrants deeper deliberation / multi-step execution."""
    router = TaskRouter()
    providers = {
        "claude": MockProvider(name="claude"),
        "codex": MockProvider(name="codex"),
    }

    prompt = "Refactor database connection pool architecture across src/db.py and src/pool.py"
    assessment = router.assess_task(prompt)

    assert assessment.execution_intent in (ExecutionIntent.CODE_EDIT, ExecutionIntent.MULTI_STEP_CODE_EDIT)
    assert assessment.implementation_required is True
    assert assessment.estimated_scope == ScopeEstimate.MULTI_FILE
    assert assessment.review_risk in (ReviewRisk.MEDIUM, ReviewRisk.HIGH)
    assert assessment.second_model_benefit is True

    decision = router.route(prompt, available_providers=providers)
    assert decision.strategy == StrategyType.CHECKPOINTED_PLAN
    assert decision.primary_provider is not None
    assert decision.secondary_provider is not None


def test_security_sensitive_code_edit_preserves_review():
    """A security-sensitive code edit retains cross-model review even if single-file."""
    router = TaskRouter()
    providers = {
        "claude": MockProvider(name="claude"),
        "codex": MockProvider(name="codex"),
    }

    prompt = "Fix auth token validation and sanitize passwords in src/auth.py"
    assessment = router.assess_task(prompt)

    assert assessment.security_sensitive is True
    assert assessment.review_risk == ReviewRisk.CRITICAL
    assert assessment.second_model_benefit is True

    decision = router.route(prompt, available_providers=providers)
    assert decision.strategy == StrategyType.AUTONOMOUS_EDIT
    assert decision.role_assignments["reviewer"] is not None
    assert decision.secondary_provider is not None
