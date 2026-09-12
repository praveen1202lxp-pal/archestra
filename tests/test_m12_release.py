"""Tests for Milestone 12 Release Readiness and Developer Experience."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import fusion_agent
from fusion_agent.cli.doctor import CheckStatus, run_doctor
from fusion_agent.cli.errors import format_cli_error
from fusion_agent.cli.main import cmd_config, cmd_init, cmd_providers, cmd_status
from fusion_agent.config.loader import ConfigLoader
from fusion_agent.config.schema import AgentConfig, FusionConfig, OptimizationMode
from fusion_agent.workspace.session import DirtyWorkingTreeError


def test_canonical_version():
    """Verify single source of truth for canonical version."""
    assert fusion_agent.__version__ == "0.12.0"

    pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
    assert pyproject_path.exists()
    content = pyproject_path.read_text(encoding="utf-8")
    assert 'version = "0.12.0"' in content


def test_cli_version_flag():
    """Verify fusion --version flag invocation."""
    res = subprocess.run(
        [sys.executable, "-m", "fusion_agent.cli.main", "--version"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    assert "Fusion Agent v0.12.0" in res.stdout


def test_cli_help_flag():
    """Verify fusion --help lists all required commands."""
    res = subprocess.run(
        [sys.executable, "-m", "fusion_agent.cli.main", "--help"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    for cmd in ["init", "doctor", "providers", "status", "config", "run", "resume"]:
        assert cmd in res.stdout


def test_doctor_diagnostics(tmp_path: Path):
    """Verify doctor checks execute non-destructively in a clean workspace."""
    # Initialize a temporary git repository
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "TestUser"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(tmp_path), check=True)

    # Initialize fusion in tmp_path
    args_init = argparse.Namespace(dir=str(tmp_path), name="DoctorTest", force=False)
    assert cmd_init(args_init) == 0

    report = run_doctor(project_dir=tmp_path)
    assert report.passed_count >= 5
    assert report.fail_count == 0
    check_names = [c.name for c in report.checks]
    assert "Fusion Version" in check_names
    assert "Python Runtime" in check_names
    assert "Git Tooling" in check_names
    assert "Storage & SQLite" in check_names
    assert "Configuration" in check_names


def test_config_hierarchical_precedence(tmp_path: Path, monkeypatch):
    """Verify strict precedence: CLI > project > user > env > defaults."""
    empty_dir = tmp_path / "empty_proj"
    empty_dir.mkdir()

    # 1. Base defaults
    loaded_default = ConfigLoader.load_hierarchical(project_dir=empty_dir)
    assert loaded_default.optimization_mode == OptimizationMode.BALANCED

    # 2. Environment variable overrides defaults
    monkeypatch.setenv("FUSION_OPTIMIZATION_MODE", "LOWEST_COST")
    loaded_env = ConfigLoader.load_hierarchical(project_dir=empty_dir)
    assert loaded_env.optimization_mode == OptimizationMode.LOWEST_COST

    # 3. User config overrides environment variable
    user_fusion = tmp_path / "user_home" / ".fusion"
    user_fusion.mkdir(parents=True)
    user_cfg = {
        "optimization_mode": "FASTEST",
        "log_level": "DEBUG",
    }
    user_cfg_file = user_fusion / "config.json"
    user_cfg_file.write_text(json.dumps(user_cfg), encoding="utf-8")
    monkeypatch.setattr(ConfigLoader, "get_user_config_path", lambda: user_cfg_file)

    loaded_user = ConfigLoader.load_hierarchical(project_dir=empty_dir)
    assert loaded_user.optimization_mode == OptimizationMode.FASTEST
    assert loaded_user.log_level == "DEBUG"

    # 4. Project config overrides user config & environment variables
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    fusion_dir = proj_dir / ".fusion"
    fusion_dir.mkdir()
    proj_cfg = {
        "project_name": "ProjectLevel",
        "optimization_mode": "BEST_QUALITY",
        "log_level": "WARNING",
    }
    (fusion_dir / "config.json").write_text(json.dumps(proj_cfg), encoding="utf-8")

    loaded_proj = ConfigLoader.load_hierarchical(project_dir=proj_dir)
    assert loaded_proj.project_name == "ProjectLevel"
    assert loaded_proj.optimization_mode == OptimizationMode.BEST_QUALITY
    assert loaded_proj.log_level == "WARNING"

    # 5. CLI overrides take highest precedence over project config
    cli_overrides = {"optimization_mode": "LOCAL_PRIVATE", "log_level": "ERROR"}
    loaded_cli = ConfigLoader.load_hierarchical(project_dir=proj_dir, cli_overrides=cli_overrides)
    assert loaded_cli.optimization_mode == OptimizationMode.LOCAL_PRIVATE
    assert loaded_cli.log_level == "ERROR"


def test_config_masking():
    """Verify sensitive fields are safely masked when exporting or displaying config."""
    cfg = FusionConfig(
        project_name="SecretProject",
        agents={
            "secure_agent": AgentConfig(
                provider_name="Secret Agent",
                provider_type="mock",
                env_api_key="AIzaSyA123456789SecretToken",
                extra_params={"auth_token": "sk-secret-password-12345", "timeout": 60},
            )
        },
    )

    safe = cfg.to_safe_dict()
    agent_data = safe["agents"]["secure_agent"]
    assert agent_data["env_api_key"] == "[REDACTED]"
    assert agent_data["extra_params"]["auth_token"] == "[REDACTED]"
    assert agent_data["extra_params"]["timeout"] == 60


def test_init_creates_state_and_updates_gitignore(tmp_path: Path):
    """Verify fusion init creates .fusion/, selective .gitignore entries, and keeps config.json trackable."""
    # Initialize a real git repository
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "TestUser"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(tmp_path), check=True)

    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("*.pyc\n__pycache__/\n", encoding="utf-8")

    args = argparse.Namespace(dir=str(tmp_path), name="InitTest", force=False)
    rc = cmd_init(args)
    assert rc == 0

    assert (tmp_path / ".fusion" / "config.json").exists()
    assert (tmp_path / ".fusion" / "fusion.db").exists()

    # Check gitignore was updated with selective runtime rules
    gi_content = gitignore.read_text(encoding="utf-8")
    assert ".fusion/*.db" in gi_content
    assert ".fusion/*.log" in gi_content
    assert ".fusion/logs/" in gi_content
    assert ".fusion/worktrees/" in gi_content
    assert ".fusion/locks/" in gi_content
    # The entire .fusion/ directory should NOT be ignored
    assert "\n.fusion/\n" not in gi_content

    # Verify Git ignore status via git check-ignore
    # 1. .fusion/config.json MUST NOT be ignored (exit code != 0)
    res_cfg = subprocess.run(
        ["git", "check-ignore", ".fusion/config.json"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    assert res_cfg.returncode != 0, ".fusion/config.json should NOT be ignored by git"

    # 2. Runtime DB, WAL, SHM MUST be ignored
    for runtime_file in [
        ".fusion/fusion.db",
        ".fusion/fusion.db-wal",
        ".fusion/fusion.db-shm",
        ".fusion/test.log",
        ".fusion/logs/run.log",
        ".fusion/worktrees/task-1",
        ".fusion/locks/task.lock",
        ".fusion/temp/scratch",
        ".fusion/cache/data",
    ]:
        res_ign = subprocess.run(
            ["git", "check-ignore", runtime_file],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
        )
        assert res_ign.returncode == 0, f"{runtime_file} should be ignored by git"

    # 3. Verify git status tracks config.json but ignores DB
    status_res = subprocess.run(
        ["git", "status", "--porcelain", "-u"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    # config.json and .gitignore should appear in untracked/modified
    assert ".fusion/config.json" in status_res.stdout
    assert "fusion.db" not in status_res.stdout

    # Running init again without force should be idempotent
    rc2 = cmd_init(args)
    assert rc2 == 0


def test_init_migrates_legacy_whole_directory_gitignore(tmp_path: Path):
    """Verify fusion init updates legacy whole-directory .fusion/ entries so config.json is trackable."""
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True, check=True)
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("# Old ignore\n.fusion/\n*.tmp\n", encoding="utf-8")

    args = argparse.Namespace(dir=str(tmp_path), name="MigrateTest", force=False)
    rc = cmd_init(args)
    assert rc == 0

    gi_content = gitignore.read_text(encoding="utf-8")
    assert "\n.fusion/\n" not in gi_content
    assert ".fusion/*.db" in gi_content

    # Verify config.json is not ignored after migration
    res_cfg = subprocess.run(
        ["git", "check-ignore", ".fusion/config.json"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
    )
    assert res_cfg.returncode != 0


def test_error_formatting_without_tracebacks():
    """Verify expected operational failures format cleanly without raw stack traces."""
    # 1. DirtyWorkingTreeError
    err1 = DirtyWorkingTreeError("Uncommitted changes: src/main.py")
    formatted1 = format_cli_error(err1, debug=False)
    assert "[!] Fusion refused to start: uncommitted changes detected" in formatted1
    assert "git stash" in formatted1
    assert "Traceback" not in formatted1

    # With debug=True, technical traceback is included
    formatted1_debug = format_cli_error(err1, debug=True)
    assert "TECHNICAL TRACEBACK (DEBUG)" in formatted1_debug

    # 2. TimeoutError
    err2 = TimeoutError("Provider timed out after 180.0s")
    formatted2 = format_cli_error(err2, debug=False)
    assert "[!] Task execution timed out" in formatted2
    assert "No unvetted changes were promoted" in formatted2
    assert "Traceback" not in formatted2

    # 3. Missing CLI provider
    err3 = FileNotFoundError("Codex CLI executable 'codex' was not found.")
    formatted3 = format_cli_error(err3, debug=False)
    assert "[!] Required provider CLI not found" in formatted3
    assert "Run 'fusion doctor'" in formatted3


def test_cmd_config_and_providers(tmp_path: Path, capsys):
    """Verify cmd_config and cmd_providers outputs."""
    args_init = argparse.Namespace(dir=str(tmp_path), name="ConfigCliTest", force=False)
    cmd_init(args_init)

    # Test config --validate
    args_cfg_val = argparse.Namespace(dir=str(tmp_path), validate=True, path=False, get=None, debug=False)
    assert cmd_config(args_cfg_val) == 0
    out_val = capsys.readouterr().out
    assert "Configuration is valid" in out_val

    # Test config (display safe dict)
    args_cfg_disp = argparse.Namespace(dir=str(tmp_path), validate=False, path=False, get=None, debug=False)
    assert cmd_config(args_cfg_disp) == 0
    out_disp = capsys.readouterr().out
    assert "Active Fusion Configuration" in out_disp
    assert "ConfigCliTest" in out_disp

    # Test providers command
    args_prov = argparse.Namespace(dir=str(tmp_path))
    cmd_providers(args_prov)
    out_prov = capsys.readouterr().out
    assert "Configured Providers for ConfigCliTest" in out_prov
