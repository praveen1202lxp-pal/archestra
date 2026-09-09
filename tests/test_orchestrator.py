"""Tests for FusionOrchestrator end-to-end task execution."""

from fusion_agent.config.schema import FusionConfig, OptimizationMode
from fusion_agent.core.orchestrator import FusionOrchestrator
from fusion_agent.memory.database import Database
from fusion_agent.models.task import TaskStatus
from fusion_agent.providers.mock import MockProvider


def test_orchestrator_end_to_end():
    db = Database(":memory:")
    config = FusionConfig.default_mock_config(project_name="OrchestratorTest")
    
    agent_1 = MockProvider(name="agent_1", default_response="Proposed fix for bug.")
    agent_2 = MockProvider(name="agent_2", default_response="Critique and verification.")

    orchestrator = FusionOrchestrator(
        config=config,
        database=db,
        providers={"agent_1": agent_1, "agent_2": agent_2},
    )

    events = []
    result = orchestrator.run_task(
        "Investigate deadlock between thread A and thread B",
        on_status=lambda ev, data: events.append(ev),
    )

    # Verify task completion
    assert result.final_answer is not None
    assert result.task.status == TaskStatus.COMPLETED
    assert result.routing.strategy.value in ("INDEPENDENT_INVESTIGATION", "PROPOSE_CRITIQUE_REFINE")

    # Verify persistence in SQLite
    conn = db.connect()
    task_row = conn.execute("SELECT * FROM tasks WHERE id = ?;", (result.task.id,)).fetchone()
    assert task_row is not None
    assert task_row["status"] == "COMPLETED"

    # Verify agent runs persisted
    runs = conn.execute("SELECT * FROM agent_runs WHERE task_id = ?;", (result.task.id,)).fetchall()
    assert len(runs) >= 1

    # Verify decision recorded
    decisions = conn.execute("SELECT * FROM decisions WHERE task_id = ?;", (result.task.id,)).fetchall()
    assert len(decisions) == 1
    assert "deadlock" in decisions[0]["title"].lower()

    db.close()


def test_orchestrator_context_continuity():
    """Verify that subsequent tasks receive context from earlier tasks."""
    db = Database(":memory:")
    config = FusionConfig.default_mock_config(project_name="ContinuityTest")
    
    agent = MockProvider(name="solo", default_response="Completed step.")
    orchestrator = FusionOrchestrator(config=config, database=db, providers={"solo": agent})

    # Run Task 1
    orchestrator.run_task("Task 1: Add user authentication module")

    # Run Task 2
    orchestrator.run_task("Task 2: Add password reset module")

    # Check that Task 2 received Task 1 in recent context
    context = orchestrator.state_manager.build_context_snapshot("continuitytest")
    assert "Task 1" in context.recent_context
    assert "[COMPLETED]" in context.recent_context

    db.close()


def test_orchestrator_unhealthy_primary_fallback():
    """Verify that orchestrator falls back to a healthy secondary provider."""
    db = Database(":memory:")
    config = FusionConfig.default_mock_config(project_name="FallbackTest")

    failing_primary = MockProvider(name="failing_agent", should_fail_health=True)
    healthy_secondary = MockProvider(name="fallback_agent", default_response="Fallback succeeded.")

    orchestrator = FusionOrchestrator(
        config=config,
        database=db,
        providers={"primary": failing_primary, "secondary": healthy_secondary},
    )

    events = []
    result = orchestrator.run_task(
        "Design caching layer",
        on_status=lambda ev, data: events.append(data.get("message", "")),
    )

    assert result.task.status == TaskStatus.COMPLETED
    assert "Fallback succeeded." in result.final_answer
    assert any("falling back" in ev.lower() for ev in events)
    db.close()


def test_orchestrator_all_providers_unhealthy():
    """Verify clean failure reporting when no healthy provider is available."""
    db = Database(":memory:")
    config = FusionConfig.default_mock_config(project_name="AllFailTest")

    failing_1 = MockProvider(name="failing_1", should_fail_health=True)
    failing_2 = MockProvider(name="failing_2", should_fail_health=True)

    orchestrator = FusionOrchestrator(
        config=config,
        database=db,
        providers={"p1": failing_1, "p2": failing_2},
    )

    result = orchestrator.run_task("Analyze query")
    assert result.task.status == TaskStatus.FAILED
    assert "unavailable" in result.final_answer.lower()
    db.close()
