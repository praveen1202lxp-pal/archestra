import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from fusion_agent.workspace.broker import ExecutionBroker
from fusion_agent.workspace.session import WorkspaceSession, WorkspaceState


@dataclass
class VerificationResult:
    """Outcome of running verification tests inside an isolated worktree."""
    passed: bool
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    command: str


class WorkspaceVerifier:
    """Executes test suites and extracts unified diffs within an isolated worktree."""

    @staticmethod
    def detect_trusted_test_command(repo_root: Path) -> Optional[str]:
        """Check for a trusted, project-local virtualenv test runner.
        
        Arbitrary repository scripts (e.g. npm test, tox, make) are NOT blindly
        executed without explicit user configuration.
        """
        # 1. Local virtual environment pytest on Windows
        venv_pytest_win = repo_root / ".venv" / "Scripts" / "pytest.exe"
        if venv_pytest_win.is_file():
            return str(venv_pytest_win.resolve())

        # 2. Local virtual environment pytest on POSIX
        venv_pytest_posix = repo_root / ".venv" / "bin" / "pytest"
        if venv_pytest_posix.is_file():
            return str(venv_pytest_posix.resolve())

        return None

    def run_tests(
        self,
        session: WorkspaceSession,
        test_command: Optional[str] = None,
        timeout: float = 90.0,
        broker: Optional[ExecutionBroker] = None,
    ) -> VerificationResult:
        """Execute verification suite strictly inside the worktree with sanitized environment."""
        session.state = WorkspaceState.VERIFYING

        # Resolve command with strict V1 security policy
        cmd = test_command or self.detect_trusted_test_command(session.repo_root)

        # Resolve bare 'pytest' to active python -m pytest
        if isinstance(cmd, str):
            if cmd.strip() == "pytest":
                cmd = f'"{sys.executable}" -m pytest'
            elif cmd.startswith("pytest "):
                cmd = f'"{sys.executable}" -m pytest ' + cmd[len("pytest "):]
        if not cmd:
            return VerificationResult(
                passed=False,
                exit_code=-1,
                stdout="",
                stderr=(
                    "Security Policy: No trusted virtual environment test runner was detected (.venv), "
                    "and no explicit verification command was provided. "
                    "Fusion Agent V1 does not blindly execute unvetted repository scripts. "
                    "Please configure 'verification_command' in .fusion/config.json."
                ),
                duration_seconds=0.0,
                command="",
            )

        exec_broker = broker or ExecutionBroker(session)
        sanitized_env = exec_broker.build_sanitized_env()
        # Ensure current worktree root is in PYTHONPATH so local package imports succeed
        curr_pp = sanitized_env.get("PYTHONPATH", "")
        sanitized_env["PYTHONPATH"] = f".{os.pathsep}{curr_pp}" if curr_pp else "."

        start_time = time.perf_counter()
        try:
            # If command is a single executable path (like pytest.exe), pass as list
            proc = subprocess.run(
                cmd if isinstance(cmd, list) else [cmd] if Path(cmd).is_file() else cmd,
                cwd=str(session.worktree_dir),
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=isinstance(cmd, str) and not Path(cmd).is_file(),
                env=sanitized_env,
                encoding="utf-8",
                errors="replace",
            )
            duration = time.perf_counter() - start_time
            passed = proc.returncode == 0
            return VerificationResult(
                passed=passed,
                exit_code=proc.returncode,
                stdout=proc.stdout.strip(),
                stderr=proc.stderr.strip(),
                duration_seconds=duration,
                command=str(cmd),
            )
        except subprocess.TimeoutExpired as exc:
            duration = time.perf_counter() - start_time
            return VerificationResult(
                passed=False,
                exit_code=-1,
                stdout="",
                stderr=f"Test verification timed out after {timeout}s: {exc}",
                duration_seconds=duration,
                command=str(cmd),
            )

    def get_diff(self, session: WorkspaceSession, base_commit: Optional[str] = None) -> str:
        """Extract a clean unified diff of all modifications made in the worktree against base_commit or HEAD."""
        worktree_path = str(session.worktree_dir)
        # Mark all untracked files with intent-to-add so they appear in git diff
        subprocess.run(
            ["git", "add", "-N", "."],
            cwd=worktree_path,
            capture_output=True,
            check=False,
        )
        target = base_commit or getattr(session, "base_commit", "") or "HEAD"
        proc = subprocess.run(
            ["git", "diff", target],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return proc.stdout.strip()
