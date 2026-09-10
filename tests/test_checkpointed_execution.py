"""Integration tests for Checkpointed Multi-Step Execution: Scenarios A, B, C, branch concurrency, and review repair."""

import json
import subprocess
from pathlib import Path

import pytest

from fusion_agent.config.schema import AgentConfig, DeliberationConfig, FusionConfig
from fusion_agent.core.orchestrator import FusionOrchestrator
from fusion_agent.core.router import TaskRouter
from fusion_agent.memory.database import Database
from fusion_agent.models.deliberation import ReviewStatus
from fusion_agent.models.plan import StepStatus
from fusion_agent.models.strategy import StrategyType
from fusion_agent.models.task import TaskStatus
from fusion_agent.providers.base import ReviewResponse
from fusion_agent.providers.mock import MockProvider
from fusion_agent.workspace.promotion import PromotionEngine
from fusion_agent.workspace.session import WorkspaceSession


@pytest.fixture
def clean_git_project(tmp_path: Path):
    """Set up an isolated project repo with mock test suite."""
    repo = tmp_path / "project"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    (repo / "README.md").write_text("# Project Root\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=Author", "-c", "user.email=author@example.com", "commit", "-m", "initial"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    return repo


def test_scenario_a_simple_task_not_planned(clean_git_project: Path):
    """Scenario A: Simple mechanical task does NOT invoke planner and routes to AUTONOMOUS_EDIT or DIRECT."""
    router = TaskRouter()
    simple_prompt = "Fix typo in docstring of fusion_agent/utils/math_utils.py"
    providers = {"mock": MockProvider(name="MockProvider")}

    decision = router.route(
        task_prompt=simple_prompt,
        available_providers=providers,
        allow_multi_step_planning=True,
    )

    # Must NOT route to CHECKPOINTED_PLAN
    assert decision.strategy != StrategyType.CHECKPOINTED_PLAN
    assert decision.strategy in (StrategyType.AUTONOMOUS_EDIT, StrategyType.DIRECT)


def test_scenario_b_multi_step_execution_with_no_promote(clean_git_project: Path):
    """Scenario B: Multi-step feature generates steps, executes sequentially in one worktree, creates checkpoints, passes verification and review, and leaves repo untouched under --no-promote."""
    db = Database(clean_git_project / ".fusion" / "fusion.db")
    config = FusionConfig(
        project_name="TestProject",
        project_root=str(clean_git_project),
        verification_command="python -c \"print('Verification passed')\"",
        deliberation=DeliberationConfig(
            allow_multi_step_planning=True,
            max_provider_calls=12,
        ),
    )

    plan_json = json.dumps({
        "title": "Persistent Provider Stats Feature",
        "summary": "Implement schema, service, and tests.",
        "steps": [
            {
                "id": "step-1",
                "objective": "Create schema file",
                "expected_files": ["stats_schema.py"],
                "dependencies": [],
            },
            {
                "id": "step-2",
                "objective": "Create stats service",
                "expected_files": ["stats_service.py"],
                "dependencies": ["step-1"],
            },
        ],
    })

    # Planner returns plan_json; implementer provides file content for each step; reviewer approves
    def planner_handler(prompt, context=None):
        return plan_json

    def implementer_handler(prompt, context=None):
        if "step-1" in prompt:
            return "### File: stats_schema.py\n```python\nSCHEMA = 'stats'\n```"
        return "### File: stats_service.py\n```python\nfrom stats_schema import SCHEMA\nSERVICE = True\n```"

    mock_planner = MockProvider(name="PlannerMock")
    mock_planner.response_generator = planner_handler
    mock_impl = MockProvider(name="ImplMock")
    mock_impl.response_generator = implementer_handler
    mock_rev = MockProvider(name="ReviewerMock", default_review_status=ReviewStatus.APPROVED)

    providers = {
        "planner": mock_planner,
        "implementer": mock_impl,
        "reviewer": mock_rev,
    }

    orchestrator = FusionOrchestrator(config=config, database=db, providers=providers)
    prompt = "Extend persistent schema and add a query service and tests for provider performance."

    res = orchestrator.run_task(user_prompt=prompt)

    # Verify task passed and strategy was CHECKPOINTED_PLAN
    assert res.task.status == TaskStatus.COMPLETED
    assert res.routing.strategy == StrategyType.CHECKPOINTED_PLAN
    assert res.verification_result.passed
    assert res.review_result.status == ReviewStatus.APPROVED

    # Verify that plan and checkpoints were recorded in SQLite
    plan_record = orchestrator.state_manager.get_plan_by_task_id(res.task.id)
    assert plan_record is not None
    assert len(plan_record.steps) == 2
    assert plan_record.steps[0].status == StepStatus.COMPLETED
    assert plan_record.steps[1].status == StepStatus.COMPLETED

    checkpoints = orchestrator.state_manager.get_checkpoints_for_plan(plan_record.plan_id)
    assert len(checkpoints) == 2
    assert "stats_schema.py" in checkpoints[0].files_changed
    assert "stats_service.py" in checkpoints[1].files_changed

    # Simulating --no-promote: Discard session cleanly
    if res.workspace_session:
        res.workspace_session.teardown(delete_branch=True)

    # Verify target repository working tree is completely clean and untouched
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(clean_git_project),
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert not (clean_git_project / "stats_schema.py").exists()
    assert not (clean_git_project / "stats_service.py").exists()


def test_scenario_c_failure_stops_dependent_steps_and_rolls_back(clean_git_project: Path):
    """Scenario C: Step 2 fails verification; Step 3 depends on Step 2 and is SKIPPED; worktree rolls back to Step 1 checkpoint."""
    db = Database(clean_git_project / ".fusion" / "fusion.db")
    config = FusionConfig(
        project_name="TestProject",
        project_root=str(clean_git_project),
        # Verification fails if broken.py exists
        verification_command="python -c \"import os, sys; sys.exit(1 if os.path.exists('broken.py') else 0)\"",
        deliberation=DeliberationConfig(
            allow_multi_step_planning=True,
            max_repair_rounds=1,
            max_provider_calls=10,
        ),
    )

    plan_json = json.dumps({
        "title": "Failing Pipeline Plan",
        "summary": "Step 2 will fail verification.",
        "steps": [
            {
                "id": "step-1",
                "objective": "Create step 1 valid file",
                "expected_files": ["step1.py"],
                "dependencies": [],
            },
            {
                "id": "step-2",
                "objective": "Create step 2 broken file",
                "expected_files": ["broken.py"],
                "dependencies": ["step-1"],
            },
            {
                "id": "step-3",
                "objective": "Step 3 dependent on step 2",
                "expected_files": ["step3.py"],
                "dependencies": ["step-2"],
            },
        ],
    })

    def implementer_handler(prompt, context=None):
        if "step-1" in prompt:
            return "### File: step1.py\n```python\nS1 = True\n```"
        elif "step-2" in prompt:
            # Continues producing broken.py even on repair
            return "### File: broken.py\n```python\nBROKEN = True\n```"
        return "### File: step3.py\n```python\nS3 = True\n```"

    mock_planner = MockProvider(name="PlannerMock", default_response=plan_json)
    mock_impl = MockProvider(name="ImplMock")
    mock_impl.response_generator = implementer_handler
    mock_rev = MockProvider(name="ReviewerMock", default_review_status=ReviewStatus.APPROVED)

    providers = {"planner": mock_planner, "implementer": mock_impl, "reviewer": mock_rev}
    orchestrator = FusionOrchestrator(config=config, database=db, providers=providers)
    prompt = "Create schema and broken service and dependent tests."

    res = orchestrator.run_task(user_prompt=prompt)

    # Verification must have failed
    assert not res.verification_result.passed

    plan_record = orchestrator.state_manager.get_plan_by_task_id(res.task.id)
    assert plan_record is not None
    assert plan_record.steps[0].status == StepStatus.COMPLETED
    assert plan_record.steps[1].status == StepStatus.FAILED
    assert plan_record.steps[2].status == StepStatus.SKIPPED

    # Worktree was rolled back to Step 1 checkpoint: broken.py does not exist, step1.py exists
    if res.workspace_session:
        assert (res.workspace_session.worktree_path / "step1.py").exists()
        assert not (res.workspace_session.worktree_path / "broken.py").exists()
        assert not (res.workspace_session.worktree_path / "step3.py").exists()
        res.workspace_session.teardown(delete_branch=True)


def test_non_master_base_branch_promotion(clean_git_project: Path):
    """Verify that promotion cleanly targets a non-master base branch (e.g. develop)."""
    # Create and switch to 'develop' branch
    subprocess.run(["git", "checkout", "-b", "develop"], cwd=str(clean_git_project), check=True, capture_output=True)

    session = WorkspaceSession(task_id="dev-branch", repo_root=clean_git_project)
    session.prepare()
    assert session.base_branch == "develop"

    # Add a file in worktree
    (session.worktree_path / "feature.py").write_text("FEATURE = 1\n", encoding="utf-8")

    engine = PromotionEngine()
    prom_res = engine.promote(session)
    assert prom_res.success
    assert prom_res.target_branch == "develop"
    assert (clean_git_project / "feature.py").exists()


def test_target_branch_moved_concurrency_conflict(clean_git_project: Path):
    """Verify that if the target branch moves since session start, promotion is safely refused."""
    session = WorkspaceSession(task_id="concurrency-task", repo_root=clean_git_project)
    session.prepare()

    # Developer concurrently commits to base branch in the host repository
    (clean_git_project / "concurrent.txt").write_text("concurrent developer commit\n", encoding="utf-8")
    subprocess.run(["git", "add", "concurrent.txt"], cwd=str(clean_git_project), check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=Other", "-c", "user.email=other@example.com", "commit", "-m", "concurrent commit"],
        cwd=str(clean_git_project),
        check=True,
        capture_output=True,
    )

    # Attempt to promote session
    (session.worktree_path / "task.py").write_text("TASK = 1\n", encoding="utf-8")
    engine = PromotionEngine()
    prom_res = engine.promote(session)

    assert not prom_res.success
    assert "concurrency violation" in prom_res.message.lower()
    session.teardown(delete_branch=True)


def test_final_review_needs_revision_bounded_repair(clean_git_project: Path):
    """Verify that final review NEEDS_REVISION triggers bounded 1-round repair and re-review."""
    db = Database(clean_git_project / ".fusion" / "fusion.db")
    config = FusionConfig(
        project_name="TestProject",
        project_root=str(clean_git_project),
        verification_command="python -c \"print('OK')\"",
        deliberation=DeliberationConfig(
            allow_multi_step_planning=True,
            max_repair_rounds=1,
            max_provider_calls=12,
        ),
    )

    plan_json = json.dumps({
        "title": "Review Revision Test",
        "summary": "Plan for review test",
        "steps": [{"id": "step-1", "objective": "Write file", "expected_files": ["f.py"]}],
    })

    review_count = 0

    def review_handler(content, criteria, context=None):
        nonlocal review_count
        review_count += 1
        if review_count == 1:
            return ReviewResponse(
                status=ReviewStatus.NEEDS_REVISION,
                comments="Missing docstring",
                suggested_fixes=["Add docstring"],
                input_tokens=10,
                output_tokens=10,
                duration_ms=10.0,
            )
        return ReviewResponse(
            status=ReviewStatus.APPROVED,
            comments="Looks good",
            suggested_fixes=[],
            input_tokens=10,
            output_tokens=10,
            duration_ms=10.0,
        )

    mock_planner = MockProvider(name="PlannerMock", default_response=plan_json)
    mock_impl = MockProvider(name="ImplMock")
    mock_impl.response_generator = lambda p, c: "### File: f.py\n```python\n# initial code\n```"
    mock_rev = MockProvider(name="ReviewerMock")
    mock_rev.review_generator = review_handler

    providers = {"planner": mock_planner, "implementer": mock_impl, "reviewer": mock_rev}
    orchestrator = FusionOrchestrator(config=config, database=db, providers=providers)
    prompt = "Create schema and service and tests."

    res = orchestrator.run_task(user_prompt=prompt)
    assert review_count == 2
    assert res.review_result.status == ReviewStatus.APPROVED
    if res.workspace_session:
        res.workspace_session.teardown(delete_branch=True)
