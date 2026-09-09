"""Atomic promotion and merge engine for verified WorkspaceSessions."""

import subprocess
from dataclasses import dataclass
from typing import Optional

from fusion_agent.workspace.session import WorkspaceSession, WorkspaceState


@dataclass
class PromotionResult:
    """Outcome of promoting a verified worktree session to the target branch."""
    success: bool
    commit_hash: Optional[str] = None
    target_branch: str = ""
    message: str = ""


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

    def promote(
        self,
        session: WorkspaceSession,
        target_branch: Optional[str] = None,
        commit_message: Optional[str] = None,
    ) -> PromotionResult:
        """Promote changes from the isolated task branch into the target repository branch.
        
        Enforces target-branch concurrency protection against session.base_commit.
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

        # 4. Stage and commit all modifications in worktree
        self._run_git(["add", "-A"], cwd=worktree_path)
        status_res = self._run_git(["status", "--porcelain"], cwd=worktree_path)
        if not status_res.stdout.strip():
            session.state = WorkspaceState.PROMOTED
            session.teardown(delete_branch=True)
            return PromotionResult(
                success=True,
                target_branch=target_branch,
                message="No modifications detected in worktree; session cleanly closed.",
            )

        msg = commit_message or f"feat(fusion): implement autonomous task {session.task_id}"
        commit_res = self._run_git(["commit", "-m", msg], cwd=worktree_path)
        if commit_res.returncode != 0:
            return PromotionResult(
                success=False,
                target_branch=target_branch,
                message=f"Failed to commit changes in worktree: {commit_res.stderr.strip()}",
            )

        # 5. Merge task branch into main repository target branch
        merge_res = self._run_git(["merge", session.task_branch], cwd=repo_path)
        if merge_res.returncode != 0:
            return PromotionResult(
                success=False,
                target_branch=target_branch,
                message=(
                    f"Merge conflict or divergence while applying task branch '{session.task_branch}' to '{target_branch}'.\n"
                    f"Git stderr: {merge_res.stderr.strip() or merge_res.stdout.strip()}"
                ),
            )

        promoted_commit = self._run_git(["rev-parse", "HEAD"], cwd=repo_path).stdout.strip()

        # 6. Clean teardown
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
