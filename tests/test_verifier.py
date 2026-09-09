"""Unit tests for WorkspaceVerifier and test execution."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from fusion_agent.workspace.session import WorkspaceSession, WorkspaceState
from fusion_agent.workspace.verifier import WorkspaceVerifier


def test_verifier_detect_trusted_command(tmp_path):
    """Detects trusted venv pytest when available."""
    repo = tmp_path / "repo"
    repo.mkdir()
    scripts = repo / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    pytest_bin = scripts / "pytest.exe"
    pytest_bin.write_text("binary")

    detected = WorkspaceVerifier.detect_trusted_test_command(repo)
    assert detected is not None
    assert "pytest.exe" in detected


def test_verifier_refuses_arbitrary_unvetted_scripts(tmp_path):
    """Refuses to blindly execute arbitrary scripts when no trusted runner is configured."""
    repo = tmp_path / "repo"
    repo.mkdir()
    session = WorkspaceSession(task_id="t_untrusted", repo_root=repo, custom_worktree_dir=tmp_path / "wt")
    verifier = WorkspaceVerifier()

    res = verifier.run_tests(session, test_command=None)
    assert res.passed is False
    assert res.exit_code == -1
    assert "Security Policy" in res.stderr
    assert "verification_command" in res.stderr


def test_verifier_run_tests_success(tmp_path):
    """Runs explicit test command inside worktree with allowlist environment and parses success."""
    session = WorkspaceSession(task_id="t1", repo_root=tmp_path, custom_worktree_dir=tmp_path / "wt")
    verifier = WorkspaceVerifier()

    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = "25 passed in 0.5s"
    mock_proc.stderr = ""

    with patch.dict(os.environ, {"PATH": "C:/tools" if os.name == "nt" else "/usr/bin", "SECRET_TOKEN": "leak"}), \
         patch("subprocess.run", return_value=mock_proc) as mock_run:
        res = verifier.run_tests(session, test_command="pytest")

        assert res.passed is True
        assert res.exit_code == 0
        assert "25 passed" in res.stdout
        assert session.state == WorkspaceState.VERIFYING
        call_kwargs = mock_run.call_args[1]
        assert "SECRET_TOKEN" not in call_kwargs["env"]
        assert "PATH" in call_kwargs["env"]


def test_verifier_run_tests_failure(tmp_path):
    """Runs test command inside worktree and captures failures."""
    session = WorkspaceSession(task_id="t2", repo_root=tmp_path, custom_worktree_dir=tmp_path / "wt")
    verifier = WorkspaceVerifier()

    mock_proc = MagicMock()
    mock_proc.returncode = 1
    mock_proc.stdout = "FAILED test_auth.py"
    mock_proc.stderr = "AssertionError"

    with patch("subprocess.run", return_value=mock_proc):
        res = verifier.run_tests(session, test_command="pytest")

        assert res.passed is False
        assert res.exit_code == 1
        assert "FAILED" in res.stdout


def test_verifier_get_diff(tmp_path):
    """Generates unified git diff including newly added files."""
    session = WorkspaceSession(task_id="t3", repo_root=tmp_path, custom_worktree_dir=tmp_path / "wt")
    verifier = WorkspaceVerifier()

    mock_diff = MagicMock()
    mock_diff.returncode = 0
    mock_diff.stdout = "diff --git a/foo.py b/foo.py\n+new line"

    with patch("subprocess.run", return_value=mock_diff):
        diff = verifier.get_diff(session)
        assert "+new line" in diff
