"""Static integrity tests verifying zero benchmark policy or task leakage in Fusion product code."""

import os
import re
from pathlib import Path


def test_no_benchmark_task_ids_in_fusion_product():
    """Verify that fusion_agent/ contains zero references to benchmark task IDs (TASK-01, TASK-02, etc.)."""
    repo_root = Path(__file__).resolve().parent.parent
    fusion_agent_dir = repo_root / "fusion_agent"

    task_id_pattern = re.compile(r"\bTASK-\d{2}\b", re.IGNORECASE)
    violations = []

    for root, _, files in os.walk(fusion_agent_dir):
        for file in files:
            if file.endswith((".py", ".json", ".yaml", ".yml", ".md", ".toml")):
                filepath = Path(root) / file
                content = filepath.read_text(encoding="utf-8", errors="ignore")
                matches = task_id_pattern.findall(content)
                if matches:
                    rel_path = filepath.relative_to(repo_root)
                    violations.append(f"{rel_path}: matches {set(matches)}")

    assert not violations, f"Found benchmark task ID leakage in product code:\n" + "\n".join(violations)


def test_no_benchmark_evaluator_paths_in_fusion_product():
    """Verify that fusion_agent/ contains zero references to hidden evaluator filenames or suites."""
    repo_root = Path(__file__).resolve().parent.parent
    fusion_agent_dir = repo_root / "fusion_agent"

    evaluator_pattern = re.compile(r"\b(eval_task_\w+|evaluators/eval_\w+|eval_suite_1_1_0)\b", re.IGNORECASE)
    violations = []

    for root, _, files in os.walk(fusion_agent_dir):
        for file in files:
            if file.endswith((".py", ".json", ".yaml", ".yml", ".md", ".toml")):
                filepath = Path(root) / file
                content = filepath.read_text(encoding="utf-8", errors="ignore")
                matches = evaluator_pattern.findall(content)
                if matches:
                    rel_path = filepath.relative_to(repo_root)
                    violations.append(f"{rel_path}: matches {set(matches)}")

    assert not violations, f"Found benchmark evaluator leakage in product code:\n" + "\n".join(violations)


def test_no_benchmark_allowed_path_oracles_in_fusion_product():
    """Verify that fusion_agent/ does not contain hardcoded benchmark allowed path lists."""
    repo_root = Path(__file__).resolve().parent.parent
    fusion_agent_dir = repo_root / "fusion_agent"

    oracle_pattern = re.compile(r"\b(allowed_paths|strict_compliance_paths|benchmark_oracle)\b", re.IGNORECASE)
    violations = []

    for root, _, files in os.walk(fusion_agent_dir):
        for file in files:
            if file.endswith((".py", ".json", ".yaml", ".yml", ".md", ".toml")):
                filepath = Path(root) / file
                content = filepath.read_text(encoding="utf-8", errors="ignore")
                matches = oracle_pattern.findall(content)
                if matches:
                    rel_path = filepath.relative_to(repo_root)
                    violations.append(f"{rel_path}: matches {set(matches)}")

    assert not violations, f"Found benchmark oracle leakage in product code:\n" + "\n".join(violations)
