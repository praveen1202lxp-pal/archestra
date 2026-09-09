"""Unit tests for PromotionEngine."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fusion_agent.workspace.promotion import PromotionEngine, PromotionResult
from fusion_agent.workspace.session import WorkspaceSession, WorkspaceState


def test_promotion_clean_merge(tmp_path):
    """Commits in worktree and merges into target repository branch when clean and unconverged."""
    session = WorkspaceSession(task_id="prom1", repo_root=tmp_path, custom_worktree_dir=tmp_path / "wt")
    session.base_commit = "base_commit_aaa"
    engine = PromotionEngine()

    mock_branch = MagicMock(returncode=0, stdout="master\n", stderr="")
    mock_rev_target = MagicMock(returncode=0, stdout="base_commit_aaa\n", stderr="")  # matches base_commit
    mock_repo_status = MagicMock(returncode=0, stdout="", stderr="")                  # main repo is clean
    mock_add = MagicMock(returncode=0, stdout="", stderr="")
    mock_wt_status = MagicMock(returncode=0, stdout="M src/file.py\n", stderr="")
    mock_commit = MagicMock(returncode=0, stdout="[fusion/task-prom1 abc] commit\n", stderr="")
    mock_merge = MagicMock(returncode=0, stdout="Fast-forward\n", stderr="")
    mock_rev_prom = MagicMock(returncode=0, stdout="merged_hash_456\n", stderr="")

    with patch.object(engine, "_run_git") as mock_git, \
         patch.object(session, "teardown") as mock_teardown:
        mock_git.side_effect = [
            mock_branch,
            mock_rev_target,
            mock_repo_status,
            mock_add,
            mock_wt_status,
            mock_commit,
            mock_merge,
            mock_rev_prom,
        ]

        result = engine.promote(session)

        assert result.success is True
        assert result.commit_hash == "merged_hash_456"
        assert session.state == WorkspaceState.PROMOTED
        mock_teardown.assert_called_once_with(delete_branch=True)


def test_promotion_concurrency_violation_fails_safely(tmp_path):
    """Refuses promotion if target branch HEAD moved since session started."""
    session = WorkspaceSession(task_id="prom_race", repo_root=tmp_path, custom_worktree_dir=tmp_path / "wt")
    session.base_commit = "base_commit_aaa"
    engine = PromotionEngine()

    mock_branch = MagicMock(returncode=0, stdout="master\n", stderr="")
    mock_rev_target = MagicMock(returncode=0, stdout="concurrent_commit_bbb\n", stderr="")  # diverged!

    with patch.object(engine, "_run_git") as mock_git:
        mock_git.side_effect = [
            mock_branch,
            mock_rev_target,
        ]

        result = engine.promote(session)

        assert result.success is False
        assert "concurrency violation" in result.message
        assert session.state != WorkspaceState.PROMOTED


def test_promotion_dirty_main_repo_fails_safely(tmp_path):
    """Refuses promotion if main working tree became dirty during task execution."""
    session = WorkspaceSession(task_id="prom_dirty", repo_root=tmp_path, custom_worktree_dir=tmp_path / "wt")
    session.base_commit = "base_commit_aaa"
    engine = PromotionEngine()

    mock_branch = MagicMock(returncode=0, stdout="master\n", stderr="")
    mock_rev_target = MagicMock(returncode=0, stdout="base_commit_aaa\n", stderr="")
    mock_repo_status = MagicMock(returncode=0, stdout="M uncommitted_work.py\n", stderr="")

    with patch.object(engine, "_run_git") as mock_git:
        mock_git.side_effect = [
            mock_branch,
            mock_rev_target,
            mock_repo_status,
        ]

        result = engine.promote(session)

        assert result.success is False
        assert "uncommitted or untracked changes" in result.message
        assert session.state != WorkspaceState.PROMOTED


def test_promotion_conflict_fails_safely(tmp_path):
    """Fails cleanly and leaves session unpromoted if git merge conflicts."""
    session = WorkspaceSession(task_id="prom2", repo_root=tmp_path, custom_worktree_dir=tmp_path / "wt")
    session.base_commit = "base_commit_aaa"
    engine = PromotionEngine()

    mock_branch = MagicMock(returncode=0, stdout="master\n", stderr="")
    mock_rev_target = MagicMock(returncode=0, stdout="base_commit_aaa\n", stderr="")
    mock_repo_status = MagicMock(returncode=0, stdout="", stderr="")
    mock_add = MagicMock(returncode=0, stdout="", stderr="")
    mock_wt_status = MagicMock(returncode=0, stdout="M src/file.py\n", stderr="")
    mock_commit = MagicMock(returncode=0, stdout="[fusion/task-prom2 abc] commit\n", stderr="")
    mock_merge = MagicMock(returncode=1, stdout="", stderr="CONFLICT: merge conflict in src/file.py")

    with patch.object(engine, "_run_git") as mock_git:
        mock_git.side_effect = [
            mock_branch,
            mock_rev_target,
            mock_repo_status,
            mock_add,
            mock_wt_status,
            mock_commit,
            mock_merge,
        ]

        result = engine.promote(session)

        assert result.success is False
        assert "Merge conflict" in result.message
        assert session.state != WorkspaceState.PROMOTED


def test_promotion_discard_cleans_session(tmp_path):
    """Discard cleans and marks session discarded."""
    session = WorkspaceSession(task_id="prom3", repo_root=tmp_path, custom_worktree_dir=tmp_path / "wt")
    engine = PromotionEngine()

    with patch.object(session, "teardown") as mock_teardown:
        engine.discard(session)
        assert session.state == WorkspaceState.DISCARDED
        mock_teardown.assert_called_once_with(delete_branch=True)
