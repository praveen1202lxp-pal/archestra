"""Acceptance and integration tests for Milestone 9: Durable Task Recovery & Resume.

Covers all required recovery behaviors:
- Scenario A: Interrupted multi-step execution resumes without re-executing completed steps.
- Scenario B: Selective rollback of unverified files without broad git clean -fd.
- Scenario C: Crash after Git checkpoint but before DB completion (Case B reconciliation with trailers).
- Scenario C2: Crash after checkpoint intent but before Git commit cleans up intent safely.
- Scenario C3: Foreign or mismatched checkpoint trailers rejected and branch reset to DB.
- Scenario D: Interrupted STARTED provider call consumes call budget.
- Scenario E: Interrupted repair consumes repair round budget.
- Scenario F: Final-review crash reruns full repository verification before resumed review.
- Scenario G: Relocated repository with same Git history resumes cleanly.
- Scenario H: Promotion commit trailer mismatch forces REQUIRES_MANUAL_RECONCILIATION.
- Scenario I: OS-level atomic file locking mutual exclusion tested with two actual processes.
- Scenario J: Resume is idempotent on completed tasks.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from fusion_agent.config.schema import DeliberationConfig, FusionConfig
from fusion_agent.core.budget import TaskBudgetController
from fusion_agent.core.orchestrator import FusionOrchestrator
from fusion_agent.memory.database import Database
from fusion_agent.models.deliberation import ReviewStatus
from fusion_agent.models.plan import (
    Checkpoint,
    CheckpointTransaction,
    CheckpointTransactionStatus,
    ExecutionPlan,
    PlanStatus,
    PlanStep,
    PromotionTransaction,
    PromotionTransactionStatus,
    StepResult,
    StepStatus,
    VerificationType,
)
from fusion_agent.models.task import Complexity, PromotionDisposition, Task, TaskStatus, TaskType
from fusion_agent.providers.base import ReviewResponse
from fusion_agent.providers.mock import MockProvider
from fusion_agent.workspace.checkpoint import CheckpointManager
from fusion_agent.workspace.lock import TaskExecutionLock, TaskLockError
from fusion_agent.workspace.promotion import PromotionEngine, PromotionResult
from fusion_agent.workspace.recovery import CheckpointRecoveryManager, RecoveryError
from fusion_agent.workspace.session import WorkspaceSession, WorkspaceState


@pytest.fixture
def clean_git_project(tmp_path: Path):
    """Set up an isolated project repo with an initial commit."""
    repo = tmp_path / "project"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Author"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "author@example.com"], cwd=str(repo), check=True, capture_output=True)
    (repo / "README.md").write_text("# Project Root\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial commit"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    return repo


# ---------------------------------------------------------------------------
# Scenario A: Clean Step Resume
# ---------------------------------------------------------------------------

def test_scenario_a_clean_step_resume_skips_completed_steps(clean_git_project: Path):
    """An interrupted task resumes at Step 2 without re-executing Step 1."""
    db = Database(clean_git_project / ".fusion" / "fusion.db")
    config = FusionConfig(
        project_name="ResumeProject",
        project_root=str(clean_git_project),
        verification_command="python -c \"import sys; sys.exit(0)\"",
        deliberation=DeliberationConfig(
            allow_multi_step_planning=True,
            max_provider_calls=12,
        ),
    )

    plan_json = json.dumps({
        "title": "Two-Step Feature",
        "summary": "Step 1 and Step 2 implementation.",
        "steps": [
            {
                "id": "step-1",
                "objective": "Implement Step 1",
                "expected_files": ["step1.py"],
                "dependencies": [],
            },
            {
                "id": "step-2",
                "objective": "Implement Step 2",
                "expected_files": ["step2.py"],
                "dependencies": ["step-1"],
            },
        ],
    })

    step1_calls = [0]
    step2_calls = [0]

    def implementer_handler(prompt, context=None):
        if "step-1" in prompt:
            step1_calls[0] += 1
            return "### File: step1.py\n```python\nVAL_1 = 42\n```"
        elif "step-2" in prompt:
            step2_calls[0] += 1
            return "### File: step2.py\n```python\nfrom step1 import VAL_1\nVAL_2 = VAL_1 * 2\n```"
        return "### File: step1.py\n```python\nVAL_1 = 42\n```"

    mock_planner = MockProvider(name="PlannerMock", default_response=plan_json)
    mock_impl = MockProvider(name="ImplMock")
    mock_impl.response_generator = implementer_handler
    mock_rev = MockProvider(name="ReviewerMock", default_review_status=ReviewStatus.APPROVED)

    providers = {"planner": mock_planner, "implementer": mock_impl, "reviewer": mock_rev}
    orch = FusionOrchestrator(config=config, database=db, providers=providers)

    # 1. Start task and execute Step 1 manually to simulate interruption right after Step 1 checkpoint
    task = orch.state_manager.create_task(
        project_id=orch.project["id"],
        title="Implement two-step feature: step 1 and step 2",
        selected_strategy="checkpointed_plan",
    )
    orch.state_manager.update_task_status(task.id, TaskStatus.IN_PROGRESS)

    recovery_mgr = CheckpointRecoveryManager()
    repo_fingerprint = recovery_mgr.get_repo_fingerprint(clean_git_project)
    cfg_snapshot = json.dumps({"verification_command": config.verification_command or ""})
    orch.state_manager.update_task_recovery_fields(
        task.id,
        repo_fingerprint=repo_fingerprint,
        execution_config_snapshot=cfg_snapshot,
    )

    session = WorkspaceSession(task_id=task.id, repo_root=clean_git_project)
    session.prepare()
    orch.state_manager.update_task_recovery_fields(task.id, base_commit=session.base_commit)

    plan_dict = json.loads(plan_json)
    plan_dict["task_id"] = task.id
    plan = ExecutionPlan.from_dict(plan_dict)
    orch.state_manager.create_plan(plan)

    # Execute Step 1
    (session.worktree_path / "step1.py").write_text("VAL_1 = 42\n", encoding="utf-8")
    chk_mgr = CheckpointManager()
    chk_res = chk_mgr.create_checkpoint(
        session=session,
        plan_id=plan.plan_id,
        step_id="step-1",
        objective_summary="Implement Step 1",
        state_manager=orch.state_manager,
    )
    step1_sha = chk_res.commit_sha
    step1_res = StepResult(
        step_id="step-1",
        status=StepStatus.COMPLETED,
        files_modified=["step1.py"],
        checkpoint_sha=step1_sha,
        verification_passed=True,
        provider="ImplMock",
    )
    orch.state_manager.update_step_status(plan.plan_id, "step-1", StepStatus.COMPLETED, result=step1_res)
    orch.state_manager.update_task_stage(task.id, "step_implementation", last_checkpoint_sha=step1_sha)

    # Simulate interruption
    orch.state_manager.update_task_status(
        task.id,
        TaskStatus.INTERRUPTED,
        interruption_reason="Simulated crash after step 1",
    )
    session.teardown(delete_branch=False)

    # Reset counter to verify Step 1 is NOT invoked on resume
    step1_calls[0] = 0
    step2_calls[0] = 0

    # 2. Resume task
    res = orch.resume_task(task.id)

    assert res.task.status == TaskStatus.COMPLETED
    assert step1_calls[0] == 0, "Step 1 must not be re-executed upon resume"
    assert step2_calls[0] == 1, "Step 2 must execute exactly once"
    assert (session.worktree_path / "step2.py").exists()


# ---------------------------------------------------------------------------
# Scenario B: Selective Rollback
# ---------------------------------------------------------------------------

def test_scenario_b_selective_rollback_preserves_fusion_and_cleans_worktree(clean_git_project: Path):
    """Selective rollback resets tracked files and removes unverified task paths without deleting .fusion."""
    db = Database(clean_git_project / ".fusion" / "fusion.db")
    orch = FusionOrchestrator(
        config=FusionConfig(project_name="Test", project_root=str(clean_git_project)),
        database=db,
    )
    task = orch.state_manager.create_task(project_id=orch.project["id"], title="Selective rollback task")
    task_id = task.id

    session = WorkspaceSession(task_id=task_id, repo_root=clean_git_project)
    session.prepare()

    # Commit initial verified file in worktree
    (session.worktree_path / "verified.py").write_text("INITIAL = 1\n", encoding="utf-8")
    chk_mgr = CheckpointManager()
    chk_res = chk_mgr.create_checkpoint(
        session=session,
        plan_id="plan-1",
        step_id="step-1",
        objective_summary="Initial verified",
    )
    verified_sha = chk_res.commit_sha

    # Now create dirty unverified state:
    # 1. Mutate tracked verified.py
    (session.worktree_path / "verified.py").write_text("CORRUPTED_MUTATION = 999\n", encoding="utf-8")
    # 2. Add unverified new file
    unverified_file = session.worktree_path / "unverified_leak.py"
    unverified_file.write_text("# unverified junk\n", encoding="utf-8")
    # 3. Add an internal .fusion directory file inside worktree
    fusion_meta = session.worktree_path / ".fusion" / "metadata.json"
    fusion_meta.parent.mkdir(parents=True, exist_ok=True)
    fusion_meta.write_text('{"keep": true}', encoding="utf-8")

    # Perform selective rollback
    chk_mgr.rollback_to_checkpoint(
        session=session,
        checkpoint_sha=verified_sha,
        created_files=["unverified_leak.py"],
    )

    # Tracked file was restored
    assert (session.worktree_path / "verified.py").read_text(encoding="utf-8") == "INITIAL = 1\n"
    # Unverified file was removed
    assert not unverified_file.exists()
    # .fusion was preserved
    assert fusion_meta.exists()

    # Verify worktree is at verified_sha
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(session.worktree_path),
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head_sha == verified_sha
    session.teardown(delete_branch=True)


# ---------------------------------------------------------------------------
# Scenario C: Case B Git Checkpoint Ahead of DB Reconciles with Trailers
# ---------------------------------------------------------------------------

def test_scenario_c_reconciles_git_checkpoint_ahead_of_db_case_b(clean_git_project: Path):
    """Crash after Git checkpoint commit but before DB completion reconciles via durable trailers and intent."""
    db = Database(clean_git_project / ".fusion" / "fusion.db")
    orch = FusionOrchestrator(
        config=FusionConfig(project_name="Test", project_root=str(clean_git_project)),
        database=db,
    )
    state_mgr = orch.state_manager

    task = state_mgr.create_task(
        project_id=orch.project["id"],
        title="Multi-step task ahead in Git",
        selected_strategy="checkpointed_plan",
    )
    plan_obj = ExecutionPlan(
        plan_id="plan-ahead",
        task_id=task.id,
        title="Ahead Plan",
        summary="Plan with Git commit ahead",
        steps=[
            PlanStep(id="step-1", objective="Step 1", expected_files=["step1.py"]),
            PlanStep(id="step-2", objective="Step 2", expected_files=["step2.py"], dependencies=["step-1"]),
        ],
    )
    state_mgr.create_plan(plan_obj)

    session = WorkspaceSession(task_id=task.id, repo_root=clean_git_project)
    session.prepare()
    parent_sha = session.base_commit

    # Record verification record in DB
    verif_id = state_mgr.record_verification(
        task_id=task.id,
        verification_type=VerificationType.STEP,
        command="python test",
        exit_code=0,
        passed=True,
        duration_seconds=0.1,
    )

    # Persist durable checkpoint intent (status='PREPARING')
    chk_id = "chk-case-b-1"
    txn = CheckpointTransaction(
        id=chk_id,
        task_id=task.id,
        plan_id=plan_obj.plan_id,
        step_id="step-1",
        expected_parent_sha=parent_sha,
        verification_id=verif_id,
        verified=True,
        approved_paths=["step1.py"],
        status=CheckpointTransactionStatus.PREPARING,
    )
    state_mgr.record_checkpoint_transaction(txn)

    # Create matching Git commit in worktree with valid trailers
    (session.worktree_path / "step1.py").write_text("S1=True\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(session.worktree_path), check=True)
    msg = (
        f"checkpoint(step-1): implement Step 1\n\n"
        f"Fusion-Task-ID: {task.id}\n"
        f"Fusion-Plan-ID: {plan_obj.plan_id}\n"
        f"Fusion-Step-ID: step-1\n"
        f"Fusion-Checkpoint-ID: {chk_id}\n"
    )
    subprocess.run(
        ["git", "commit", "-m", msg],
        cwd=str(session.worktree_path),
        check=True,
    )
    git_head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(session.worktree_path),
        capture_output=True,
        text=True,
    ).stdout.strip()

    # Reconcile checkpoints
    recovery_mgr = CheckpointRecoveryManager()
    res = recovery_mgr.reconcile_checkpoints(session, task, plan_obj, state_mgr)

    assert res.reconciled_from_git is True
    assert res.verified_checkpoint_sha == git_head_sha
    assert res.resumed_step_index == 1

    # Verify SQLite checkpoint transaction was marked COMPLETED
    updated_txn = state_mgr.get_checkpoint_transaction(chk_id)
    assert updated_txn.status == CheckpointTransactionStatus.COMPLETED

    # Verify DB checkpoints table contains the commit
    chk_row = state_mgr.get_checkpoint(chk_id)
    assert chk_row is not None
    assert chk_row.commit_sha == git_head_sha

    # Verify Step 1 in plan was marked COMPLETED
    p_step = state_mgr.get_step(plan_obj.plan_id, "step-1")
    assert p_step.status == StepStatus.COMPLETED
    session.teardown(delete_branch=True)


# ---------------------------------------------------------------------------
# Scenario C2: Crash After Intent but Before Git Commit
# ---------------------------------------------------------------------------

def test_scenario_c2_crash_after_intent_before_git_commit(clean_git_project: Path):
    """Crash after writing intent but before creating Git commit discards uncommitted intent cleanly."""
    db = Database(clean_git_project / ".fusion" / "fusion.db")
    orch = FusionOrchestrator(
        config=FusionConfig(project_name="Test", project_root=str(clean_git_project)),
        database=db,
    )
    state_mgr = orch.state_manager

    task = state_mgr.create_task(
        project_id=orch.project["id"],
        title="Intent crash task",
        selected_strategy="checkpointed_plan",
    )
    plan_obj = ExecutionPlan(
        plan_id="plan-intent-crash",
        task_id=task.id,
        title="Intent Plan",
        summary="Plan",
        steps=[PlanStep(id="step-1", objective="Step 1", expected_files=["s1.py"])],
    )
    state_mgr.create_plan(plan_obj)

    session = WorkspaceSession(task_id=task.id, repo_root=clean_git_project)
    session.prepare()
    parent_sha = session.base_commit

    verif_id = state_mgr.record_verification(
        task_id=task.id,
        verification_type=VerificationType.STEP,
        command="python test",
        exit_code=0,
        passed=True,
    )

    # Intent persisted
    chk_id = "chk-intent-only"
    txn = CheckpointTransaction(
        id=chk_id,
        task_id=task.id,
        plan_id=plan_obj.plan_id,
        step_id="step-1",
        expected_parent_sha=parent_sha,
        verification_id=verif_id,
        verified=True,
        approved_paths=["s1.py"],
        status=CheckpointTransactionStatus.PREPARING,
    )
    state_mgr.record_checkpoint_transaction(txn)
    # But NO git commit was created!

    recovery_mgr = CheckpointRecoveryManager()
    res = recovery_mgr.reconcile_checkpoints(session, task, plan_obj, state_mgr)

    # Should remain at parent_sha and step index 0
    assert res.reconciled_from_git is False
    assert res.verified_checkpoint_sha == parent_sha
    assert res.resumed_step_index == 0

    # Stale intent should be marked FAILED
    updated_txn = state_mgr.get_checkpoint_transaction(chk_id)
    assert updated_txn.status == CheckpointTransactionStatus.FAILED
    session.teardown(delete_branch=True)


# ---------------------------------------------------------------------------
# Scenario C3: Foreign or Mismatched Checkpoint Trailer Rejected
# ---------------------------------------------------------------------------

def test_scenario_c3_foreign_or_mismatched_checkpoint_trailer_rejected(clean_git_project: Path):
    """Foreign/mismatched checkpoint commit ahead of DB is rejected and branch is reset to DB checkpoint."""
    db = Database(clean_git_project / ".fusion" / "fusion.db")
    orch = FusionOrchestrator(
        config=FusionConfig(project_name="Test", project_root=str(clean_git_project)),
        database=db,
    )
    state_mgr = orch.state_manager

    task = state_mgr.create_task(
        project_id=orch.project["id"],
        title="Mismatched trailer task",
        selected_strategy="checkpointed_plan",
    )
    plan_obj = ExecutionPlan(
        plan_id="plan-mismatch",
        task_id=task.id,
        title="Plan",
        summary="Plan",
        steps=[PlanStep(id="step-1", objective="Step 1", expected_files=["s1.py"])],
    )
    state_mgr.create_plan(plan_obj)

    session = WorkspaceSession(task_id=task.id, repo_root=clean_git_project)
    session.prepare()
    parent_sha = session.base_commit

    # Create commit ahead with mismatched task ID in trailers
    (session.worktree_path / "foreign.py").write_text("x=1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(session.worktree_path), check=True)
    msg = (
        f"checkpoint(step-1): foreign commit\n\n"
        f"Fusion-Task-ID: foreign-task-999\n"
        f"Fusion-Plan-ID: {plan_obj.plan_id}\n"
        f"Fusion-Step-ID: step-1\n"
        f"Fusion-Checkpoint-ID: chk-foreign\n"
    )
    subprocess.run(["git", "commit", "-m", msg], cwd=str(session.worktree_path), check=True)

    recovery_mgr = CheckpointRecoveryManager()
    res = recovery_mgr.reconcile_checkpoints(session, task, plan_obj, state_mgr)

    # Must reject foreign commit and stay at parent_sha
    assert res.reconciled_from_git is False
    assert res.verified_checkpoint_sha == parent_sha
    assert res.resumed_step_index == 0

    # Branch in worktree must have been reset back to parent_sha
    current_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(session.worktree_path),
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert current_head == parent_sha
    session.teardown(delete_branch=True)


# ---------------------------------------------------------------------------
# Scenario D: Interrupted STARTED Provider Call Consumes Budget
# ---------------------------------------------------------------------------

def test_scenario_d_started_call_consumes_call_budget(clean_git_project: Path):
    """A provider call reaching STARTED state consumes call budget even after process crash."""
    db = Database(clean_git_project / ".fusion" / "fusion.db")
    orch = FusionOrchestrator(
        config=FusionConfig(project_name="Test", project_root=str(clean_git_project)),
        database=db,
    )
    state_mgr = orch.state_manager

    task = state_mgr.create_task(project_id=orch.project["id"], title="Budget test")

    # Record 2 STARTED calls left behind by a crash
    run_id1, att1 = state_mgr.record_provider_stage_started(
        task_id=task.id,
        stage="step_implementation",
        provider="ProviderA",
        role="implementer",
        step_id="step-1",
        round_number=0,
    )
    assert att1 == 1

    # Reconcile stale runs
    reconciled = state_mgr.reconcile_stale_provider_runs(task.id)
    assert reconciled == 1

    # Verify attempt_number increments on next attempt
    run_id2, att2 = state_mgr.record_provider_stage_started(
        task_id=task.id,
        stage="step_implementation",
        provider="ProviderA",
        role="implementer",
        step_id="step-1",
        round_number=0,
    )
    assert att2 == 2

    # Restore budget from history
    delib_cfg = DeliberationConfig(max_provider_calls=5)
    budget = TaskBudgetController.restore_from_history(task.id, state_mgr, delib_cfg)

    # Both STARTED/INTERRUPTED attempts counted towards budget!
    assert budget.calls_made == 2


# ---------------------------------------------------------------------------
# Scenario E: Interrupted Repair Consumes Repair Budget
# ---------------------------------------------------------------------------

def test_scenario_e_interrupted_repair_consumes_repair_budget(clean_git_project: Path):
    """An interrupted repair attempt consumes repair round budget and is not refreshed on resume."""
    db = Database(clean_git_project / ".fusion" / "fusion.db")
    orch = FusionOrchestrator(
        config=FusionConfig(project_name="Test", project_root=str(clean_git_project)),
        database=db,
    )
    state_mgr = orch.state_manager

    task = state_mgr.create_task(project_id=orch.project["id"], title="Repair budget test")

    # Record a repair stage that was interrupted while RUNNING
    state_mgr.record_provider_stage_started(
        task_id=task.id,
        stage="final_repair",
        provider="RepairProvider",
        role="implementer",
        round_number=1,
    )
    state_mgr.reconcile_stale_provider_runs(task.id)

    # Restore budget with max_repair_rounds=1
    delib_cfg = DeliberationConfig(max_repair_rounds=1)
    budget = TaskBudgetController.restore_from_history(task.id, state_mgr, delib_cfg)

    assert budget.repair_rounds_attempted == 1
    can_repair, reason = budget.can_attempt_repair()
    assert can_repair is False
    assert "reached" in reason.lower()


# ---------------------------------------------------------------------------
# Scenario F: Final-Review Crash Reruns Final Verification
# ---------------------------------------------------------------------------

def test_scenario_f_final_review_crash_reruns_final_verification(clean_git_project: Path):
    """A crash during final review reruns full repository verification upon resume."""
    db = Database(clean_git_project / ".fusion" / "fusion.db")
    config = FusionConfig(
        project_name="RerunVerifProject",
        project_root=str(clean_git_project),
        verification_command="python -c \"import sys; sys.exit(0)\"",
        deliberation=DeliberationConfig(allow_multi_step_planning=True),
    )

    plan_json = json.dumps({
        "title": "One-Step Plan",
        "summary": "Summary",
        "steps": [
            {
                "id": "step-1",
                "objective": "Step 1",
                "expected_files": ["main.py"],
                "dependencies": [],
            }
        ],
    })

    mock_planner = MockProvider(name="PlannerMock", default_response=plan_json)
    mock_impl = MockProvider(name="ImplMock", default_response="### File: main.py\n```python\nAPP=1\n```")
    mock_rev = MockProvider(name="ReviewerMock", default_review_status=ReviewStatus.APPROVED)

    orch = FusionOrchestrator(
        config=config,
        database=db,
        providers={"planner": mock_planner, "implementer": mock_impl, "reviewer": mock_rev},
    )

    task = orch.state_manager.create_task(
        project_id=orch.project["id"],
        title="Task with crash during final review",
        selected_strategy="checkpointed_plan",
    )
    recovery_mgr = CheckpointRecoveryManager()
    repo_fingerprint = recovery_mgr.get_repo_fingerprint(clean_git_project)
    cfg_snapshot = json.dumps({"verification_command": config.verification_command or ""})
    orch.state_manager.update_task_recovery_fields(
        task.id,
        repo_fingerprint=repo_fingerprint,
        execution_config_snapshot=cfg_snapshot,
    )

    plan_dict = json.loads(plan_json)
    plan_dict["task_id"] = task.id
    plan_obj = ExecutionPlan.from_dict(plan_dict)
    orch.state_manager.create_plan(plan_obj)

    session = WorkspaceSession(task_id=task.id, repo_root=clean_git_project)
    session.prepare()
    orch.state_manager.update_task_recovery_fields(task.id, base_commit=session.base_commit)

    (session.worktree_path / "main.py").write_text("APP=1\n", encoding="utf-8")
    chk_mgr = CheckpointManager()
    chk_res = chk_mgr.create_checkpoint(
        session=session,
        plan_id=plan_obj.plan_id,
        step_id="step-1",
        objective_summary="Step 1",
        state_manager=orch.state_manager,
    )
    step1_res = StepResult(
        step_id="step-1",
        status=StepStatus.COMPLETED,
        files_modified=["main.py"],
        checkpoint_sha=chk_res.commit_sha,
        verification_passed=True,
    )
    orch.state_manager.update_step_status(plan_obj.plan_id, "step-1", StepStatus.COMPLETED, result=step1_res)
    orch.state_manager.update_task_stage(task.id, "final_review", last_checkpoint_sha=chk_res.commit_sha)

    # Initial verification before crash
    orch.state_manager.record_verification(
        task_id=task.id,
        verification_type=VerificationType.FINAL,
        command=config.verification_command,
        exit_code=0,
        passed=True,
    )
    initial_verifs = len(orch.state_manager.get_verifications_for_task(task.id))

    # Crash during final review
    orch.state_manager.update_task_status(task.id, TaskStatus.INTERRUPTED, interruption_reason="Crash in review")
    session.teardown(delete_branch=False)

    # Resume task
    res = orch.resume_task(task.id)

    assert res.task.status == TaskStatus.COMPLETED
    # A fresh final verification must have been executed on resume!
    final_verifs = orch.state_manager.get_verifications_for_task(task.id)
    assert len(final_verifs) > initial_verifs
    assert final_verifs[-1]["verification_type"] == VerificationType.FINAL.value


# ---------------------------------------------------------------------------
# Scenario G: Relocated Repository Identity Verification
# ---------------------------------------------------------------------------

def test_scenario_g_relocated_git_repository_can_resume(clean_git_project: Path, tmp_path: Path):
    """Relocated repository with same Git history validates identity and resumes successfully."""
    db_path = clean_git_project / ".fusion" / "fusion.db"
    db = Database(db_path)
    state_mgr = FusionOrchestrator(
        config=FusionConfig(project_name="Test", project_root=str(clean_git_project)),
        database=db,
    ).state_manager

    recovery_mgr = CheckpointRecoveryManager()
    original_fingerprint = recovery_mgr.get_repo_fingerprint(clean_git_project)
    base_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(clean_git_project),
        capture_output=True,
        text=True,
    ).stdout.strip()

    # Move entire project to a new directory
    relocated_path = tmp_path / "relocated_project"
    shutil.copytree(clean_git_project, relocated_path)

    # Validate identity on relocated repository
    assert recovery_mgr.validate_repository_identity(
        repo_root=relocated_path,
        expected_fingerprint=original_fingerprint,
        expected_base_commit=base_commit,
    ) is True

    # Validate that an alien repository is rejected
    alien_repo = tmp_path / "alien_project"
    alien_repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(alien_repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=Alien", "-c", "user.email=alien@example.com", "commit", "--allow-empty", "-m", "alien"],
        cwd=str(alien_repo),
        check=True,
        capture_output=True,
    )
    assert recovery_mgr.validate_repository_identity(
        repo_root=alien_repo,
        expected_fingerprint=original_fingerprint,
        expected_base_commit=base_commit,
    ) is False


# ---------------------------------------------------------------------------
# Scenario H: Promotion Trailer Mismatch Requires Manual Reconciliation
# ---------------------------------------------------------------------------

def test_scenario_h_promotion_trailer_mismatch_forces_manual_reconciliation(clean_git_project: Path):
    """Promotion commit metadata mismatch forces REQUIRES_MANUAL_RECONCILIATION."""
    db = Database(clean_git_project / ".fusion" / "fusion.db")
    orch = FusionOrchestrator(
        config=FusionConfig(project_name="Test", project_root=str(clean_git_project)),
        database=db,
    )
    state_mgr = orch.state_manager

    task_id = "promo-mismatch-task"
    task = state_mgr.create_task(project_id=orch.project["id"], task_id=task_id, title="Promotion mismatch task")
    promo_id = "promo-123"

    txn = PromotionTransaction(
        id=promo_id,
        task_id=task_id,
        target_branch="master",
        expected_target_sha="initial_sha",
        task_branch=f"fusion/task-{task_id}",
        task_head_sha="valid_task_head",
        diff_hash="expected_diff_hash_123",
        status=PromotionTransactionStatus.PREPARING,
    )
    state_mgr.record_promotion_transaction(txn)

    # Commit on master with mismatched diff hash
    corrupted_msg = (
        f"feat: squashed feature\n\n"
        f"Fusion-Task-ID: {task_id}\n"
        f"Fusion-Promotion-ID: {promo_id}\n"
        f"Fusion-Task-Head: valid_task_head\n"
        f"Fusion-Diff-Hash: WRONG_TAMPERED_DIFF_HASH\n"
    )
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", corrupted_msg],
        cwd=str(clean_git_project),
        check=True,
    )

    session = WorkspaceSession(task_id=task_id, repo_root=clean_git_project)
    engine = PromotionEngine()
    promo_res = engine.reconcile_interrupted_promotion(session, state_manager=state_mgr)

    assert promo_res.requires_manual_reconciliation is True
    assert promo_res.success is False

    updated_txn = state_mgr.get_promotion_transaction(promo_id)
    assert updated_txn.status == PromotionTransactionStatus.REQUIRES_MANUAL_RECONCILIATION


# ---------------------------------------------------------------------------
# Scenario I: OS Atomic Lock Tested with Two Real Processes
# ---------------------------------------------------------------------------

def test_scenario_i_task_execution_lock_mutual_exclusion_two_processes(tmp_path: Path):
    """Atomic task locking enforces mutual exclusion across two separate OS processes."""
    locks_dir = tmp_path / ".fusion" / "locks"
    locks_dir.mkdir(parents=True, exist_ok=True)
    task_id = "process-lock-test"

    lock1 = TaskExecutionLock(task_id=task_id, locks_dir=locks_dir)
    assert lock1.acquire() is True

    # Process 2 attempts to acquire lock via separate Python OS process
    code = f"""
import sys
from pathlib import Path
from fusion_agent.workspace.lock import TaskExecutionLock

lock = TaskExecutionLock(task_id="{task_id}", locks_dir=Path(r"{locks_dir}"))
acquired = lock.acquire()
if acquired:
    sys.exit(0)
else:
    sys.exit(1)
"""
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(Path(__file__).parents[1]),
        capture_output=True,
        text=True,
    )

    # Process 2 must FAIL to acquire the lock because Process 1 holds it
    assert proc.returncode == 1, f"Process 2 unexpectedly acquired lock! Output: {proc.stdout} {proc.stderr}"

    # Release Process 1 lock
    lock1.release()

    # Now Process 3 runs and must succeed in acquiring the released lock
    proc_after = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(Path(__file__).parents[1]),
        capture_output=True,
        text=True,
    )
    assert proc_after.returncode == 0, f"Process 3 failed to acquire released lock! Output: {proc_after.stdout} {proc_after.stderr}"


# ---------------------------------------------------------------------------
# Scenario J: Resume Idempotency
# ---------------------------------------------------------------------------

def test_scenario_j_resume_is_idempotent_on_completed_task(clean_git_project: Path):
    """Resuming an already COMPLETED task returns cleanly without re-executing anything."""
    db = Database(clean_git_project / ".fusion" / "fusion.db")
    orch = FusionOrchestrator(
        config=FusionConfig(project_name="Test", project_root=str(clean_git_project)),
        database=db,
    )

    task = orch.state_manager.create_task(project_id=orch.project["id"], title="Already finished task")
    orch.state_manager.update_task_status(task.id, TaskStatus.COMPLETED)

    res = orch.resume_task(task.id)
    assert res.task.status == TaskStatus.COMPLETED
    assert "already COMPLETED" in res.final_answer


# ---------------------------------------------------------------------------
# Scenario K: Missing Worktree Reconstruction
# ---------------------------------------------------------------------------

def test_scenario_k_missing_worktree_reconstruction(clean_git_project: Path):
    """Fusion reconstructs worktree from verified task branch without copying files from developer's main worktree."""
    db = Database(clean_git_project / ".fusion" / "fusion.db")
    config = FusionConfig(
        project_name="ReconstructProject",
        project_root=str(clean_git_project),
        verification_command="python -c \"import sys; sys.exit(0)\"",
        deliberation=DeliberationConfig(allow_multi_step_planning=True, max_provider_calls=10),
    )

    plan_json = json.dumps({
        "title": "Reconstruct Plan",
        "summary": "Two steps",
        "steps": [
            {"id": "step-1", "objective": "Step 1", "expected_files": ["step1.py"], "dependencies": []},
            {"id": "step-2", "objective": "Step 2", "expected_files": ["step2.py"], "dependencies": ["step-1"]},
        ],
    })

    step2_executed = [False]

    def implementer_handler(prompt, context=None):
        if "step-2" in prompt:
            step2_executed[0] = True
            return "### File: step2.py\n```python\nS2 = 200\n```"
        return "### File: step1.py\n```python\nS1 = 100\n```"

    mock_planner = MockProvider(name="PlannerMock", default_response=plan_json)
    mock_impl = MockProvider(name="ImplMock")
    mock_impl.response_generator = implementer_handler
    mock_rev = MockProvider(name="ReviewerMock", default_review_status=ReviewStatus.APPROVED)
    providers = {"planner": mock_planner, "implementer": mock_impl, "reviewer": mock_rev}
    orch = FusionOrchestrator(config=config, database=db, providers=providers)

    task = orch.state_manager.create_task(
        project_id=orch.project["id"],
        title="Reconstruction task",
        selected_strategy="checkpointed_plan",
    )
    orch.state_manager.update_task_status(task.id, TaskStatus.IN_PROGRESS)

    recovery_mgr = CheckpointRecoveryManager()
    repo_fingerprint = recovery_mgr.get_repo_fingerprint(clean_git_project)
    base_commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(clean_git_project), capture_output=True, text=True).stdout.strip()
    orch.state_manager.update_task_status(
        task.id,
        TaskStatus.IN_PROGRESS,
        repo_fingerprint=repo_fingerprint,
        execution_config_snapshot=json.dumps({"verification_command": config.verification_command}),
    )
    task.base_commit = base_commit
    task.repo_fingerprint = repo_fingerprint

    # Create plan and step 1 checkpoint
    plan_obj = ExecutionPlan.from_dict(json.loads(plan_json), task_id=task.id)
    orch.state_manager.create_plan(plan_obj)

    session = WorkspaceSession(task_id=task.id, repo_root=clean_git_project)
    session.prepare()

    (session.worktree_path / "step1.py").write_text("S1 = 100\n", encoding="utf-8")
    chk_mgr = CheckpointManager()
    chk_res = chk_mgr.create_checkpoint(
        session=session,
        plan_id=plan_obj.plan_id,
        step_id="step-1",
        expected_files=["step1.py"],
        state_manager=orch.state_manager,
    )
    orch.state_manager.update_step_status(plan_obj.plan_id, "step-1", StepStatus.COMPLETED)
    orch.state_manager.update_task_stage(task.id, "step_completed:step-1", last_checkpoint_sha=chk_res.checkpoint_sha)
    task.last_checkpoint_sha = chk_res.checkpoint_sha

    # Intentionally DELETE the task worktree directory and prune worktree tracking
    shutil.rmtree(session.worktree_dir, ignore_errors=True)
    subprocess.run(["git", "worktree", "prune"], cwd=str(clean_git_project), check=True)
    assert not session.worktree_dir.exists()

    # In developer's main worktree, add a commit with dev_main_only.py
    (clean_git_project / "dev_main_only.py").write_text("DEV_ONLY = True\n", encoding="utf-8")
    subprocess.run(["git", "add", "dev_main_only.py"], cwd=str(clean_git_project), check=True)
    subprocess.run(["git", "commit", "-m", "developer local commit"], cwd=str(clean_git_project), check=True)

    # Run recovery / resume
    result = orch.resume_task(task.id)

    # Assertions:
    # 1. Worktree was reconstructed
    assert session.worktree_dir.exists()
    # 2. Reconstructed worktree does NOT contain dev_main_only.py
    assert not (session.worktree_dir / "dev_main_only.py").exists()
    # 3. Reconstructed worktree contains step1.py and step2.py
    assert (session.worktree_dir / "step1.py").exists()
    assert (session.worktree_dir / "step2.py").exists()
    # 4. Step 2 was executed, Step 1 was not re-executed
    assert step2_executed[0] is True
    assert result.task.status == TaskStatus.COMPLETED


# ---------------------------------------------------------------------------
# Scenario L: Target Branch Divergence Blocks Promotion
# ---------------------------------------------------------------------------

def test_scenario_l_target_branch_divergence_blocks_promotion(clean_git_project: Path):
    """When target branch moves independently (SHA A -> SHA B), resume continues but promotion is BLOCKED."""
    db = Database(clean_git_project / ".fusion" / "fusion.db")
    config = FusionConfig(
        project_name="DivergeProject",
        project_root=str(clean_git_project),
        verification_command="python -c \"import sys; sys.exit(0)\"",
        deliberation=DeliberationConfig(allow_multi_step_planning=True, max_provider_calls=10),
    )

    plan_json = json.dumps({
        "title": "Diverge Plan",
        "summary": "Two steps",
        "steps": [
            {"id": "step-1", "objective": "Step 1", "expected_files": ["step1.py"], "dependencies": []},
            {"id": "step-2", "objective": "Step 2", "expected_files": ["step2.py"], "dependencies": ["step-1"]},
        ],
    })

    mock_planner = MockProvider(name="PlannerMock", default_response=plan_json)
    mock_impl = MockProvider(name="ImplMock", default_response="### File: step2.py\n```python\nS2 = 1\n```")
    mock_rev = MockProvider(name="ReviewerMock", default_review_status=ReviewStatus.APPROVED)
    providers = {"planner": mock_planner, "implementer": mock_impl, "reviewer": mock_rev}
    orch = FusionOrchestrator(config=config, database=db, providers=providers)

    task = orch.state_manager.create_task(
        project_id=orch.project["id"],
        title="Diverge task",
        selected_strategy="checkpointed_plan",
    )
    orch.state_manager.update_task_status(task.id, TaskStatus.IN_PROGRESS)

    recovery_mgr = CheckpointRecoveryManager()
    repo_fingerprint = recovery_mgr.get_repo_fingerprint(clean_git_project)
    base_commit_sha_a = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(clean_git_project), capture_output=True, text=True).stdout.strip()
    orch.state_manager.update_task_status(
        task.id,
        TaskStatus.IN_PROGRESS,
        repo_fingerprint=repo_fingerprint,
        base_commit=base_commit_sha_a,
        execution_config_snapshot=json.dumps({"verification_command": config.verification_command}),
    )
    task.base_commit = base_commit_sha_a
    task.repo_fingerprint = repo_fingerprint

    plan_obj = ExecutionPlan.from_dict(json.loads(plan_json), task_id=task.id)
    orch.state_manager.create_plan(plan_obj)

    session = WorkspaceSession(task_id=task.id, repo_root=clean_git_project)
    session.prepare()

    (session.worktree_path / "step1.py").write_text("S1 = 1\n", encoding="utf-8")
    chk_mgr = CheckpointManager()
    chk_res = chk_mgr.create_checkpoint(
        session=session,
        plan_id=plan_obj.plan_id,
        step_id="step-1",
        expected_files=["step1.py"],
        state_manager=orch.state_manager,
    )
    orch.state_manager.record_checkpoint(chk_res)
    orch.state_manager.update_step_status(plan_obj.plan_id, "step-1", StepStatus.COMPLETED)
    orch.state_manager.update_task_stage(task.id, "step_completed:step-1", last_checkpoint_sha=chk_res.checkpoint_sha)
    orch.state_manager.update_task_status(task.id, TaskStatus.INTERRUPTED, base_commit=base_commit_sha_a)
    task.last_checkpoint_sha = chk_res.checkpoint_sha

    # Advance target branch independently to SHA B
    (clean_git_project / "other_developer.txt").write_text("concurrent edit\n", encoding="utf-8")
    subprocess.run(["git", "add", "other_developer.txt"], cwd=str(clean_git_project), check=True)
    subprocess.run(["git", "commit", "-m", "advance target branch to SHA B"], cwd=str(clean_git_project), check=True)
    target_sha_b = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(clean_git_project), capture_output=True, text=True).stdout.strip()
    assert target_sha_b != base_commit_sha_a

    # Resume task - execution continues on isolated task branch
    result = orch.resume_task(task.id)
    assert result.task.status == TaskStatus.COMPLETED
    assert (result.workspace_session.worktree_path / "step2.py").exists()

    # Now attempt promotion of this task session
    promo = PromotionEngine()
    promo_res = promo.promote(result.workspace_session, target_branch="master", state_manager=orch.state_manager)

    # Concurrency violation MUST block promotion
    assert promo_res.success is False
    assert "concurrency violation" in promo_res.message.lower()

    # Verify target branch was NOT rebased, merged, or overwritten
    current_target_head = subprocess.run(["git", "rev-parse", "master"], cwd=str(clean_git_project), capture_output=True, text=True).stdout.strip()
    assert current_target_head == target_sha_b
    assert not (clean_git_project / "step1.py").exists()
    assert not (clean_git_project / "step2.py").exists()
    assert (clean_git_project / "other_developer.txt").exists()


# ---------------------------------------------------------------------------
# Scenario M: Stale Provider Invocation Retry & Single Accepted Constraint
# ---------------------------------------------------------------------------

def test_scenario_m_stale_provider_invocation_retry_and_accepted_constraint(clean_git_project: Path):
    """Stale provider invocation marked INTERRUPTED, retry succeeds, exactly 1 accepted result enforced by SQLite."""
    import sqlite3
    db = Database(clean_git_project / ".fusion" / "fusion.db")
    orch = FusionOrchestrator(
        config=FusionConfig(project_name="Test", project_root=str(clean_git_project)),
        database=db,
    )
    state_mgr = orch.state_manager

    task = state_mgr.create_task(project_id=orch.project["id"], title="Invocation retry task")
    logical_id = f"{task.id}_plan-1_step-1_implementation_0"

    # Attempt 1: STARTED before crash
    run_id_1, att_1 = state_mgr.record_provider_stage_started(
        task_id=task.id,
        stage="step_implementation",
        provider="TestProvider",
        role="implementer",
        plan_id="plan-1",
        step_id="step-1",
        logical_invocation_id=logical_id,
    )
    assert att_1 == 1

    # Simulate crash and resume reconciliation
    stale_count = state_mgr.reconcile_stale_provider_runs(task.id)
    assert stale_count == 1

    # Stale attempt 1 is now INTERRUPTED and is_accepted = 0
    conn = db.connect()
    row_1 = conn.execute("SELECT status, is_accepted, attempt_number FROM agent_runs WHERE id = ?;", (run_id_1,)).fetchone()
    assert row_1["status"] == "INTERRUPTED"
    assert row_1["is_accepted"] == 0
    assert row_1["attempt_number"] == 1

    # Provider budget remains consumed
    budget = TaskBudgetController.restore_from_history(task.id, state_mgr)
    assert budget.calls_made == 1

    # Attempt 2 created on resume
    run_id_2, att_2 = state_mgr.record_provider_stage_started(
        task_id=task.id,
        stage="step_implementation",
        provider="TestProvider",
        role="implementer",
        plan_id="plan-1",
        step_id="step-1",
        logical_invocation_id=logical_id,
    )
    assert att_2 == 2

    # Attempt 2 completes and is marked accepted
    state_mgr.complete_provider_stage(
        run_id=run_id_2,
        response_content="def hello(): pass",
        is_accepted=True,
    )

    # Exactly 1 row has is_accepted = 1 for this logical_invocation_id
    rows = conn.execute(
        "SELECT id, attempt_number, status, is_accepted FROM agent_runs WHERE logical_invocation_id = ? ORDER BY attempt_number ASC;",
        (logical_id,),
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["is_accepted"] == 0
    assert rows[1]["is_accepted"] == 1

    # Persistence-level constraint test: trying to set is_accepted = 1 on attempt 1 MUST fail with IntegrityError
    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute("UPDATE agent_runs SET is_accepted = 1 WHERE id = ?;", (run_id_1,))


# ---------------------------------------------------------------------------
# Scenario N: Promotion Crash — Successful Reconciliation Path
# ---------------------------------------------------------------------------

def test_scenario_n_promotion_crash_successful_reconciliation(clean_git_project: Path):
    """Crash after squash promotion lands on target branch with valid trailers reconciles to APPLIED without duplicate commit."""
    db = Database(clean_git_project / ".fusion" / "fusion.db")
    orch = FusionOrchestrator(
        config=FusionConfig(project_name="Test", project_root=str(clean_git_project)),
        database=db,
    )
    state_mgr = orch.state_manager

    task = state_mgr.create_task(project_id=orch.project["id"], title="Promotion crash task")
    session = WorkspaceSession(task_id=task.id, repo_root=clean_git_project)
    session.prepare()

    (session.worktree_path / "feature.py").write_text("FEATURE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(session.worktree_path), check=True)
    subprocess.run(["git", "commit", "-m", "feature commit"], cwd=str(session.worktree_path), check=True)
    task_head_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(session.worktree_path), capture_output=True, text=True).stdout.strip()
    diff_hash = "abc123diffhash"

    # Persist promotion transaction in PREPARING
    promo_id = "promo-crash-1"
    txn = PromotionTransaction(
        id=promo_id,
        task_id=task.id,
        target_branch="master",
        expected_target_sha=session.base_commit,
        task_branch=session.task_branch,
        task_head_sha=task_head_sha,
        diff_hash=diff_hash,
        status=PromotionTransactionStatus.PREPARING,
    )
    state_mgr.record_promotion_transaction(txn)

    # Simulate squash commit successfully created on target branch with exact matching trailers
    (clean_git_project / "feature.py").write_text("FEATURE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "feature.py"], cwd=str(clean_git_project), check=True)
    msg = (
        f"feat(fusion): promote task {task.id}\n\n"
        f"Fusion-Task-ID: {task.id}\n"
        f"Fusion-Promotion-ID: {promo_id}\n"
        f"Fusion-Task-Head: {task_head_sha}\n"
        f"Fusion-Diff-Hash: {diff_hash}\n"
    )
    subprocess.run(["git", "commit", "-m", msg], cwd=str(clean_git_project), check=True)
    target_promoted_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(clean_git_project), capture_output=True, text=True).stdout.strip()

    # Reconcile interrupted promotion
    promo_engine = PromotionEngine()
    promo_res = promo_engine.reconcile_interrupted_promotion(session, state_manager=state_mgr)

    assert promo_res.success is True
    assert promo_res.commit_hash == target_promoted_sha
    assert "reconciled promotion commit" in promo_res.message.lower()

    # Verify transaction in SQLite transitioned to APPLIED
    updated_txn = state_mgr.get_promotion_transaction(promo_id)
    assert updated_txn.status == PromotionTransactionStatus.APPLIED
    assert updated_txn.resulting_target_sha == target_promoted_sha

    # Verify target branch HEAD is still target_promoted_sha (no duplicate commit created)
    head_after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(clean_git_project), capture_output=True, text=True).stdout.strip()
    assert head_after == target_promoted_sha


# ---------------------------------------------------------------------------
# Scenario O: Repeated Interrupted Resumes
# ---------------------------------------------------------------------------

def test_scenario_o_repeated_interrupted_resumes(clean_git_project: Path):
    """Crash -> resume 1 -> crash -> resume 2 executes incrementally with cumulative budgets and no duplicate checkpoints."""
    db = Database(clean_git_project / ".fusion" / "fusion.db")
    config = FusionConfig(
        project_name="RepeatResumeProject",
        project_root=str(clean_git_project),
        verification_command="python -c \"import sys; sys.exit(0)\"",
        deliberation=DeliberationConfig(allow_multi_step_planning=True, max_provider_calls=12),
    )

    plan_json = json.dumps({
        "title": "Three Step Feature",
        "summary": "Step 1, 2, 3",
        "steps": [
            {"id": "step-1", "objective": "Step 1", "expected_files": ["step1.py"], "dependencies": []},
            {"id": "step-2", "objective": "Step 2", "expected_files": ["step2.py"], "dependencies": ["step-1"]},
            {"id": "step-3", "objective": "Step 3", "expected_files": ["step3.py"], "dependencies": ["step-2"]},
        ],
    })

    execution_counts = {"step-1": 0, "step-2": 0, "step-3": 0}

    def impl_handler(prompt, context=None):
        if "step-1" in prompt:
            execution_counts["step-1"] += 1
            return "### File: step1.py\n```python\nS1 = 1\n```"
        elif "step-2" in prompt:
            execution_counts["step-2"] += 1
            return "### File: step2.py\n```python\nS2 = 2\n```"
        elif "step-3" in prompt:
            execution_counts["step-3"] += 1
            return "### File: step3.py\n```python\nS3 = 3\n```"
        return "### File: step1.py\n```python\nS1 = 1\n```"

    mock_planner = MockProvider(name="PlannerMock", default_response=plan_json)
    mock_impl = MockProvider(name="ImplMock")
    mock_impl.response_generator = impl_handler
    mock_rev = MockProvider(name="ReviewerMock", default_review_status=ReviewStatus.APPROVED)
    providers = {"planner": mock_planner, "implementer": mock_impl, "reviewer": mock_rev}
    orch = FusionOrchestrator(config=config, database=db, providers=providers)

    task = orch.state_manager.create_task(
        project_id=orch.project["id"],
        title="Three step task",
        selected_strategy="checkpointed_plan",
    )
    orch.state_manager.update_task_status(task.id, TaskStatus.IN_PROGRESS)

    recovery_mgr = CheckpointRecoveryManager()
    repo_fingerprint = recovery_mgr.get_repo_fingerprint(clean_git_project)
    base_commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(clean_git_project), capture_output=True, text=True).stdout.strip()
    orch.state_manager.update_task_status(
        task.id,
        TaskStatus.IN_PROGRESS,
        repo_fingerprint=repo_fingerprint,
        execution_config_snapshot=json.dumps({"verification_command": config.verification_command}),
    )
    task.base_commit = base_commit
    task.repo_fingerprint = repo_fingerprint

    plan_obj = ExecutionPlan.from_dict(json.loads(plan_json), task_id=task.id)
    orch.state_manager.create_plan(plan_obj)

    session = WorkspaceSession(task_id=task.id, repo_root=clean_git_project)
    session.prepare()

    # Run 1: Step 1 completes and checkpoints, then crashes before Step 2
    (session.worktree_path / "step1.py").write_text("S1 = 1\n", encoding="utf-8")
    chk_mgr = CheckpointManager()
    chk_1 = chk_mgr.create_checkpoint(session, plan_obj.plan_id, "step-1", ["step1.py"], state_manager=orch.state_manager)
    orch.state_manager.record_checkpoint(chk_1)
    orch.state_manager.update_step_status(plan_obj.plan_id, "step-1", StepStatus.COMPLETED)
    orch.state_manager.record_provider_stage(task.id, "step_implementation", "ImplMock", "implementer", "S1", step_id="step-1")
    task.last_checkpoint_sha = chk_1.checkpoint_sha
    assert task.recovery_attempts == 0

    # Resume 1: Completes Step 2 and checkpoints, then crashes before Step 3
    (session.worktree_path / "step2.py").write_text("S2 = 2\n", encoding="utf-8")
    chk_2 = chk_mgr.create_checkpoint(session, plan_obj.plan_id, "step-2", ["step2.py"], state_manager=orch.state_manager)
    orch.state_manager.record_checkpoint(chk_2)
    orch.state_manager.update_step_status(plan_obj.plan_id, "step-2", StepStatus.COMPLETED)
    orch.state_manager.record_provider_stage(task.id, "step_implementation", "ImplMock", "implementer", "S2", step_id="step-2")
    orch.state_manager.update_task_status(task.id, TaskStatus.INTERRUPTED, recovery_attempts=1)
    task.recovery_attempts = 1
    task.last_checkpoint_sha = chk_2.checkpoint_sha

    # Resume 2: Resumes task through orchestrator
    result = orch.resume_task(task.id)

    assert result.task.status == TaskStatus.COMPLETED
    assert result.task.recovery_attempts == 2

    # Verify Step 1 and Step 2 were not re-executed by implementer_handler
    assert execution_counts["step-1"] == 0
    assert execution_counts["step-2"] == 0
    assert execution_counts["step-3"] == 1

    # Checkpoints: exactly 3 checkpoints in DB, NO duplicates
    chks = orch.state_manager.get_checkpoints_for_task(task.id)
    assert len(chks) == 3
    assert {c["step_id"] for c in chks} == {"step-1", "step-2", "step-3"}

    # Provider budget is cumulative (2 prior calls + 2 resume calls: impl and review)
    budget = TaskBudgetController.restore_from_history(task.id, orch.state_manager)
    assert budget.calls_made == 4


# ---------------------------------------------------------------------------
# Scenario P: Config and Provider Drift
# ---------------------------------------------------------------------------

def test_scenario_p_config_and_provider_drift(clean_git_project: Path):
    """SAFE drift permits resume; BLOCKING drift (verification command, safety policy, unavailable provider, bad repo) fails closed."""
    recovery_mgr = CheckpointRecoveryManager()

    # 1. SAFE drift: statistics and latency changes
    snapshot = json.dumps({
        "verification_command": "pytest",
        "average_latency_ms": 120.0,
        "total_calls": 5,
    })
    current_cfg = {
        "verification_command": "pytest",
        "average_latency_ms": 450.0,
        "total_calls": 15,
    }
    safe_ok, safe_err = recovery_mgr.validate_config_drift(current_cfg, snapshot)
    assert safe_ok is True
    assert safe_err is None

    # 2. BLOCKING drift: verification command materially changed
    bad_cfg = {"verification_command": "echo bypass"}
    b_ok, b_err = recovery_mgr.validate_config_drift(bad_cfg, snapshot)
    assert b_ok is False
    assert "verification_command changed" in b_err

    # 3. BLOCKING drift: safety policy materially weakened
    sec_snapshot = json.dumps({
        "verification_command": "pytest",
        "safety_checks_enabled": True,
    })
    weak_cfg = {
        "verification_command": "pytest",
        "safety_checks_enabled": False,
    }
    w_ok, w_err = recovery_mgr.validate_config_drift(weak_cfg, sec_snapshot)
    assert w_ok is False
    assert "safety policy materially weakened" in w_err

    # 4. BLOCKING drift: required provider unavailable or unhealthy
    db = Database(clean_git_project / ".fusion" / "fusion.db")
    config = FusionConfig(project_name="Drift", project_root=str(clean_git_project))
    orch_no_providers = FusionOrchestrator(config=config, database=db, providers={})

    task = orch_no_providers.state_manager.create_task(
        project_id=orch_no_providers.project["id"],
        title="Drift task",
        selected_strategy="checkpointed_plan",
    )
    orch_no_providers.state_manager.update_task_status(
        task.id,
        TaskStatus.INTERRUPTED,
        repo_fingerprint=recovery_mgr.get_repo_fingerprint(clean_git_project),
        base_commit=subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(clean_git_project), capture_output=True, text=True).stdout.strip(),
    )
    session_p = WorkspaceSession(task_id=task.id, repo_root=clean_git_project)
    session_p.prepare()
    plan_obj = ExecutionPlan(plan_id="p1", task_id=task.id, title="P", summary="P", steps=[PlanStep(id="s1", objective="O", expected_files=["f.py"])])
    orch_no_providers.state_manager.create_plan(plan_obj)

    # 4a. When no eligible providers are available
    with pytest.raises(ValueError) as exc_no_p:
        orch_no_providers.resume_task(task.id)
    assert "no eligible providers" in str(exc_no_p.value).lower()

    # 4b. When required provider is unhealthy
    unhealthy_lead = MockProvider(name="MockLead", should_fail_health=True)
    orch_unhealthy = FusionOrchestrator(
        config=config,
        database=db,
        providers={"lead": unhealthy_lead},
    )
    with pytest.raises(RuntimeError) as exc_unhealthy:
        orch_unhealthy.resume_task(task.id)
    assert "unavailable or unhealthy" in str(exc_unhealthy.value)

    # 5. BLOCKING drift: repository identity cannot be proven
    wrong_repo = clean_git_project.parent / "wrong_repo"
    wrong_repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(wrong_repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "A"], cwd=str(wrong_repo), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "a@a.com"], cwd=str(wrong_repo), check=True, capture_output=True)
    (wrong_repo / "INIT").write_text("Diff", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(wrong_repo), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init wrong"], cwd=str(wrong_repo), check=True, capture_output=True)

    orch_wrong = FusionOrchestrator(
        config=FusionConfig(project_name="Drift", project_root=str(wrong_repo)),
        database=Database(wrong_repo / ".fusion" / "fusion.db"),
    )
    task_wrong = orch_wrong.state_manager.create_task(
        project_id=orch_wrong.project["id"],
        title="Wrong repo task",
    )
    orch_wrong.state_manager.update_task_status(
        task_wrong.id,
        TaskStatus.INTERRUPTED,
        repo_fingerprint="non-existent-fingerprint",
        base_commit="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
    )

    with pytest.raises(RecoveryError) as exc_repo:
        orch_wrong.resume_task(task_wrong.id)
    assert "Repository identity validation failed" in str(exc_repo.value)


# ---------------------------------------------------------------------------
# Scenario Q: Interruption Preservation Policy
# ---------------------------------------------------------------------------

def test_scenario_q_interruption_preservation_policy(clean_git_project: Path):
    """Interrupted task branch and checkpoints are preserved for recovery; normal discard/cleanup removes them."""
    db = Database(clean_git_project / ".fusion" / "fusion.db")
    orch = FusionOrchestrator(
        config=FusionConfig(project_name="Test", project_root=str(clean_git_project)),
        database=db,
    )
    state_mgr = orch.state_manager

    # 1. Unexpected interruption: task has checkpoint and crashes abruptly
    task = state_mgr.create_task(project_id=orch.project["id"], title="Preserved task")
    session = WorkspaceSession(task_id=task.id, repo_root=clean_git_project)
    session.prepare()
    (session.worktree_path / "work.py").write_text("W = 1\n", encoding="utf-8")
    chk_mgr = CheckpointManager()
    plan_obj = ExecutionPlan(plan_id="p1", task_id=task.id, title="P", summary="P", steps=[PlanStep(id="s1", objective="O", expected_files=["work.py"])])
    state_mgr.create_plan(plan_obj)
    chk = chk_mgr.create_checkpoint(session, "p1", "s1", ["work.py"], state_manager=state_mgr)
    state_mgr.update_task_status(task.id, TaskStatus.INTERRUPTED)

    # Process died abruptly (NO teardown called)
    # Check that task branch and checkpoint commit remain available in Git
    branch_check = subprocess.run(["git", "branch", "--list", session.task_branch], cwd=str(clean_git_project), capture_output=True, text=True).stdout.strip()
    assert session.task_branch in branch_check
    cat_check = subprocess.run(["git", "cat-file", "-t", chk.checkpoint_sha], cwd=str(clean_git_project), capture_output=True, text=True).stdout.strip()
    assert cat_check == "commit"

    # 2. Normal completed with --no-promote: discard cleanly removes branch and worktree
    promo = PromotionEngine()
    promo.discard(session)
    branch_after_discard = subprocess.run(["git", "branch", "--list", session.task_branch], cwd=str(clean_git_project), capture_output=True, text=True).stdout.strip()
    assert not branch_after_discard
    assert not session.worktree_dir.exists()

    # 3. Ordinary non-recoverable cleanup path (e.g. task failed with 0 checkpoints)
    task_empty = state_mgr.create_task(project_id=orch.project["id"], title="Empty failed task")
    session_empty = WorkspaceSession(task_id=task_empty.id, repo_root=clean_git_project)
    session_empty.prepare()
    assert session_empty.worktree_dir.exists()
    # Non-recoverable failure triggers teardown(delete_branch=True)
    session_empty.teardown(delete_branch=True)
    assert not session_empty.worktree_dir.exists()
    branch_empty = subprocess.run(["git", "branch", "--list", session_empty.task_branch], cwd=str(clean_git_project), capture_output=True, text=True).stdout.strip()
    assert not branch_empty
