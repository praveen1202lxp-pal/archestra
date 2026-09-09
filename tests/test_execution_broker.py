"""Unit tests for ExecutionBroker path containment and process sandboxing."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from fusion_agent.workspace.broker import ExecutionBroker
from fusion_agent.workspace.session import WorkspaceSession, WorkspaceState


@pytest.fixture
def mock_session(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    worktree = tmp_path / "worktrees" / "task-123"
    worktree.mkdir(parents=True)
    session = WorkspaceSession(task_id="123", repo_root=repo, custom_worktree_dir=worktree)
    session.state = WorkspaceState.PREPARED
    return session


def test_broker_resolve_valid_path(mock_session):
    """Resolves normal relative paths to canonical paths within worktree."""
    broker = ExecutionBroker(mock_session)
    resolved = broker.resolve_path("src/main.py")
    expected = (mock_session.worktree_dir / "src" / "main.py").resolve()
    assert str(resolved).lower() == str(expected).lower()


def test_broker_block_path_traversal_outside(mock_session):
    """Rejects directory traversal attempts escaping the worktree."""
    broker = ExecutionBroker(mock_session)
    with pytest.raises(PermissionError) as exc:
        broker.resolve_path("../../secrets.env")
    assert "escapes the isolated worktree" in str(exc.value)


def test_broker_block_absolute_drive_path_outside(mock_session):
    """Rejects absolute paths outside the worktree."""
    broker = ExecutionBroker(mock_session)
    outside = "C:/Windows/System32/cmd.exe" if os.name == "nt" else "/etc/passwd"
    with pytest.raises(PermissionError) as exc:
        broker.resolve_path(outside)
    assert "escapes" in str(exc.value) or "Access denied" in str(exc.value)


def test_broker_drive_letter_case_insensitivity(mock_session):
    """Handles drive-letter case differences gracefully without false escapes on Windows."""
    broker = ExecutionBroker(mock_session)
    wt_str = str(mock_session.worktree_dir)
    if os.name == "nt" and ":" in wt_str:
        # Invert case of drive letter
        drive = wt_str[0]
        flipped_drive = drive.lower() if drive.isupper() else drive.upper()
        flipped_path = flipped_drive + wt_str[1:] + "/file.txt"
        resolved = broker.resolve_path(flipped_path)
        assert str(resolved).lower() == (mock_session.worktree_dir / "file.txt").resolve().__str__().lower()


def test_broker_block_unc_path(mock_session):
    """Rejects UNC network paths (e.g. \\\\server\\share\\evil.txt)."""
    broker = ExecutionBroker(mock_session)
    with pytest.raises(PermissionError) as exc:
        broker.resolve_path(r"\\192.168.1.1\share\payload.ps1")
    assert "UNC paths are prohibited" in str(exc.value)

    with pytest.raises(PermissionError) as exc:
        broker.resolve_path("//remote-server/share/payload.sh")
    assert "UNC paths are prohibited" in str(exc.value)


def test_broker_block_device_namespace_path(mock_session):
    """Rejects Windows device namespace paths (\\\\.\\ or \\\\?\\)."""
    broker = ExecutionBroker(mock_session)
    with pytest.raises(PermissionError) as exc:
        broker.resolve_path(r"\\.\pipe\docker_engine")
    assert "Windows device namespace paths are prohibited" in str(exc.value)

    with pytest.raises(PermissionError) as exc:
        broker.resolve_path(r"\\?\C:\escaped.txt")
    assert "Windows device namespace paths are prohibited" in str(exc.value)


def test_broker_block_dos_reserved_device_names(mock_session):
    """Rejects DOS reserved device names (CON, PRN, AUX, NUL, COM1-9, LPT1-9)."""
    broker = ExecutionBroker(mock_session)
    reserved_names = ["NUL", "CON", "PRN", "AUX", "COM1", "LPT1", "nul.txt", "con.json"]
    for name in reserved_names:
        with pytest.raises(PermissionError) as exc:
            broker.resolve_path(name)
        assert "reserved device name" in str(exc.value)


def test_broker_block_git_metadata_and_linked_worktree(mock_session):
    """Rejects direct access to .git metadata or linked worktree references."""
    broker = ExecutionBroker(mock_session)
    # 1. Directory reference
    with pytest.raises(PermissionError) as exc:
        broker.resolve_path(".git/config")
    assert ".git repository metadata" in str(exc.value)

    # 2. Direct .git file reference (worktrees have a .git file)
    with pytest.raises(PermissionError) as exc:
        broker.resolve_path(".git")
    assert ".git repository metadata" in str(exc.value)

    # 3. Subdir path component matching .git
    with pytest.raises(PermissionError) as exc:
        broker.resolve_path("src/.git/hooks")
    assert ".git repository metadata" in str(exc.value)


def test_broker_block_symlink_pointing_outside(mock_session, tmp_path):
    """Resolves symlinks to real targets and rejects if pointing outside worktree."""
    broker = ExecutionBroker(mock_session)
    outside_file = tmp_path / "outside_secret.txt"
    outside_file.write_text("secret")

    link_path = mock_session.worktree_dir / "evil_symlink.txt"
    try:
        os.symlink(str(outside_file), str(link_path))
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation not supported without admin privileges on this environment.")

    with pytest.raises(PermissionError) as exc:
        broker.resolve_path("evil_symlink.txt")
    assert "escapes the isolated worktree" in str(exc.value)


def test_broker_file_crud_operations(mock_session):
    """Performs write, read, edit, and exists within worktree boundary."""
    broker = ExecutionBroker(mock_session)

    # 1. Write file
    broker.write_file("app/config.json", '{"debug": false}')
    assert broker.file_exists("app/config.json")
    assert mock_session.state == WorkspaceState.ACTIVE

    # 2. Read file
    content = broker.read_file("app/config.json")
    assert content == '{"debug": false}'

    # 3. Edit file
    broker.edit_file("app/config.json", '"debug": false', '"debug": true')
    assert broker.read_file("app/config.json") == '{"debug": true}'

    # 4. List dir
    entries = broker.list_dir("app")
    assert "app/config.json" in entries


def test_broker_run_command_allowlist_environment(mock_session):
    """Executes subprocess with allowlist-based environment, stripping all unapproved vars."""
    broker = ExecutionBroker(mock_session)

    test_env = {
        "PATH": "C:/tools" if os.name == "nt" else "/usr/bin",
        "SYSTEMROOT": "C:/Windows",
        "OPENAI_API_KEY": "sk-secret-12345",
        "GITHUB_TOKEN": "ghp_secret_67890",
        "CUSTOM_SECRET_AUTH": "leaked_auth",
        "UNAPPROVED_ARBITRARY_VAR": "should_be_excluded",
    }

    with patch.dict(os.environ, test_env, clear=True), \
         patch("subprocess.run") as mock_run:
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "output"
        mock_proc.stderr = ""
        mock_run.return_value = mock_proc

        code, out, err = broker.run_command(["echo", "hi"], timeout=10.0)

        assert code == 0
        assert out == "output"
        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["cwd"] == str(mock_session.worktree_dir)
        env = call_kwargs["env"]

        # Allowlisted system vars are preserved
        assert env.get("PATH") == test_env["PATH"]

        # Secrets and unapproved vars are completely absent
        assert "OPENAI_API_KEY" not in env
        assert "GITHUB_TOKEN" not in env
        assert "CUSTOM_SECRET_AUTH" not in env
        assert "UNAPPROVED_ARBITRARY_VAR" not in env


def test_broker_run_command_rejects_sensitive_extra_env(mock_session):
    """Rejects caller-supplied extra_env containing sensitive key/token terms."""
    broker = ExecutionBroker(mock_session)

    with pytest.raises(PermissionError) as exc:
        broker.run_command(["echo", "hi"], extra_env={"MY_SECRET_TOKEN": "12345"})
    assert "rejected as potentially sensitive" in str(exc.value)
