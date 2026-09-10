"""Disposable Git Repository Isolation Manager for Benchmark Tasks.

Guarantees that each benchmark run operates on a clean, isolated snapshot
with a deterministic fresh initial commit. Prevents future commits, tags,
or solution branches from being discoverable by agents via git log --all.
"""

import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import List, Optional, Tuple

from benchmarks.tasks.catalog import setup_task_fixtures


def compute_baseline_snapshot_hash(repo_path: Path) -> str:
    """Compute deterministic full SHA-256 digest of all files in the baseline repo snapshot."""
    file_records = []
    for p in sorted(repo_path.rglob("*")):
        if p.is_file() and ".git" not in p.parts:
            rel = p.relative_to(repo_path).as_posix()
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            file_records.append(f"{rel}:{digest}")
    payload = "\n".join(file_records).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def compute_sut_tree_hash(repo_path: Path) -> str:
    """Compute deterministic full SHA-256 digest of SUT working tree excluding .git and transient artifacts."""
    from benchmarks.evaluators import ArtifactPolicy

    file_records = []
    for p in sorted(repo_path.rglob("*")):
        if p.is_file() and ".git" not in p.parts:
            rel = p.relative_to(repo_path).as_posix()
            if ArtifactPolicy.is_ignored(rel):
                continue
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            file_records.append(f"{rel}:{digest}")
    payload = "\n".join(file_records).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


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
        self.baseline_snapshot_hash: str = ""

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
        self.baseline_snapshot_hash = compute_baseline_snapshot_hash(self.repo_path)
        return self.repo_path

    def freeze_sut_candidate_state(self) -> Tuple[List[str], str, str]:
        """Deterministically freeze SUT candidate state before hidden evaluation using a temporary Git index.

        Captures unstaged working-tree modifications, deletions, and allowed untracked files
        via a temporary GIT_INDEX_FILE, completely preserving the real SUT Git index (.git/index).

        Returns:
            (sut_touched_files, sut_git_diff, sut_candidate_tree_hash)
        """
        if not self.repo_path or not self.repo_path.exists():
            return [], "", ""

        from benchmarks.evaluators import ArtifactPolicy

        temp_index = self.repo_path / ".git" / f"tmp_idx_{uuid.uuid4().hex}"
        env = {**os.environ, "GIT_INDEX_FILE": str(temp_index)}

        try:
            # 1. Initialize temporary index from baseline commit HEAD
            subprocess.run(
                ["git", "read-tree", "HEAD"],
                cwd=str(self.repo_path),
                env=env,
                check=True,
                capture_output=True,
            )

            # 2. Stage all working tree modifications, deletions, and untracked candidate files
            subprocess.run(
                ["git", "add", "-A", "."],
                cwd=str(self.repo_path),
                env=env,
                check=True,
                capture_output=True,
            )

            # 3. Purge any transient runtime artifacts per ArtifactPolicy
            staged_proc = subprocess.run(
                ["git", "diff", "--cached", "--name-only", "HEAD"],
                cwd=str(self.repo_path),
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            staged_files = [f.strip().replace("\\", "/") for f in staged_proc.stdout.splitlines() if f.strip()]
            ignored = [f for f in staged_files if ArtifactPolicy.is_ignored(f)]
            if ignored:
                subprocess.run(
                    ["git", "rm", "--cached", "-f", "--", *ignored],
                    cwd=str(self.repo_path),
                    env=env,
                    check=True,
                    capture_output=True,
                )

            # 4. Extract touched files against baseline HEAD
            touched_proc = subprocess.run(
                ["git", "diff", "--cached", "--name-only", "HEAD"],
                cwd=str(self.repo_path),
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            sut_touched_files = sorted(list({
                f.strip().replace("\\", "/")
                for f in touched_proc.stdout.splitlines()
                if f.strip() and not ArtifactPolicy.is_ignored(f.strip())
            }))

            # 5. Extract unified diff against baseline HEAD
            diff_proc = subprocess.run(
                ["git", "diff", "--cached", "HEAD"],
                cwd=str(self.repo_path),
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            sut_git_diff = diff_proc.stdout

            # 6. Write tree from the temporary index to get the canonical Git tree object hash
            tree_proc = subprocess.run(
                ["git", "write-tree"],
                cwd=str(self.repo_path),
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            sut_candidate_tree_hash = tree_proc.stdout.strip()

            return sut_touched_files, sut_git_diff, sut_candidate_tree_hash

        finally:
            if temp_index.exists():
                try:
                    temp_index.unlink()
                except Exception:
                    pass

    def get_touched_files(self) -> List[str]:
        """Return list of modified, added, or deleted relative file paths from candidate state."""
        touched, _, _ = self.freeze_sut_candidate_state()
        return touched

    def capture_diff(self) -> str:
        """Capture unified git diff against baseline commit from candidate state."""
        _, diff, _ = self.freeze_sut_candidate_state()
        return diff

    def freeze_sut_state(self) -> Tuple[List[str], str, str]:
        """Freeze and capture SUT candidate state (backward-compatible alias).

        Returns:
            (files_touched, git_diff, sut_tree_hash)
        """
        return self.freeze_sut_candidate_state()

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
