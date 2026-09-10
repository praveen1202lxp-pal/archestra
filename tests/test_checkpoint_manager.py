"""Tests for CheckpointManager: command-local git identity, selective staging, and exact rollback."""

import subprocess
from pathlib import Path

import pytest

from fusion_agent.workspace.checkpoint import (
    CheckpointManager,
    CheckpointRollbackError,
    UnexpectedFilesError,
)
from fusion_agent.workspace.session import WorkspaceSession


@pytest.fixture
def git_repo(tmp_path: Path):
    """Create a temporary initialized Git repository with an initial commit."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "--allow-empty", "-m", "init"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    return repo


def test_checkpoint_uses_command_local_git_identity(git_repo: Path):
    """Verify that creating a checkpoint does not modify global or repository-level git user config."""
    # Check baseline config
    orig_name = subprocess.run(
        ["git", "config", "user.name"], cwd=str(git_repo), capture_output=True, text=True
    ).stdout.strip()
    orig_email = subprocess.run(
        ["git", "config", "user.email"], cwd=str(git_repo), capture_output=True, text=True
    ).stdout.strip()

    session = WorkspaceSession(task_id="local-id", repo_root=git_repo)
    session.prepare()
    try:
        mgr = CheckpointManager()
        test_file = session.worktree_path / "hello.py"
        test_file.write_text("print('hello')", encoding="utf-8")

        chk = mgr.create_checkpoint(
            session=session,
            plan_id="plan-1",
            step_id="step-1",
            objective_summary="add hello",
            approved_files=["hello.py"],
        )
        assert chk.commit_sha
        assert "hello.py" in chk.files_changed

        # Verify git commit author uses Fusion Engine identity
        author = subprocess.run(
            ["git", "log", "-1", "--format=%an <%ae>"],
            cwd=str(session.worktree_path),
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert "Fusion Engine" in author
        assert "fusion@local" in author

        # Verify repository and global config were NOT mutated
        current_name = subprocess.run(
            ["git", "config", "user.name"], cwd=str(git_repo), capture_output=True, text=True
        ).stdout.strip()
        current_email = subprocess.run(
            ["git", "config", "user.email"], cwd=str(git_repo), capture_output=True, text=True
        ).stdout.strip()
        assert current_name == orig_name
        assert current_email == orig_email
    finally:
        session.teardown(delete_branch=True)


def test_selective_staging_rejects_unexpected_files(git_repo: Path):
    """Verify that unapproved or rogue files created during a step trigger UnexpectedFilesError."""
    session = WorkspaceSession(task_id="rogue-check", repo_root=git_repo)
    session.prepare()
    try:
        mgr = CheckpointManager()
        # Legitimate approved file
        (session.worktree_path / "service.py").write_text("# service", encoding="utf-8")
        # Unexpected rogue file (e.g. emitted by a bad script or external build)
        (session.worktree_path / "rogue_dump.tmp").write_text("bad data", encoding="utf-8")

        with pytest.raises(UnexpectedFilesError) as exc_info:
            mgr.create_checkpoint(
                session=session,
                plan_id="plan-rogue",
                step_id="step-1",
                objective_summary="create service",
                approved_files=["service.py"],  # rogue_dump.tmp is NOT approved
            )
        assert "rogue_dump.tmp" in str(exc_info.value)
    finally:
        session.teardown(delete_branch=True)


def test_exact_rollback_removes_untracked_files(git_repo: Path):
    """Verify that rollback restores the worktree exactly to the checkpoint, removing untracked files."""
    session = WorkspaceSession(task_id="rollback-check", repo_root=git_repo)
    session.prepare()
    try:
        mgr = CheckpointManager()
        # Step 1: legitimate edit and checkpoint
        (session.worktree_path / "base.py").write_text("BASE = True\n", encoding="utf-8")
        chk1 = mgr.create_checkpoint(
            session=session,
            plan_id="plan-rb",
            step_id="step-1",
            objective_summary="initial base",
            approved_files=["base.py"],
        )

        # Step 2: introduces modified file + new untracked file, then fails
        (session.worktree_path / "base.py").write_text("BASE = CORRUPTED\n", encoding="utf-8")
        untracked = session.worktree_path / "untracked_fail.py"
        untracked.write_text("broken code\n", encoding="utf-8")

        # Rollback to Step 1 checkpoint
        mgr.rollback_to_checkpoint(
            session=session,
            checkpoint_sha=chk1.commit_sha,
            created_files=["untracked_fail.py"],
        )

        # Assert untracked file was purged and base.py was restored
        assert not untracked.exists()
        assert (session.worktree_path / "base.py").read_text(encoding="utf-8") == "BASE = True\n"

        # Assert working tree status is completely clean
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(session.worktree_path),
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert status == ""
    finally:
        session.teardown(delete_branch=True)


def test_checkpoint_diff_generation(git_repo: Path):
    """Verify that get_diff_between extracts exact unified diffs between checkpoints."""
    session = WorkspaceSession(task_id="diff-check", repo_root=git_repo)
    session.prepare()
    try:
        mgr = CheckpointManager()
        (session.worktree_path / "code.py").write_text("def a(): return 1\n", encoding="utf-8")
        chk1 = mgr.create_checkpoint(session, "p", "s1", "step 1", approved_files=["code.py"])

        (session.worktree_path / "code.py").write_text("def a(): return 2\n", encoding="utf-8")
        chk2 = mgr.create_checkpoint(session, "p", "s2", "step 2", approved_files=["code.py"])

        diff = mgr.get_diff_between(session, chk1.commit_sha, chk2.commit_sha)
        assert "-def a(): return 1" in diff
        assert "+def a(): return 2" in diff
    finally:
        session.teardown(delete_branch=True)
