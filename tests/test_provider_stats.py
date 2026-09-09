"""Unit tests for ProviderStatsTracker and SQLite historical performance (Milestone 6)."""

from fusion_agent.memory.database import Database
from fusion_agent.memory.project_state import ProjectStateManager
from fusion_agent.memory.provider_stats import ProviderStatsTracker
from fusion_agent.models.deliberation import ReviewStatus
from fusion_agent.models.task import Complexity, TaskStatus, TaskType


def test_provider_stats_calculation():
    db = Database(":memory:")
    state = ProjectStateManager(db)
    tracker = ProviderStatsTracker(db)

    # Initially with no runs
    empty_stats = tracker.get_provider_stats("test_provider")
    assert empty_stats.total_tasks_attempted == 0
    assert empty_stats.task_success_rate == 1.0

    # Create project and tasks
    proj = state.get_or_create_project("test-proj", "Test Project", root_path=".")
    t1 = state.create_task(proj["id"], "Task 1", task_type=TaskType.CODE_MODIFICATION)
    t2 = state.create_task(proj["id"], "Task 2", task_type=TaskType.CODE_MODIFICATION)

    # Record runs for provider_a
    state.record_agent_run(
        task_id=t1.id,
        provider_name="provider_a",
        role="proposer",
        response_content="code",
        input_tokens=1000,
        output_tokens=200,
        duration_ms=500.0,
        status="SUCCESS",
    )
    state.update_task_status(t1.id, TaskStatus.COMPLETED, verification_passed=True, repair_rounds=1)

    state.record_agent_run(
        task_id=t2.id,
        provider_name="provider_a",
        role="proposer",
        response_content="code",
        input_tokens=2000,
        output_tokens=400,
        duration_ms=1500.0,
        status="SUCCESS",
    )
    state.update_task_status(t2.id, TaskStatus.COMPLETED, verification_passed=True, repair_rounds=0)

    # Record review conducted by provider_b
    state.record_review(
        task_id=t1.id,
        reviewer_provider="provider_b",
        subject_agent="provider_a",
        status=ReviewStatus.NEEDS_REVISION,
        comments="Missing check",
    )

    stats_a = tracker.get_provider_stats("provider_a")
    assert stats_a.total_tasks_attempted == 2
    assert stats_a.tasks_succeeded == 2
    assert stats_a.task_success_rate == 1.0
    assert stats_a.average_duration_ms == 1000.0
    assert stats_a.average_input_tokens == 1500
    assert stats_a.average_output_tokens == 300

    stats_b = tracker.get_provider_stats("provider_b")
    assert stats_b.total_reviews_conducted == 1
    assert stats_b.actionable_reviews_count == 1
    assert stats_b.review_usefulness_rate == 1.0
