import hashlib
import subprocess
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from fusion_agent.models.plan import PromotionTransaction, PromotionTransactionStatus
from fusion_agent.workspace.session import WorkspaceSession, WorkspaceState


@dataclass
class PromotionResult:
    """Outcome of promoting a verified worktree session to the target branch."""
    success: bool
    commit_hash: Optional[str] = None
    target_branch: str = ""
    message: str = ""
    requires_manual_reconciliation: bool = False


class PromotionEngine:
    """Manages transactional squash/merge promotion of verified WorkspaceSessions."""

    @staticmethod
    def _run_git(args: list, cwd: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def reconcile_interrupted_promotion(
        self,
        session: WorkspaceSession,
        state_manager: Any,
        target_branch: Optional[str] = None,
    ) -> Optional[PromotionResult]:
        """Reconcile an interrupted promotion transaction following a crash."""
        if not state_manager or not hasattr(state_manager, "get_active_promotion_transaction"):
            return None

        txn = state_manager.get_active_promotion_transaction(session.task_id)
        if not txn or txn.status in (PromotionTransactionStatus.NOT_STARTED, PromotionTransactionStatus.AWAITING_HUMAN):
            return None

        repo_path = str(session.repo_root)
        tgt = target_branch or txn.target_branch or session.base_branch or "master"

        # If already recorded as APPLIED, verify that the commit exists in Git
        if txn.status == PromotionTransactionStatus.APPLIED and txn.resulting_target_sha:
            check = self._run_git(["cat-file", "-t", txn.resulting_target_sha], cwd=repo_path)
            if check.stdout.strip() == "commit":
                session.state = WorkspaceState.PROMOTED
                session.teardown(delete_branch=True)
                return PromotionResult(
                    success=True,
                    commit_hash=txn.resulting_target_sha,
                    target_branch=tgt,
                    message=f"Reconciled completed promotion: commit {txn.resulting_target_sha[:8]} already applied.",
                )

        # If interrupted during PREPARING: inspect target branch log for matching trailers
        if txn.status == PromotionTransactionStatus.PREPARING:
            log_res = self._run_git(["log", "-5", "--format=%H%x1f%B%x1e", tgt], cwd=repo_path)
            if log_res.returncode == 0 and log_res.stdout.strip():
                entries = log_res.stdout.strip().split("\x1e")
                for entry in entries:
                    if not entry.strip():
                        continue
                    parts = entry.strip().split("\x1f", 1)
                    c_sha = parts[0].strip()
                    c_msg = parts[1] if len(parts) > 1 else ""

                    if f"Fusion-Promotion-ID: {txn.id}" in c_msg:
                        # Extract and verify all trailers
                        t_task_id = None
                        t_task_head = None
                        t_diff_hash = None
                        for line in c_msg.splitlines():
                            line = line.strip()
                            if line.startswith("Fusion-Task-ID:"):
                                t_task_id = line.split(":", 1)[1].strip()
                            elif line.startswith("Fusion-Task-Head:"):
                                t_task_head = line.split(":", 1)[1].strip()
                            elif line.startswith("Fusion-Diff-Hash:"):
                                t_diff_hash = line.split(":", 1)[1].strip()

                        # Strict validation: Task ID, Task Head, and Diff Hash must all agree
                        if (
                            t_task_id == session.task_id
                            and t_task_head == txn.task_head_sha
                            and t_diff_hash == txn.diff_hash
                        ):
                            state_manager.update_promotion_transaction(
                                txn.id,
                                status=PromotionTransactionStatus.APPLIED,
                                resulting_target_sha=c_sha,
                            )
                            session.state = WorkspaceState.PROMOTED
                            session.teardown(delete_branch=True)
                            return PromotionResult(
                                success=True,
                                commit_hash=c_sha,
                                target_branch=tgt,
                                message=f"Reconciled promotion commit {c_sha[:8]} from matching Git trailers.",
                            )
                        else:
                            # Trailers mismatched or corrupted!
                            state_manager.update_promotion_transaction(
                                txn.id,
                                status=PromotionTransactionStatus.REQUIRES_MANUAL_RECONCILIATION,
                                error_message="Trailers on discovered promotion commit did not match expected promotion transaction.",
                            )
                            return PromotionResult(
                                success=False,
                                target_branch=tgt,
                                message="Promotion commit trailers do not match transaction. REQUIRES_MANUAL_RECONCILIATION set.",
                                requires_manual_reconciliation=True,
                            )

            # Did not find promotion commit; mark FAILED or abort
            state_manager.update_promotion_transaction(
                txn.id,
                status=PromotionTransactionStatus.FAILED,
                error_message="Promotion process crashed before merge commit was created.",
            )

        return None

    def promote(
        self,
        session: WorkspaceSession,
        target_branch: Optional[str] = None,
        commit_message: Optional[str] = None,
        state_manager: Optional[Any] = None,
    ) -> PromotionResult:
        """Promote changes from the isolated task branch into the target repository branch.
        
        Enforces target-branch concurrency protection against session.base_commit and
        records write-ahead PromotionTransaction with durable commit trailers.
        """
        worktree_path = str(session.worktree_dir)
        repo_path = str(session.repo_root)

        # 1. Determine target branch
        if not target_branch:
            res = self._run_git(["branch", "--show-current"], cwd=repo_path)
            target_branch = res.stdout.strip() or session.base_branch or "master"

        # 2. Concurrency Protection: verify target branch HEAD has not diverged
        current_target_head = self._run_git(["rev-parse", target_branch], cwd=repo_path).stdout.strip()
        if session.base_commit and current_target_head and current_target_head != session.base_commit:
            return PromotionResult(
                success=False,
                target_branch=target_branch,
                message=(
                    f"Target branch concurrency violation: '{target_branch}' HEAD has moved from "
                    f"{session.base_commit[:8]} to {current_target_head[:8]} since session was prepared. "
                    f"Promotion aborted to prevent overwriting concurrent developer changes. "
                    f"Manual reconciliation required."
                ),
            )

        # 3. Verify main repository working tree is clean before merge
        repo_status = self._run_git(["status", "--porcelain"], cwd=repo_path).stdout.strip()
        dirty_lines = [
            line for line in repo_status.splitlines()
            if line.strip() and not line.strip().endswith(".fusion") and ".fusion/" not in line
        ]
        if dirty_lines:
            return PromotionResult(
                success=False,
                target_branch=target_branch,
                message=(
                    f"Cannot promote into '{target_branch}': the main repository working tree has "
                    f"uncommitted or untracked changes. Please commit or stash your changes before promoting."
                ),
            )

        # 4. Stage and commit any remaining modifications in worktree
        self._run_git(["add", "-A"], cwd=worktree_path)
        status_res = self._run_git(["status", "--porcelain"], cwd=worktree_path)
        if status_res.stdout.strip():
            msg = commit_message or f"feat(fusion): implement autonomous task {session.task_id}"
            commit_res = self._run_git(["commit", "-m", msg], cwd=worktree_path)
            if commit_res.returncode != 0:
                return PromotionResult(
                    success=False,
                    target_branch=target_branch,
                    message=f"Failed to commit changes in worktree: {commit_res.stderr.strip()}",
                )

        # 5. Check if task branch actually differs from base commit
        diff_against_base = self._run_git(["diff", session.base_commit, session.task_branch], cwd=repo_path).stdout.strip()
        if not diff_against_base:
            session.state = WorkspaceState.PROMOTED
            session.teardown(delete_branch=True)
            return PromotionResult(
                success=True,
                target_branch=target_branch,
                message="No modifications detected between task branch and target branch; session cleanly closed.",
            )

        # Compute diff hash and task HEAD SHA for transaction integrity
        diff_hash = hashlib.sha256(diff_against_base.encode("utf-8")).hexdigest()[:16]
        promotion_id = str(uuid.uuid4())[:8]
        task_head_sha = ""

        # Write-ahead PromotionTransaction in PREPARING status
        if state_manager and hasattr(state_manager, "record_promotion_transaction"):
            task_head_sha = self._run_git(["rev-parse", session.task_branch], cwd=repo_path).stdout.strip()
            txn = PromotionTransaction(
                id=promotion_id,
                task_id=session.task_id,
                target_branch=target_branch,
                expected_target_sha=session.base_commit or current_target_head,
                task_branch=session.task_branch,
                task_head_sha=task_head_sha,
                diff_hash=diff_hash,
                status=PromotionTransactionStatus.PREPARING,
            )
            state_manager.record_promotion_transaction(txn)

        # 6. Squash merge task branch into main repository target branch
        self._run_git(["checkout", target_branch], cwd=repo_path)
        merge_res = self._run_git(["merge", "--squash", session.task_branch], cwd=repo_path)
        if merge_res.returncode != 0:
            self._run_git(["merge", "--abort"], cwd=repo_path)
            if state_manager and hasattr(state_manager, "update_promotion_transaction"):
                state_manager.update_promotion_transaction(
                    promotion_id,
                    status=PromotionTransactionStatus.FAILED,
                    error_message=f"Merge conflict during squash: {merge_res.stderr.strip()}",
                )
            return PromotionResult(
                success=False,
                target_branch=target_branch,
                message=(
                    f"Merge conflict or divergence while applying task branch '{session.task_branch}' to '{target_branch}'.\n"
                    f"Git stderr: {merge_res.stderr.strip() or merge_res.stdout.strip()}"
                ),
            )

        # Append durable Fusion promotion metadata trailers to squashed commit
        base_msg = commit_message or f"feat(fusion): implement autonomous task {session.task_id}"
        full_commit_msg = (
            f"{base_msg.strip()}\n\n"
            f"Fusion-Task-ID: {session.task_id}\n"
            f"Fusion-Promotion-ID: {promotion_id}\n"
            f"Fusion-Task-Head: {task_head_sha}\n"
            f"Fusion-Diff-Hash: {diff_hash}\n"
        )

        commit_res = self._run_git(["commit", "-m", full_commit_msg], cwd=repo_path)
        if commit_res.returncode != 0:
            if state_manager and hasattr(state_manager, "update_promotion_transaction"):
                state_manager.update_promotion_transaction(
                    promotion_id,
                    status=PromotionTransactionStatus.FAILED,
                    error_message=f"Failed to commit squashed changes: {commit_res.stderr.strip()}",
                )
            return PromotionResult(
                success=False,
                target_branch=target_branch,
                message=f"Failed to commit squashed changes in target branch '{target_branch}': {commit_res.stderr.strip()}",
            )

        promoted_commit = self._run_git(["rev-parse", "HEAD"], cwd=repo_path).stdout.strip()

        # Update PromotionTransaction to APPLIED
        if state_manager and hasattr(state_manager, "update_promotion_transaction"):
            state_manager.update_promotion_transaction(
                promotion_id,
                status=PromotionTransactionStatus.APPLIED,
                resulting_target_sha=promoted_commit,
            )

        # 7. Clean teardown
        session.state = WorkspaceState.PROMOTED
        session.teardown(delete_branch=True)

        return PromotionResult(
            success=True,
            commit_hash=promoted_commit,
            target_branch=target_branch,
            message=f"Successfully promoted changes into {target_branch} (commit {promoted_commit[:8]}).",
        )


    def discard(self, session: WorkspaceSession) -> None:
        """Discard an unverified or rejected session with zero leftover residue."""
        session.teardown(delete_branch=True)
        session.state = WorkspaceState.DISCARDED
