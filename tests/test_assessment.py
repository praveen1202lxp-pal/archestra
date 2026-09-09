"""Unit tests for deterministic TaskAssessment (Milestone 6)."""

from fusion_agent.core.router import TaskRouter
from fusion_agent.models.assessment import ReviewRisk, ScopeEstimate
from fusion_agent.models.task import Complexity, TaskType


def test_bug_investigation_assessment():
    router = TaskRouter()
    ass = router.assess_task("Investigate race condition in task scheduler thread pool")

    assert ass.task_type == TaskType.BUG_INVESTIGATION
    assert ass.debugging_required is True
    assert ass.complexity in (Complexity.HIGH, Complexity.CRITICAL)
    assert ass.second_model_benefit is True


def test_architecture_design_assessment():
    router = TaskRouter()
    ass = router.assess_task("Design a new modular plugin architecture for Fusion Agent")

    assert ass.task_type == TaskType.ARCHITECTURE_DESIGN
    assert ass.architecture_reasoning_required is True
    assert ass.estimated_scope == ScopeEstimate.REPO_WIDE
    assert ass.second_model_benefit is True


def test_code_modification_assessment():
    router = TaskRouter()
    ass = router.assess_task("Implement a thread-safe LRU cache in Python")

    assert ass.task_type == TaskType.CODE_MODIFICATION
    assert ass.implementation_required is True
    assert ass.estimated_scope == ScopeEstimate.SINGLE_FILE
    assert ass.review_risk == ReviewRisk.MEDIUM


def test_security_sensitive_assessment():
    router = TaskRouter()
    ass = router.assess_task("Implement token authentication and sanitize passwords in API endpoints")

    assert ass.security_sensitive is True
    assert ass.complexity == Complexity.CRITICAL
    assert ass.review_risk == ReviewRisk.CRITICAL
    assert ass.second_model_benefit is True


def test_simple_query_assessment():
    router = TaskRouter()
    ass = router.assess_task("Explain what RAII means in C++")

    assert ass.task_type == TaskType.SIMPLE_QUERY
    assert ass.complexity == Complexity.LOW
    assert ass.second_model_benefit is False
    assert ass.implementation_required is False


def test_multi_file_scope_assessment():
    router = TaskRouter()
    ass = router.assess_task("Refactor error handling across multiple files in the repository")

    assert ass.estimated_scope == ScopeEstimate.MULTI_FILE
    assert ass.expected_files_count >= 3
