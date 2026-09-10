"""Tests for PlanEngine: DAG validation, bounds, fail-closed planning, sanitized expectations, and amendments."""

from pathlib import Path

import pytest

from fusion_agent.core.planner import PlanEngine, PlanValidationError
from fusion_agent.models.assessment import ReviewRisk
from fusion_agent.models.plan import ExecutionPlan, PlanStep, StepStatus
from fusion_agent.models.task import Complexity, Task, TaskStatus, TaskType
from fusion_agent.providers.mock import MockProvider


def test_dag_cycle_detection():
    """Verify that circular step dependencies are detected and rejected."""
    steps = [
        PlanStep(id="step-1", objective="first", dependencies=["step-2"]),
        PlanStep(id="step-2", objective="second", dependencies=["step-1"]),
    ]
    ok, err = PlanEngine.validate_plan_dag(steps)
    assert not ok
    assert "cycle" in err.lower()


def test_max_steps_bounding():
    """Verify that plans exceeding the maximum step bound are rejected."""
    engine = PlanEngine(max_steps=3)
    raw_json = """{
        "title": "Too Many Steps",
        "summary": "Exceeds bound",
        "steps": [
            {"id": "step-1", "objective": "one"},
            {"id": "step-2", "objective": "two"},
            {"id": "step-3", "objective": "three"},
            {"id": "step-4", "objective": "four"}
        ]
    }"""
    data, err = engine.parse_plan_json(raw_json, max_steps=3)
    assert data is None
    assert "exceeding hard limit" in err


def test_plan_failure_fails_closed():
    """Verify that corrupted or unparseable planner output raises PlanValidationError (fails closed)."""
    engine = PlanEngine(max_steps=5)
    bad_provider = MockProvider(
        name="BadPlanner",
        default_response="I cannot make a plan. Just do everything manually!",
    )
    task = Task(
        id="t-fail",
        project_id="p",
        title="Complex multi-step task",
        description="multi-step",
        task_type=TaskType.CODE_MODIFICATION,
        complexity=Complexity.HIGH,
    )

    with pytest.raises(PlanValidationError) as exc_info:
        engine.generate_plan(task=task, context=None, planner=bad_provider)
    assert "Failed to generate valid ExecutionPlan" in str(exc_info.value)


def test_verification_expectations_sanitization(tmp_path: Path):
    """Verify that shell operators, path traversal, and absolute paths are rejected."""
    # 1. Reject shell chaining operators
    ok, err = PlanEngine.validate_verification_expectation("pytest tests/test_foo.py; rm -rf /")
    assert not ok
    assert "forbidden" in err.lower()

    ok, err = PlanEngine.validate_verification_expectation("pytest tests/test_foo.py | grep fail")
    assert not ok

    ok, err = PlanEngine.validate_verification_expectation("pytest tests/test_foo.py && echo bad")
    assert not ok

    # 2. Reject path traversal
    ok, err = PlanEngine.validate_verification_expectation("../../../etc/passwd")
    assert not ok
    assert "traversal" in err.lower()

    # 3. Reject absolute paths
    ok, err = PlanEngine.validate_verification_expectation("/var/run/tests.sh")
    assert not ok
    assert "absolute" in err.lower()

    ok, err = PlanEngine.validate_verification_expectation("C:\\Windows\\cmd.exe")
    assert not ok

    # 4. Allow legitimate relative test paths and filters
    test_file = tmp_path / "tests" / "test_valid.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("def test_ok(): pass", encoding="utf-8")

    ok, err = PlanEngine.validate_verification_expectation("tests/test_valid.py", worktree_path=tmp_path)
    assert ok
    assert err is None

    ok, err = PlanEngine.validate_verification_expectation("tests/test_valid.py::test_ok", worktree_path=tmp_path)
    assert ok

    # 5. Reject non-existent test file when worktree is supplied
    ok, err = PlanEngine.validate_verification_expectation("tests/non_existent.py", worktree_path=tmp_path)
    assert not ok
    assert "does not exist" in err


def test_plan_amendment_cannot_modify_completed_steps():
    """Verify that a plan amendment is forbidden from altering COMPLETED steps."""
    engine = PlanEngine(max_steps=5, max_amendments=1)
    plan = ExecutionPlan(
        plan_id="p-1",
        task_id="t-1",
        title="Plan",
        steps=[
            PlanStep(id="step-1", objective="done step", status=StepStatus.COMPLETED),
            PlanStep(id="step-2", objective="pending step", status=StepStatus.PENDING),
        ],
    )

    with pytest.raises(PlanValidationError) as exc_info:
        engine.apply_plan_amendment(
            plan=plan,
            amendment_dict={"action": "ADD_FILES", "step_id": "step-1", "files": ["new_file.py"]},
        )
    assert "already COMPLETED and cannot be modified" in str(exc_info.value)


def test_plan_amendment_cycle_rejected():
    """Verify that an amendment that creates a dependency cycle is rejected."""
    engine = PlanEngine(max_steps=5, max_amendments=1)
    plan = ExecutionPlan(
        plan_id="p-1",
        task_id="t-1",
        title="Plan",
        steps=[
            PlanStep(id="step-1", objective="first", dependencies=[]),
            PlanStep(id="step-2", objective="second", dependencies=["step-1"]),
        ],
    )

    # Insert step-3 depending on step-2, but step-1 somehow depends on step-3
    plan.get_step("step-1").dependencies = ["step-3"]
    with pytest.raises(PlanValidationError) as exc_info:
        engine.apply_plan_amendment(
            plan=plan,
            amendment_dict={
                "action": "INSERT_STEP",
                "step": {"id": "step-3", "objective": "third", "dependencies": ["step-2"]},
            },
        )
    assert "cycle" in str(exc_info.value).lower()


def test_plan_amendments_max_bound():
    """Verify that exceeding max_plan_amendments is strictly rejected."""
    engine = PlanEngine(max_steps=5, max_amendments=1)
    plan = ExecutionPlan(
        plan_id="p-1",
        task_id="t-1",
        title="Plan",
        amendments_count=1,  # Already used 1 amendment
        steps=[PlanStep(id="step-1", objective="step")],
    )

    with pytest.raises(PlanValidationError) as exc_info:
        engine.apply_plan_amendment(
            plan=plan,
            amendment_dict={"action": "ADD_FILES", "step_id": "step-1", "files": ["file.py"]},
        )
    assert "Maximum plan amendments (1) exceeded" in str(exc_info.value)
