"""Unit tests for ExecutionIntent classification and edit-intent routing (Milestone 11 Phase C-DEV)."""

import pytest

from fusion_agent.config.schema import OptimizationMode
from fusion_agent.core.router import TaskRouter
from fusion_agent.models.assessment import ExecutionIntent, ReviewRisk, ScopeEstimate
from fusion_agent.models.strategy import StrategyType
from fusion_agent.models.task import Complexity, TaskType
from fusion_agent.providers.mock import MockProvider


def test_edit_intent_fix_single_file():
    router = TaskRouter()
    ass = router.assess_task("Fix the off-by-one boundary condition in calculate_metrics() in src/metrics.py.")
    assert ass.execution_intent == ExecutionIntent.CODE_EDIT
    assert ass.implementation_required is True
    assert ass.task_type in (TaskType.CODE_MODIFICATION, TaskType.BUG_INVESTIGATION)
    assert ass.estimated_scope == ScopeEstimate.SINGLE_FILE


def test_edit_intent_repair_test():
    router = TaskRouter()
    ass = router.assess_task("Repair the failing unit test test_auth_token_refresh in tests/test_auth.py.")
    assert ass.execution_intent == ExecutionIntent.CODE_EDIT
    assert ass.implementation_required is True


def test_edit_intent_implement_feature():
    router = TaskRouter()
    ass = router.assess_task("Implement a rate-limiter middleware in src/middleware/limiter.py using token bucket.")
    assert ass.execution_intent == ExecutionIntent.CODE_EDIT
    assert ass.implementation_required is True


def test_edit_intent_multi_file_feature():
    router = TaskRouter()
    ass = router.assess_task(
        "Add an EventBus in src/events.py and connect it to WorkerPool in src/workers.py across multiple files."
    )
    assert ass.execution_intent == ExecutionIntent.MULTI_STEP_CODE_EDIT
    assert ass.implementation_required is True
    assert ass.estimated_scope == ScopeEstimate.MULTI_FILE


def test_design_analysis_intent():
    router = TaskRouter()
    ass = router.assess_task("Design an RFC and architecture proposal for cross-region data replication.")
    assert ass.execution_intent == ExecutionIntent.DESIGN_ANALYSIS
    assert ass.implementation_required is False
    assert ass.task_type == TaskType.ARCHITECTURE_DESIGN


def test_investigation_intent():
    router = TaskRouter()
    ass = router.assess_task("Investigate why the worker thread pool deadlocks under high load.")
    assert ass.execution_intent == ExecutionIntent.INVESTIGATION
    assert ass.implementation_required is False
    assert ass.task_type == TaskType.BUG_INVESTIGATION


def test_answer_only_intent():
    router = TaskRouter()
    ass = router.assess_task("What is the difference between asyncio.gather and asyncio.wait in Python?")
    assert ass.execution_intent == ExecutionIntent.ANSWER_ONLY
    assert ass.implementation_required is False
    assert ass.task_type == TaskType.SIMPLE_QUERY


def test_route_implementation_invariant():
    """Verify that any implementation-required task ALWAYS routes to an implementation-capable strategy."""
    router = TaskRouter()
    providers = {
        "p1": MockProvider("provider_1"),
        "p2": MockProvider("provider_2"),
    }

    # 1. Single file fix
    decision = router.route(
        task_prompt="Fix boundary calculation in src/math_utils.py so it returns 0 on empty input.",
        available_providers=providers,
        optimization_mode=OptimizationMode.BALANCED,
    )
    assert decision.strategy == StrategyType.AUTONOMOUS_EDIT
    assert decision.task_assessment.implementation_required is True

    # 2. Multi-file feature
    decision_mf = router.route(
        task_prompt="Implement CacheManager in src/cache.py and integrate it into Store in src/store.py.",
        available_providers=providers,
        optimization_mode=OptimizationMode.BALANCED,
    )
    assert decision_mf.strategy == StrategyType.CHECKPOINTED_PLAN
    assert decision_mf.task_assessment.implementation_required is True

    # 3. Best Quality mode does NOT downgrade implementation to prose-only deliberation
    decision_bq = router.route(
        task_prompt="Fix off-by-one error in src/pagination.py.",
        available_providers=providers,
        optimization_mode=OptimizationMode.BEST_QUALITY,
    )
    assert decision_bq.strategy in (StrategyType.AUTONOMOUS_EDIT, StrategyType.CHECKPOINTED_PLAN)
    assert decision_bq.strategy != StrategyType.PROPOSE_CRITIQUE_REFINE


def test_cost_discipline_routing():
    """Verify minimum useful deliberation: simple local edits do not escalate to multi-provider deliberation."""
    router = TaskRouter()
    providers = {
        "p1": MockProvider("provider_1"),
        "p2": MockProvider("provider_2"),
    }

    # Low-complexity localized query
    dec_simple = router.route(
        task_prompt="Explain what RAII means in C++",
        available_providers=providers,
    )
    assert dec_simple.strategy == StrategyType.DIRECT
    assert dec_simple.secondary_provider is None

    # Deep architectural design uses deliberation
    dec_arch = router.route(
        task_prompt="Design a new modular microservice architecture and evaluate tradeoffs",
        available_providers=providers,
    )
    assert dec_arch.strategy == StrategyType.PROPOSE_CRITIQUE_REFINE
    assert dec_arch.secondary_provider is not None
