"""Tests for Fusion CLI commands."""

import argparse
from pathlib import Path

from fusion_agent.cli.main import cmd_init, cmd_run, cmd_status


def test_cli_init_and_status(tmp_path: Path, capsys):
    # Test init
    args_init = argparse.Namespace(
        dir=str(tmp_path),
        name="CliTestProject",
        goal="CLI Test Goal",
        force=False,
    )
    rc = cmd_init(args_init)
    assert rc == 0

    assert (tmp_path / ".fusion" / "config.json").exists()
    assert (tmp_path / ".fusion" / "fusion.db").exists()

    # Test status
    args_status = argparse.Namespace(dir=str(tmp_path))
    rc_status = cmd_status(args_status)
    assert rc_status == 0

    captured = capsys.readouterr().out
    assert "CliTestProject" in captured
    assert "Configured Providers:" in captured


def test_cli_run(tmp_path: Path, capsys):
    # Initialize first
    args_init = argparse.Namespace(
        dir=str(tmp_path),
        name="RunTestProject",
        goal="Testing Run",
        force=False,
    )
    cmd_init(args_init)

    # Run task with --debug
    args_run = argparse.Namespace(
        dir=str(tmp_path),
        task="Investigate memory corruption on shutdown",
        debug=True,
    )
    rc_run = cmd_run(args_run)
    assert rc_run == 0

    captured = capsys.readouterr().out
    assert "Fusion:" in captured
    assert "DELIBERATION INSPECTION (DEBUG)" in captured
