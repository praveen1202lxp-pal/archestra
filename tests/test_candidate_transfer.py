"""Deterministic tests for candidate provenance, substrate transfer, and telemetry semantics."""

import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from benchmarks.adapters.agy_adapter import _to_wsl_path
from benchmarks.adapters.codex_adapter import parse_codex_jsonl_events
from benchmarks.evaluators import BenchmarkEvaluator, ScopeOracle
from benchmarks.isolation import DisposableBenchmarkEnvironment
from benchmarks.schema import BenchmarkScore, BenchmarkTask
from benchmarks.tasks.catalog import get_task_by_id


def test_codex_host_workspace_candidate_transfer(tmp_path):
    """Standalone Codex host workspace: modified + newly created file appear in frozen candidate."""
    with DisposableBenchmarkEnvironment(task_id="TASK-01", run_id="test_host_xfer", base_temp_dir=tmp_path) as env:
        baseline_tree = env.baseline_tree_hash
        assert baseline_tree

        # 1. Modify existing file
        pag_file = env.repo_path / "src" / "pagination.py"
        assert pag_file.exists()
        original_content = pag_file.read_text()
        pag_file.write_text(original_content + "\n# Candidate modification\n")

        # 2. Create new file
        helper_file = env.repo_path / "src" / "helper.py"
        helper_file.write_text("def helper():\n    return 42\n")

        # 3. Freeze candidate state before any evaluation
        touched, diff, cand_tree = env.freeze_sut_candidate_state()

        assert "src/pagination.py" in touched
        assert "src/helper.py" in touched
        assert cand_tree != baseline_tree
        assert "+# Candidate modification" in diff
        assert "+def helper():" in diff


def test_antigravity_docker_workspace_candidate_transfer(tmp_path):
    """Standalone Antigravity Docker: container edits workspace -> edits present on host candidate capture."""
    # Check if WSL and docker are available
    has_docker = False
    try:
        r = subprocess.run(["wsl", "-u", "root", "docker", "info"], capture_output=True, timeout=10)
        has_docker = (r.returncode == 0)
    except Exception:
        has_docker = False

    if not has_docker:
        pytest.skip("WSL Docker not available for Docker substrate transfer test")

    with DisposableBenchmarkEnvironment(task_id="TASK-01", run_id="test_docker_xfer", base_temp_dir=tmp_path) as env:
        baseline_tree = env.baseline_tree_hash
        wsl_repo = _to_wsl_path(env.repo_path)

        # Run container that edits a file and creates a new file in /workspace
        cmd = [
            "wsl", "-u", "root", "docker", "run", "--rm",
            "--entrypoint", "sh",
            "-v", f"{wsl_repo}:/workspace:rw",
            "antigravity-benchmark:1.2.0",
            "-c",
            "echo '# docker edit' >> /workspace/src/pagination.py && echo 'docker_new = 1' > /workspace/src/docker_created.py",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        assert res.returncode == 0, f"Docker command failed: {res.stderr}"

        # Capture and freeze candidate state on host after container exits
        touched, diff, cand_tree = env.freeze_sut_candidate_state()

        assert "src/pagination.py" in touched
        assert "src/docker_created.py" in touched
        assert cand_tree != baseline_tree
        assert "+# docker edit" in diff
        assert "+docker_new = 1" in diff


def test_fusion_worktree_multi_step_and_deletion_transfer(tmp_path):
    """Fusion worktree: multi-step modifications and deletions survive and appear in frozen candidate."""
    with DisposableBenchmarkEnvironment(task_id="TASK-04", run_id="test_fusion_xfer", base_temp_dir=tmp_path) as env:
        baseline_tree = env.baseline_tree_hash

        # Simulate a Fusion worktree directory
        worktree_dir = tmp_path / "fusion_worktree_sim"
        shutil.copytree(env.repo_path, worktree_dir)

        # Multi-step modifications in worktree:
        # Step 1: modify src/lru_cache.py
        cache_file = worktree_dir / "src" / "lru_cache.py"
        cache_file.write_text("class LRUCache:\n    pass\n")

        # Step 2: create new file src/new_module.py
        new_file = worktree_dir / "src" / "new_module.py"
        new_file.write_text("MODULE_VERSION = 2\n")

        # Step 3: delete an existing file in worktree
        store_file = worktree_dir / "src" / "store.py"
        assert store_file.exists()
        store_file.unlink()

        # Simulate Fusion adapter worktree transfer to repo_path with deletion propagation
        for src_file in worktree_dir.rglob("*"):
            if src_file.is_file():
                rel = src_file.relative_to(worktree_dir)
                rel_str = str(rel).replace("\\", "/")
                if rel_str.startswith(".git") or rel_str.startswith(".fusion_"):
                    continue
                dest_file = env.repo_path / rel
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dest_file)

        for cur_file in list(env.repo_path.rglob("*")):
            if cur_file.is_file():
                rel = cur_file.relative_to(env.repo_path)
                rel_str = str(rel).replace("\\", "/")
                if rel_str.startswith(".git") or rel_str.startswith(".fusion_"):
                    continue
                if not (worktree_dir / rel).exists():
                    cur_file.unlink()

        # Freeze candidate state
        touched, diff, cand_tree = env.freeze_sut_candidate_state()

        assert "src/lru_cache.py" in touched
        assert "src/new_module.py" in touched
        assert "src/store.py" in touched
        assert not (env.repo_path / "src" / "store.py").exists()
        assert cand_tree != baseline_tree


def test_deletion_capture(tmp_path):
    """Deleting an existing file is captured in candidate touched files, diff, and tree hash."""
    with DisposableBenchmarkEnvironment(task_id="TASK-01", run_id="test_del_capture", base_temp_dir=tmp_path) as env:
        baseline_tree = env.baseline_tree_hash

        target = env.repo_path / "src" / "pagination.py"
        assert target.exists()
        target.unlink()

        touched, diff, cand_tree = env.freeze_sut_candidate_state()

        assert "src/pagination.py" in touched
        assert "deleted file mode" in diff
        assert cand_tree != baseline_tree


def test_baseline_discrimination_invariant(tmp_path):
    """If an edit task's candidate tree equals its baseline tree, hidden functional PASS must be rejected."""
    task_04 = get_task_by_id("TASK-04")
    evaluator = BenchmarkEvaluator()

    with DisposableBenchmarkEnvironment(task_id="TASK-04", run_id="test_invariant", base_temp_dir=tmp_path) as env:
        baseline_tree = env.baseline_tree_hash
        # Zero modifications: candidate tree is identical to baseline
        touched, diff, cand_tree = env.freeze_sut_candidate_state()
        assert cand_tree == baseline_tree
        assert touched == []

        scoring = evaluator.evaluate_task(
            task=task_04,
            repo_path=env.repo_path,
            files_touched=touched,
            baseline_tree_hash=baseline_tree,
            candidate_tree_hash=cand_tree,
        )

        assert scoring.hidden_tests_passed is False
        assert scoring.score == BenchmarkScore.FAIL
        assert scoring.scope_violated is True
        assert len(scoring.scope_violation_reasons) > 0
        assert any("Missing required path modifications" in r for r in scoring.scope_violation_reasons)


def test_codex_jsonl_parser_null_telemetry():
    """Parser preserves actual values, handles multi-turn events, and returns NULL/None (never 0) for missing metrics."""
    # 1. Complete event stream with all tokens
    stdout_full = "\n".join([
        '{"type":"thread.started","thread_id":"th_1"}',
        '{"type":"turn.started"}',
        '{"type":"turn.completed","usage":{"input_tokens":1200,"output_tokens":350,"reasoning_output_tokens":85}}',
    ])
    in_tok, out_tok, reas_tok, turns = parse_codex_jsonl_events(stdout_full)
    assert in_tok == 1200
    assert out_tok == 350
    assert reas_tok == 85
    assert turns == 1

    # 2. Event stream without reasoning tokens -> reasoning is None (NULL), NOT 0
    stdout_no_reas = "\n".join([
        '{"type":"turn.started"}',
        '{"type":"turn.completed","usage":{"input_tokens":500,"output_tokens":120}}',
    ])
    in_tok, out_tok, reas_tok, turns = parse_codex_jsonl_events(stdout_no_reas)
    assert in_tok == 500
    assert out_tok == 120
    assert reas_tok is None  # MUST be None / NULL, never 0

    # 3. Multiple turns accumulated
    stdout_multi = "\n".join([
        '{"type":"turn.started"}',
        '{"type":"turn.completed","usage":{"input_tokens":400,"output_tokens":100,"reasoning_output_tokens":20}}',
        '{"type":"turn.started"}',
        '{"type":"turn.completed","usage":{"input_tokens":600,"output_tokens":150,"reasoning_output_tokens":30}}',
    ])
    in_tok, out_tok, reas_tok, turns = parse_codex_jsonl_events(stdout_multi)
    assert in_tok == 1000
    assert out_tok == 250
    assert reas_tok == 50
    assert turns == 2

    # 4. Incomplete/timeout stream without usage -> all token metrics are None (NULL), NOT 0
    stdout_timeout = "\n".join([
        '{"type":"thread.started","thread_id":"th_timeout"}',
        '{"type":"turn.started"}',
        '{"type":"message.delta","content":"Thinking about LRU cache..."}',
    ])
    in_tok, out_tok, reas_tok, turns = parse_codex_jsonl_events(stdout_timeout)
    assert in_tok is None  # MUST be None / NULL, never 0
    assert out_tok is None  # MUST be None / NULL, never 0
    assert reas_tok is None  # MUST be None / NULL, never 0
    assert turns == 1
