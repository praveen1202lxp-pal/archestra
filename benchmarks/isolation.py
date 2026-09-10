"""Disposable Git Repository Isolation Manager for Benchmark Tasks.

Guarantees that each benchmark run operates on a clean, isolated snapshot
with a deterministic fresh initial commit. Prevents future commits, tags,
or solution branches from being discoverable by agents via git log --all.
"""

import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

from benchmarks.tasks.catalog import setup_task_fixtures


def _on_rm_error(func, path, exc_info):
    """Error handler for shutil.rmtree on Windows read-only git pack files."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass


class DisposableBenchmarkEnvironment:
    """Manages creation, git operations, diff capture, and teardown of isolated benchmark repos."""

    def __init__(self, task_id: str, run_id: str, base_temp_dir: Optional[Path] = None):
        self.task_id = task_id
        self.run_id = run_id
        self.base_temp_dir = base_temp_dir
        self.temp_dir: Optional[tempfile.TemporaryDirectory] = None
        self.repo_path: Optional[Path] = None
        self.baseline_commit_hash: str = ""

    def __enter__(self) -> "DisposableBenchmarkEnvironment":
        self.create()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()

    def create(self) -> Path:
        """Create a clean, isolated Git repository with a fresh deterministic initial commit."""
        self.temp_dir = tempfile.TemporaryDirectory(
            prefix=f"bench_{self.task_id}_{self.run_id}_",
            dir=str(self.base_temp_dir) if self.base_temp_dir else None,
        )
        self.repo_path = Path(self.temp_dir.name).resolve()

        # 1. Initialize fresh standalone Git repository (zero remote tracking, no parent git history)
        subprocess.run(
            ["git", "init", "-b", "master"],
            cwd=str(self.repo_path),
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "benchmark@archestra.eval"],
            cwd=str(self.repo_path),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Benchmark Isolation Runner"],
            cwd=str(self.repo_path),
            check=True,
            capture_output=True,
        )

        # 2. Materialize SUT-visible baseline files
        setup_task_fixtures(self.task_id, self.repo_path)

        # 3. Create initial baseline commit
        subprocess.run(
            ["git", "add", "."],
            cwd=str(self.repo_path),
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", f"baseline: initial snapshot for {self.task_id}"],
            cwd=str(self.repo_path),
            check=True,
            capture_output=True,
        )

        # 4. Record baseline commit hash
        rev = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(self.repo_path),
            check=True,
            capture_output=True,
            text=True,
        )
        self.baseline_commit_hash = rev.stdout.strip()
        return self.repo_path

    def get_touched_files(self) -> List[str]:
        """Return list of modified, added, or deleted relative file paths."""
        if not self.repo_path or not self.repo_path.exists():
            return []

        # Stage untracked files intent so diff/status catches them
        subprocess.run(
            ["git", "add", "-N", "."],
            cwd=str(self.repo_path),
            capture_output=True,
        )

        status = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=str(self.repo_path),
            capture_output=True,
            text=True,
        )
        files = [f.strip().replace("\\", "/") for f in status.stdout.splitlines() if f.strip()]
        return sorted(list(set(files)))

    def capture_diff(self) -> str:
        """Capture unified git diff against baseline commit."""
        if not self.repo_path or not self.repo_path.exists():
            return ""

        subprocess.run(
            ["git", "add", "-N", "."],
            cwd=str(self.repo_path),
            capture_output=True,
        )

        diff = subprocess.run(
            ["git", "diff", "HEAD"],
            cwd=str(self.repo_path),
            capture_output=True,
            text=True,
        )
        return diff.stdout

    def cleanup(self) -> None:
        """Safely tear down the disposable temporary repository."""
        if self.temp_dir:
            try:
                self.temp_dir.cleanup()
            except Exception:
                if self.repo_path and self.repo_path.exists():
                    shutil.rmtree(str(self.repo_path), onerror=_on_rm_error)
            self.temp_dir = None
            self.repo_path = None
