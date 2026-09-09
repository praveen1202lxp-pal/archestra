"""Unit tests for WorkspaceSession."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fusion_agent.workspace.session import (
    DirtyWorkingTreeError,
    WorkspaceSession,
    WorkspaceState,
)


def test_session_init_paths():
    """Initializes paths correctly relative to repository root."""
    repo_root = Path("C:/mock/repo")
    session = WorkspaceSession(task_id="abc123", repo_root=repo_root)

    assert session.task_id == "abc123"
    assert session.repo_root == repo_root.resolve()
    assert session.task_branch == "fusion/task-abc123"
    assert session.state == WorkspaceState.INITIALIZING
    assert session.worktree_dir.name == "task-abc123"


def test_session_prepare_dirty_repo_fails():
    """Session preparation raises DirtyWorkingTreeError if working tree has uncommitted or untracked changes."""
    session = WorkspaceSession(task_id="test1", repo_root=Path("C:/mock/repo"))

    with patch.object(session, "_run_git") as mock_git:
        # rev-parse is-inside-work-tree -> true; status -> dirty lines
        mock_git.side_effect = ["true", "M file.txt\n?? untracked.py"]
        with pytest.raises(DirtyWorkingTreeError) as exc:
            session.prepare()
        assert "uncommitted or untracked changes" in str(exc.value)
        assert "file.txt" in str(exc.value)


def test_session_prepare_success():
    """Session preparation creates ephemeral worktree on dedicated task branch and captures base commit."""
    session = WorkspaceSession(task_id="test2", repo_root=Path("C:/mock/repo"))

    with patch.object(session, "_run_git") as mock_git, \
         patch.object(Path, "exists", return_value=False), \
         patch.object(Path, "mkdir"):
        # is_git, status (clean), rev-parse HEAD, branch --show-current, branch -D, worktree add
        mock_git.side_effect = ["true", "", "mock_base_hash", "main", "", ""]
        session.prepare()

        assert session.state == WorkspaceState.PREPARED
        assert session.base_commit == "mock_base_hash"
        assert session.base_branch == "main"
        mock_git.assert_any_call(["worktree", "add", "-b", "fusion/task-test2", str(session.worktree_dir), "HEAD"])


def test_session_teardown_cleans_worktree_and_branch():
    """Teardown removes worktree, prunes, and deletes task branch."""
    session = WorkspaceSession(task_id="test3", repo_root=Path("C:/mock/repo"))
    session.state = WorkspaceState.ACTIVE

    with patch.object(session, "_run_git") as mock_git, \
         patch.object(Path, "exists", return_value=True), \
         patch("shutil.rmtree"):
        session.teardown(delete_branch=True)

        assert session.state == WorkspaceState.DISCARDED
        mock_git.assert_any_call(["worktree", "remove", "--force", str(session.worktree_dir)], check=False)
        mock_git.assert_any_call(["worktree", "prune"], check=False)
        mock_git.assert_any_call(["branch", "-D", "fusion/task-test3"], check=False)


def test_session_context_manager_aborts_on_exception():
    """Context manager tears down and discards if an exception occurs."""
    session = WorkspaceSession(task_id="test4", repo_root=Path("C:/mock/repo"))

    with patch.object(session, "prepare"), \
         patch.object(session, "teardown") as mock_teardown:
        try:
            with session:
                raise ValueError("Simulated task error")
        except ValueError:
            pass
        mock_teardown.assert_called_once_with(delete_branch=True)
