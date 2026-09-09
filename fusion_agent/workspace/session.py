"""Workspace Session management using isolated ephemeral Git worktrees."""

import os
import shutil
import subprocess
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional


class WorkspaceState(str, Enum):
    """Lifecycle states of a WorkspaceSession."""
    INITIALIZING = "INITIALIZING"
    PREPARED = "PREPARED"
    ACTIVE = "ACTIVE"
    VERIFYING = "VERIFYING"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    PROMOTED = "PROMOTED"
    DISCARDED = "DISCARDED"
    FAILED = "FAILED"


class DirtyWorkingTreeError(RuntimeError):
    """Raised when an autonomous session is attempted on a dirty repository."""
    pass


class WorkspaceSession:
    """Manages an isolated Git worktree workspace for safe autonomous agent operations."""

    def __init__(
        self,
        task_id: str,
        repo_root: Optional[Path] = None,
        custom_worktree_dir: Optional[Path] = None,
    ):
        self.task_id = task_id
        self.repo_root = (repo_root or Path.cwd()).resolve()
        self.worktree_dir = (
            custom_worktree_dir or (self.repo_root / ".fusion" / "worktrees" / f"task-{task_id}")
        ).resolve()
        self.task_branch = f"fusion/task-{task_id}"
        self.base_commit: str = ""
        self.base_branch: str = ""
        self.state: WorkspaceState = WorkspaceState.INITIALIZING
        self.created_at: str = datetime.now(timezone.utc).isoformat()

    def _run_git(self, args: list, cwd: Optional[Path] = None, check: bool = True) -> str:
        """Run a git command in the repository or worktree."""
        cmd = ["git"] + args
        work_dir = str(cwd or self.repo_root)
        proc = subprocess.run(
            cmd,
            cwd=work_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if check and proc.returncode != 0:
            raise RuntimeError(
                f"Git command failed: {' '.join(cmd)}\nStderr: {proc.stderr.strip() or proc.stdout.strip()}"
            )
        return proc.stdout.strip()

    def prepare(self) -> None:
        """Initialize the isolated Git worktree for this session."""
        # 1. Verify git repository
        is_git = self._run_git(["rev-parse", "--is-inside-work-tree"], check=False)
        if is_git != "true":
            raise RuntimeError(f"Directory {self.repo_root} is not a valid Git repository.")

        # 2. Check uncommitted or untracked changes in main working tree (Strict refusal in V1)
        status = self._run_git(["status", "--porcelain"])
        # Filter out internal .fusion directory entries if any
        dirty_lines = [
            line for line in status.splitlines()
            if line.strip() and not line.strip().endswith(".fusion") and ".fusion/" not in line
        ]
        if dirty_lines:
            sample = "\n".join(dirty_lines[:5])
            raise DirtyWorkingTreeError(
                f"Cannot start isolated workspace: target repository has uncommitted or untracked changes.\n"
                f"Fusion Agent V1 requires a clean working tree to ensure reproducible, conflict-free "
                f"verification and prevent data loss. Please commit, stash, or clean your working tree.\n"
                f"Dirty files:\n{sample}"
            )

        # 3. Resolve base commit and active branch
        self.base_commit = self._run_git(["rev-parse", "HEAD"])
        self.base_branch = self._run_git(["branch", "--show-current"]) or "master"

        # 4. Clean up any leftover worktree or branch from previous aborted attempts
        if self.worktree_dir.exists():
            self._run_git(["worktree", "remove", "--force", str(self.worktree_dir)], check=False)
            shutil.rmtree(self.worktree_dir, ignore_errors=True)

        self._run_git(["branch", "-D", self.task_branch], check=False)

        # 5. Create ephemeral worktree on dedicated branch
        self.worktree_dir.parent.mkdir(parents=True, exist_ok=True)
        self._run_git([
            "worktree", "add", "-b", self.task_branch, str(self.worktree_dir), "HEAD"
        ])

        self.state = WorkspaceState.PREPARED

    def teardown(self, delete_branch: bool = True) -> None:
        """Tear down and prune the ephemeral worktree and task branch."""
        # Remove worktree
        if self.worktree_dir.exists():
            self._run_git(["worktree", "remove", "--force", str(self.worktree_dir)], check=False)
            shutil.rmtree(self.worktree_dir, ignore_errors=True)

        self._run_git(["worktree", "prune"], check=False)

        # Delete branch if specified and not promoted
        if delete_branch and self.state != WorkspaceState.PROMOTED:
            self._run_git(["branch", "-D", self.task_branch], check=False)

        if self.state != WorkspaceState.PROMOTED:
            self.state = WorkspaceState.DISCARDED

    def __enter__(self) -> "WorkspaceSession":
        self.prepare()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.state != WorkspaceState.PROMOTED:
            self.teardown(delete_branch=True)
