"""Tests for SQLite database persistence and ProjectStateManager."""

from fusion_agent.memory.database import Database
from fusion_agent.memory.project_state import ProjectStateManager
from fusion_agent.models.deliberation import ReviewStatus
from fusion_agent.models.task import Complexity, TaskStatus, TaskType


def test_database_init_in_memory():
    db = Database(":memory:")
    conn = db.connect()
    assert conn is not None

    # Verify tables created
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
    table_names = [t["name"] for t in tables]
    assert "projects" in table_names
    assert "tasks" in table_names
    assert "decisions" in table_names
    assert "agent_runs" in table_names
    assert "reviews" in table_names
    db.close()


def test_project_crud_and_state():
    db = Database(":memory:")
    manager = ProjectStateManager(db)

    # Create project
    proj = manager.get_or_create_project(
        project_id="test_proj",
        name="Test Process Manager",
        goal="Reliable tracking",
        architecture="Modular",
    )
    assert proj["id"] == "test_proj"
    assert proj["name"] == "Test Process Manager"

    # Update project
    manager.update_project("test_proj", current_milestone="Milestone 2")
    proj_updated = manager.get_or_create_project("test_proj", name="Test Process Manager")
    assert proj_updated["current_milestone"] == "Milestone 2"


def test_task_lifecycle_and_runs():
    db = Database(":memory:")
    manager = ProjectStateManager(db)
    manager.get_or_create_project("test_proj", name="Test")

    # Create Task
    task = manager.create_task(
        project_id="test_proj",
        title="Fix race condition in mutex",
        description="Mutex lock order issue",
        task_type=TaskType.BUG_INVESTIGATION,
        complexity=Complexity.HIGH,
    )
    assert task.id is not None
    assert task.status == TaskStatus.PENDING

    # Update status
    manager.update_task_status(task.id, TaskStatus.IN_PROGRESS, selected_strategy="INDEPENDENT_INVESTIGATION")
    fetched = manager.get_task(task.id)
    assert fetched.status == TaskStatus.IN_PROGRESS
    assert fetched.selected_strategy == "INDEPENDENT_INVESTIGATION"

    # Record agent run
    run_id = manager.record_agent_run(
        task_id=task.id,
        provider_name="MockAgent",
        role="investigator",
        response_content="Hypothesis: deadlock caused by inverse acquisition order.",
        input_tokens=100,
        output_tokens=50,
    )
    assert run_id is not None

    # Record review
    rev_id = manager.record_review(
        task_id=task.id,
        reviewer_provider="CriticAgent",
        subject_agent="MockAgent",
        status=ReviewStatus.APPROVED,
        comments="Hypothesis is verified by call stack.",
    )
    assert rev_id is not None

    # Record decision
    dec_id = manager.record_decision(
        project_id="test_proj",
        task_id=task.id,
        title="Mutex Acquisition Order",
        decision="Acquire Lock A before Lock B everywhere.",
        rationale="Prevents circular wait deadlock.",
    )
    assert dec_id is not None

    # Mark complete
    manager.update_task_status(task.id, TaskStatus.COMPLETED)
    assert manager.get_task(task.id).status == TaskStatus.COMPLETED


def test_context_snapshot_generation():
    db = Database(":memory:")
    manager = ProjectStateManager(db)
    manager.get_or_create_project(
        project_id="proc_mgr",
        name="Process Manager",
        goal="Track app activity",
        architecture="Microkernel",
    )
    manager.record_decision(
        project_id="proc_mgr",
        title="Use PID + CreateTime",
        decision="Avoid PID reuse issues by storing timestamp.",
    )

    task = manager.create_task(
        project_id="proc_mgr",
        title="Implement process watcher",
        task_type=TaskType.CODE_MODIFICATION,
    )

    snapshot = manager.build_context_snapshot("proc_mgr", current_task=task)

    assert "PROJECT: Process Manager" in snapshot.permanent_context
    assert "Use PID + CreateTime" in snapshot.permanent_context
    assert "ACTIVE TASK ID:" in snapshot.current_context
    assert "Implement process watcher" in snapshot.current_context

    prompt_text = snapshot.to_prompt_context()
    assert "### PROJECT OVERVIEW & ARCHITECTURE" in prompt_text
    assert "### CURRENT TASK & RELEVANT STATE" in prompt_text
