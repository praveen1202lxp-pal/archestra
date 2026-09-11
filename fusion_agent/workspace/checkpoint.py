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
        objective_summary: Any = "",
        approved_files: Optional[List[str]] = None,
        verification_passed: bool = True,
        provider_name: str = "",
        token_metrics: Optional[Dict[str, Any]] = None,
        base_commit_sha: Optional[str] = None,
        verification_id: Optional[str] = None,
        state_manager: Optional[Any] = None,
        expected_files: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Checkpoint:
        """Stage only approved task paths, record write-ahead intent, commit with trailers, and record state."""
        worktree = session.worktree_path

        if approved_files is None and expected_files is not None:
            approved_files = expected_files
        if approved_files is None and isinstance(objective_summary, (list, tuple)):
            approved_files = list(objective_summary)
            objective_summary = f"Implement {step_id}"

        # 1. Inspect git status before staging (use --untracked-files=all so individual files are listed)
        status_raw = self._run_git(["status", "--porcelain", "--untracked-files=all"], cwd=worktree)
        changed_paths: Set[str] = set()
        for line in status_raw.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line[2:].strip().split(" -> ")
            rel_file = parts[-1].strip().replace("\\", "/")
            if not rel_file.startswith(".fusion") and "/.fusion/" not in rel_file:
                changed_paths.add(rel_file)

        # 2. Validate against approved paths if provided
        if approved_files is not None:
            norm_approved = {f.replace("\\", "/").strip("./") for f in approved_files}
            unexpected = [
                p for p in changed_paths
                if p not in norm_approved and not any(a == p or a.startswith(p if p.endswith("/") else p + "/") for a in norm_approved)
            ]
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
        checkpoint_id = str(uuid.uuid4())[:8]
        expected_parent_sha = self._run_git(["rev-parse", "HEAD"], cwd=worktree)

        # Write-ahead checkpoint transaction intent in PREPARING status
        if state_manager and hasattr(state_manager, "record_checkpoint_transaction_intent"):
            state_manager.record_checkpoint_transaction_intent(
                checkpoint_id=checkpoint_id,
                task_id=session.task_id,
                plan_id=plan_id,
                step_id=step_id,
                expected_parent_sha=expected_parent_sha,
                verification_id=verification_id or "verified",
                approved_paths=list(paths_to_stage),
                verified=verification_passed,
            )

        try:
            if paths_to_stage:
                summary_text = " ".join(objective_summary) if isinstance(objective_summary, (list, tuple)) else str(objective_summary or "")
                commit_msg = (
                    f"checkpoint({step_id}): {summary_text.strip()}\n\n"
                    f"Fusion-Task-ID: {session.task_id}\n"
                    f"Fusion-Plan-ID: {plan_id}\n"
                    f"Fusion-Step-ID: {step_id}\n"
                    f"Fusion-Checkpoint-ID: {checkpoint_id}\n"
                )
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

            # Mark checkpoint transaction COMPLETED
            if state_manager and hasattr(state_manager, "complete_checkpoint_transaction"):
                state_manager.complete_checkpoint_transaction(checkpoint_id)

        except Exception as exc:
            if state_manager and hasattr(state_manager, "fail_checkpoint_transaction"):
                state_manager.fail_checkpoint_transaction(checkpoint_id)
            raise exc

        base_sha = base_commit_sha or session.base_commit or commit_sha

        checkpoint = Checkpoint(
            checkpoint_id=checkpoint_id,
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

    @classmethod
    def get_commit_trailers(cls, commit_sha: str, cwd: Path) -> Dict[str, str]:
        """Extract Fusion metadata trailers from a Git commit."""
        raw_msg = cls._run_git(["log", "-1", "--format=%B", commit_sha], cwd=cwd, check=False)
        trailers: Dict[str, str] = {}
        for line in raw_msg.splitlines():
            line = line.strip()
            if line.startswith("Fusion-") and ":" in line:
                k, v = line.split(":", 1)
                trailers[k.strip()] = v.strip()
        return trailers

    def rollback_to_checkpoint(
        self,
        session: WorkspaceSession,
        checkpoint_sha: str,
        created_files: Optional[List[str]] = None,
    ) -> None:
        """Restore the isolated worktree strictly to checkpoint_sha using selective rollback."""
        worktree = session.worktree_path

        # 1. Reset tracked files to the verified checkpoint commit
        self._run_git(["reset", "--hard", checkpoint_sha], cwd=worktree)

        # 2. Specifically remove task-created files if they were NOT tracked in checkpoint_sha
        if created_files:
            for rel_f in created_files:
                norm_rel = rel_f.replace("\\", "/").strip("./")
                is_tracked = self._run_git(
                    ["ls-tree", "--name-only", checkpoint_sha, "--", norm_rel],
                    cwd=worktree,
                    check=False,
                ).strip()
                if not is_tracked:
                    target_file = (worktree / norm_rel).resolve()
                    if str(target_file).startswith(str(worktree)) and target_file.is_file():
                        try:
                            target_file.unlink()
                        except Exception:
                            pass

        # 3. Selectively remove unverified/untracked task files inside worktree (preserving .fusion)
        status_raw = self._run_git(["status", "--porcelain", "--untracked-files=all"], cwd=worktree)
        for line in status_raw.splitlines():
            line = line.strip()
            if line.startswith("??"):
                rel_path = line[2:].strip().replace("\\", "/")
                if not rel_path.startswith(".fusion") and "/.fusion/" not in rel_path:
                    target_file = (worktree / rel_path).resolve()
                    if str(target_file).startswith(str(worktree)) and target_file.is_file():
                        try:
                            target_file.unlink()
                        except Exception:
                            pass

        # 4. Verify post-rollback state
        status = self._run_git(["status", "--porcelain"], cwd=worktree)
        dirty_lines = [
            l for l in status.splitlines()
            if l.strip() and not l.strip().endswith(".fusion") and ".fusion/" not in l
        ]
        if dirty_lines:
            raise CheckpointRollbackError(
                f"Worktree selective rollback to {checkpoint_sha[:8]} failed to restore clean state.\n"
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
