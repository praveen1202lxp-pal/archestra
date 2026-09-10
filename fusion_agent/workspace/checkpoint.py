"""Native Git checkpoint management within an isolated WorkspaceSession."""

import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from fusion_agent.models.plan import Checkpoint
from fusion_agent.workspace.session import WorkspaceSession


class UnexpectedFilesError(RuntimeError):
    """Raised when unapproved or unexpected files are modified/created during a step."""
    pass


class CheckpointRollbackError(RuntimeError):
    """Raised when rollback fails to restore the expected clean checkpoint state."""
    pass


class CheckpointManager:
    """Manages internal Git commits and immutable checkpoints on isolated task branches."""

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
                f"Git command failed in worktree ({' '.join(args)}):\n"
                f"{proc.stderr.strip() or proc.stdout.strip()}"
            )
        return proc.stdout.strip()

    def create_checkpoint(
        self,
        session: WorkspaceSession,
        plan_id: str,
        step_id: str,
        objective_summary: str,
        approved_files: Optional[List[str]] = None,
        verification_passed: bool = True,
        provider_name: str = "",
        token_metrics: Optional[Dict[str, Any]] = None,
        base_commit_sha: Optional[str] = None,
    ) -> Checkpoint:
        """Stage only approved task paths, commit with command-local identity, and record state."""
        worktree = session.worktree_path

        # 1. Inspect git status before staging
        status_raw = self._run_git(["status", "--porcelain"], cwd=worktree)
        changed_paths: Set[str] = set()
        for line in status_raw.splitlines():
            line = line.strip()
            if not line:
                continue
            # Format: XY <file> or XY <orig> -> <file>
            parts = line[2:].strip().split(" -> ")
            rel_file = parts[-1].strip().replace("\\", "/")
            if not rel_file.startswith(".fusion") and "/.fusion/" not in rel_file:
                changed_paths.add(rel_file)

        # 2. Validate against approved paths if provided
        if approved_files is not None:
            norm_approved = {f.replace("\\", "/").strip("./") for f in approved_files}
            unexpected = [p for p in changed_paths if p not in norm_approved]
            if unexpected:
                raise UnexpectedFilesError(
                    f"Checkpoint rejected for {step_id}: unexpected files were created or modified "
                    f"by verification or external process: {', '.join(unexpected)}"
                )

        # 3. Stage only approved paths (or changed paths if approved not specified)
        paths_to_stage = list(norm_approved & changed_paths) if approved_files is not None else list(changed_paths)
        for p in paths_to_stage:
            self._run_git(["add", "--", p], cwd=worktree)

        # 4. Check status of staged files
        diff_summary = ""
        files_changed: List[str] = []

        if paths_to_stage:
            commit_msg = f"checkpoint({step_id}): {objective_summary.strip()}"
            # Command-local Git identity: does NOT modify global or local repo config
            self._run_git([
                "-c", "user.name=Fusion Engine",
                "-c", "user.email=fusion@local",
                "commit", "-m", commit_msg
            ], cwd=worktree)

            commit_sha = self._run_git(["rev-parse", "HEAD"], cwd=worktree)
            files_raw = self._run_git(["diff-tree", "--no-commit-id", "--name-only", "-r", commit_sha], cwd=worktree)
            files_changed = [f.strip() for f in files_raw.splitlines() if f.strip()]
            diff_summary = self._run_git(["diff", "HEAD~1", "HEAD", "--stat"], cwd=worktree)
        else:
            commit_sha = self._run_git(["rev-parse", "HEAD"], cwd=worktree)
            diff_summary = "No file changes in this step."

        base_sha = base_commit_sha or session.base_commit or commit_sha

        checkpoint = Checkpoint(
            checkpoint_id=str(uuid.uuid4())[:8],
            plan_id=plan_id,
            task_id=session.task_id,
            step_id=step_id,
            commit_sha=commit_sha,
            base_commit_sha=base_sha,
            files_changed=files_changed,
            diff_summary=diff_summary,
            verification_passed=verification_passed,
            provider_name=provider_name,
            token_metrics=token_metrics or {},
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        return checkpoint

    def rollback_to_checkpoint(
        self,
        session: WorkspaceSession,
        checkpoint_sha: str,
        created_files: Optional[List[str]] = None,
    ) -> None:
        """Restore the isolated worktree strictly to checkpoint_sha, removing untracked task files."""
        worktree = session.worktree_path

        # 1. Reset tracked files
        self._run_git(["reset", "--hard", checkpoint_sha], cwd=worktree)

        # 2. Specifically remove known task-created files if still present
        if created_files:
            for rel_f in created_files:
                target_file = (worktree / rel_f).resolve()
                # Security constraint: only delete if inside the worktree
                if str(target_file).startswith(str(worktree)) and target_file.is_file():
                    try:
                        target_file.unlink()
                    except Exception:
                        pass

        # 3. Clean any remaining untracked files strictly inside worktree
        self._run_git(["clean", "-fd"], cwd=worktree)

        # 4. Verify post-rollback state
        status = self._run_git(["status", "--porcelain"], cwd=worktree)
        dirty_lines = [
            l for l in status.splitlines()
            if l.strip() and not l.strip().endswith(".fusion") and ".fusion/" not in l
        ]
        if dirty_lines:
            raise CheckpointRollbackError(
                f"Worktree rollback to {checkpoint_sha[:8]} failed to restore clean state.\n"
                f"Remaining status:\n{chr(10).join(dirty_lines)}"
            )

    def get_diff_between(
        self,
        session: WorkspaceSession,
        from_sha: str,
        to_sha: str,
    ) -> str:
        """Extract unified diff between two commit SHAs on the task branch."""
        worktree = session.worktree_path
        return self._run_git(["diff", from_sha, to_sha], cwd=worktree, check=False)
