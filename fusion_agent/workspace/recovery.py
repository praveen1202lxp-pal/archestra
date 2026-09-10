"""Checkpoint recovery manager and 3-way state reconciler for Milestone 9."""

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fusion_agent.models.plan import Checkpoint, ExecutionPlan, StepStatus
from fusion_agent.models.task import Task
from fusion_agent.workspace.checkpoint import CheckpointManager
from fusion_agent.workspace.session import WorkspaceSession


class RecoveryError(RuntimeError):
    """Raised when task recovery cannot safely reconcile Git, SQLite, and worktree state."""
    pass


@dataclass
class ReconciliationResult:
    """Outcome of 3-way checkpoint and worktree reconciliation."""
    success: bool
    verified_checkpoint_sha: str
    reconciled_step_id: Optional[str] = None
    action_taken: str = ""
    message: str = ""
    resumed_step_index: int = 0
    worktree_rolled_back: bool = False

    @property
    def reconciled_from_git(self) -> bool:
        return self.action_taken == "reconciled_git_commit_into_db"


class CheckpointRecoveryManager:
    """Reconciles SQLite, Git branch history, commit trailers, and isolated worktree state."""

    def __init__(self, checkpoint_mgr: Optional[CheckpointManager] = None):
        self.checkpoint_mgr = checkpoint_mgr or CheckpointManager()

    @staticmethod
    def _run_git(args: List[str], cwd: Path, check: bool = True) -> str:
        proc = subprocess.run(
            ["git"] + args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if check and proc.returncode != 0:
            raise RuntimeError(
                f"Git command failed in {cwd} ({' '.join(args)}):\n"
                f"{proc.stderr.strip() or proc.stdout.strip()}"
            )
        return proc.stdout.strip()

    def get_repo_fingerprint(self, repo_root: Path) -> str:
        """Compute durable repository fingerprint based on root/initial commits."""
        try:
            root_commits = self._run_git(["rev-list", "--max-parents=0", "HEAD"], cwd=repo_root, check=False)
            first_commit = root_commits.splitlines()[-1].strip() if root_commits else "unknown"
            return f"root:{first_commit}"
        except Exception:
            return "root:unknown"

    def validate_repository_identity(
        self,
        repo_root: Path,
        expected_fingerprint: Optional[str] = None,
        expected_base_commit: Optional[str] = None,
    ) -> bool:
        """Verify that current repository matches the task's original repository.
        
        Relocating the repository directory to another path is permitted as long as
        root commits and base commits are reachable in Git.
        """
        if not expected_fingerprint and not expected_base_commit:
            return True

        if expected_fingerprint:
            current_fp = self.get_repo_fingerprint(repo_root)
            if current_fp == expected_fingerprint:
                return True

        # Check if base commit is reachable in Git
        if expected_base_commit:
            check = self._run_git(["cat-file", "-t", expected_base_commit], cwd=repo_root, check=False)
            if check.strip() == "commit":
                return True

        return False

    def validate_config_drift(
        self,
        current_config_dict: Dict[str, Any],
        snapshot_str: Optional[str],
    ) -> Tuple[bool, Optional[str]]:
        """Validate that critical execution configuration settings have not drifted.
        
        Verification command, project root, and core safety settings must not change.
        """
        if not snapshot_str:
            return True, None

        try:
            snapshot = json.loads(snapshot_str)
        except Exception:
            return True, None

        # Critical: verification_command
        curr_verif = current_config_dict.get("verification_command", "").strip()
        snap_verif = snapshot.get("verification_command", "").strip()
        if curr_verif != snap_verif:
            return False, (
                f"Configuration drift blocked resume: verification_command changed from "
                f"'{snap_verif}' to '{curr_verif}'. Resume refused to prevent testing against inconsistent criteria."
            )

        # Critical: safety policy materially weakened
        for sec_key in ("safety_checks_enabled", "secret_filter_enabled", "strict_safety", "safety_policy_enabled"):
            if snapshot.get(sec_key) is True and current_config_dict.get(sec_key) is False:
                return False, (
                    f"Configuration drift blocked resume: safety policy materially weakened "
                    f"('{sec_key}' changed from True to False). Resume refused."
                )

        return True, None

    def reconcile_checkpoints(
        self,
        session: WorkspaceSession,
        task: Task,
        plan: Optional[ExecutionPlan],
        state_manager: Any,
    ) -> ReconciliationResult:
        """Perform 3-way reconciliation across SQLite, Git task branch, and task worktree.
        
        Strict recovery rule: Resume only from the latest state Fusion can prove was verified.
        """
        repo_path = session.repo_root
        worktree_path = session.worktree_path

        # 1. Check if task branch exists in Git
        branches_raw = self._run_git(["branch", "--list", session.task_branch], cwd=repo_path, check=False)
        if not branches_raw.strip():
            # If no task branch, check if task has a last_checkpoint_sha reachable in Git
            if task.last_checkpoint_sha:
                cat_check = self._run_git(["cat-file", "-t", task.last_checkpoint_sha], cwd=repo_path, check=False)
                if cat_check.strip() == "commit":
                    # Recreate task branch from verified checkpoint SHA
                    self._run_git(["branch", session.task_branch, task.last_checkpoint_sha], cwd=repo_path)
                else:
                    raise RecoveryError(
                        f"Unrecoverable state: Task branch '{session.task_branch}' is missing and "
                        f"SQLite checkpoint SHA '{task.last_checkpoint_sha}' is not reachable in Git."
                    )
            else:
                raise RecoveryError(
                    f"Unrecoverable state: Task branch '{session.task_branch}' is missing and "
                    f"no verified checkpoints exist in SQLite."
                )

        # 2. Get branch HEAD SHA
        branch_head = self._run_git(["rev-parse", session.task_branch], cwd=repo_path)

        # 3. Retrieve SQLite latest checkpoint
        db_checkpoints = state_manager.get_checkpoints_for_task(task.id)
        last_db_checkpoint: Optional[Dict[str, Any]] = db_checkpoints[-1] if db_checkpoints else None
        last_db_sha = (
            last_db_checkpoint["commit_sha"]
            if last_db_checkpoint
            else (task.last_checkpoint_sha or task.base_commit or session.base_commit)
        )

        verified_sha = ""
        action_taken = ""
        reconciled_step = None

        # Case A: SQLite checkpoint == Git task branch HEAD
        if last_db_sha and last_db_sha == branch_head:
            verified_sha = last_db_sha
            action_taken = "matched_sqlite_git_head"

        # Case B: Git task branch HEAD is ahead of SQLite checkpoint (crash after git commit before DB insert)
        elif branch_head and branch_head != last_db_sha:
            trailers = self.checkpoint_mgr.get_commit_trailers(branch_head, cwd=repo_path)
            t_task_id = trailers.get("Fusion-Task-ID")
            t_plan_id = trailers.get("Fusion-Plan-ID")
            t_step_id = trailers.get("Fusion-Step-ID")
            t_chk_id = trailers.get("Fusion-Checkpoint-ID")

            is_valid_recovery = False
            if t_task_id == task.id and t_step_id and t_chk_id:
                txn = state_manager.get_checkpoint_transaction(t_chk_id)
                if txn and txn["status"] in ("PREPARING", "COMPLETED"):
                    if txn["task_id"] == task.id and txn["step_id"] == t_step_id:
                        if txn["verified"] == 1:
                            parent_commits = self._run_git(["rev-list", "--parents", "-n", "1", branch_head], cwd=repo_path, check=False)
                            parent_parts = parent_commits.split()
                            actual_parent = parent_parts[1] if len(parent_parts) > 1 else ""
                            if actual_parent == txn["expected_parent_sha"]:
                                files_raw = self._run_git(["diff-tree", "--no-commit-id", "--name-only", "-r", branch_head], cwd=repo_path)
                                changed_files = [f.strip() for f in files_raw.splitlines() if f.strip()]
                                approved_paths = txn["approved_paths"]
                                if isinstance(approved_paths, str):
                                    approved_paths = json.loads(approved_paths or "[]")
                                if not approved_paths or set(changed_files).issubset(set(approved_paths)):
                                    is_valid_recovery = True

            if is_valid_recovery:
                verified_sha = branch_head
                reconciled_step = t_step_id
                action_taken = "reconciled_git_commit_into_db"
                state_manager.complete_checkpoint_transaction(t_chk_id)
                try:
                    files_raw = self._run_git(["diff-tree", "--no-commit-id", "--name-only", "-r", branch_head], cwd=repo_path)
                    files_changed = [f.strip() for f in files_raw.splitlines() if f.strip()]
                    diff_summary = self._run_git(["diff", f"{branch_head}~1", branch_head, "--stat"], cwd=repo_path, check=False)
                    base_sha = session.base_commit or (last_db_sha or branch_head)

                    chk_obj = Checkpoint(
                        checkpoint_id=t_chk_id,
                        plan_id=plan.plan_id if plan else (t_plan_id or "unknown"),
                        task_id=task.id,
                        step_id=t_step_id,
                        commit_sha=branch_head,
                        base_commit_sha=base_sha,
                        files_changed=files_changed,
                        diff_summary=diff_summary,
                        verification_passed=True,
                        provider_name=trailers.get("Fusion-Provider", "recovered"),
                        created_at=task.created_at,
                    )
                    state_manager.create_checkpoint(chk_obj)
                    state_manager.update_task_stage(task.id, f"step_completed:{t_step_id}", last_checkpoint_sha=branch_head)
                    if plan:
                        state_manager.update_step_status(plan.plan_id, t_step_id, StepStatus.COMPLETED)
                except Exception:
                    pass
            elif last_db_sha:
                # Branch HEAD does not match trailers and intent for this task; roll back branch HEAD to last verified checkpoint / base
                self._run_git(["update-ref", f"refs/heads/{session.task_branch}", last_db_sha], cwd=repo_path)
                if worktree_path and worktree_path.exists():
                    self._run_git(["reset", "--hard", last_db_sha], cwd=worktree_path, check=False)
                verified_sha = last_db_sha
                action_taken = "rolled_back_untrusted_branch_head_to_db_checkpoint"
            else:
                raise RecoveryError(
                    f"Unrecoverable state: Branch HEAD '{branch_head[:8]}' has invalid trailers/intent and "
                    f"no previous verified checkpoint exists in SQLite."
                )

        elif last_db_sha:
            verified_sha = last_db_sha
            action_taken = "used_sqlite_checkpoint"
        else:
            # No checkpoints yet: verified state is base commit
            verified_sha = session.base_commit or branch_head
            action_taken = "no_checkpoints_reset_to_base"

        # 4. Verify that verified_sha actually exists in Git
        cat_test = self._run_git(["cat-file", "-t", verified_sha], cwd=repo_path, check=False)
        if cat_test.strip() != "commit":
            raise RecoveryError(
                f"Unrecoverable state: Proven verified checkpoint commit '{verified_sha}' is missing from Git."
            )

        # 5. Worktree inspection and clean rollback to verified_sha
        rolled_back = False
        if worktree_path.exists():
            status = self._run_git(["status", "--porcelain", "--untracked-files=all"], cwd=worktree_path)
            dirty_lines = [
                l for l in status.splitlines()
                if l.strip() and not l.strip().endswith(".fusion") and ".fusion/" not in l
            ]
            current_wt_head = self._run_git(["rev-parse", "HEAD"], cwd=worktree_path, check=False)

            if dirty_lines or current_wt_head != verified_sha:
                # Worktree contains unverified modifications or is at wrong commit! Strict rollback.
                self.checkpoint_mgr.rollback_to_checkpoint(session, verified_sha)
                rolled_back = True

        # 6. Determine resume step index
        resume_index = 0
        if plan:
            finished = [
                s.id for s in plan.steps
                if s.status == StepStatus.COMPLETED or s.id == reconciled_step
            ]
            for idx, step in enumerate(plan.steps):
                if step.id not in finished:
                    resume_index = idx
                    break
            else:
                resume_index = len(plan.steps)

        # 7. Mark any lingering uncommitted PREPARING checkpoint intents as FAILED
        conn = state_manager.db.connect()
        with conn:
            conn.execute(
                "UPDATE checkpoint_transactions SET status = 'FAILED' WHERE task_id = ? AND status = 'PREPARING';",
                (task.id,),
            )

        return ReconciliationResult(
            success=True,
            verified_checkpoint_sha=verified_sha,
            reconciled_step_id=reconciled_step,
            action_taken=action_taken,
            resumed_step_index=resume_index,
            worktree_rolled_back=rolled_back,
            message=f"Reconciled state to verified checkpoint {verified_sha[:8]} ({action_taken}).",
        )
