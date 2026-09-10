"""Benchmark Execution Runner for Comparative Multi-SUT Evaluation.

Orchestrates clean disposable repositories, randomized/rotated SUT execution order,
repetitions, post-exit hidden evaluation, provenance collection, and persistence.
"""

import argparse
import os
import platform
import random
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set

from benchmarks.adapters import (
    AntigravityAloneAdapter,
    BaseSUTAdapter,
    CodexAloneAdapter,
    FusionSUTAdapter,
    MockSUTAdapter,
)
from benchmarks.evaluators import BenchmarkEvaluator
from benchmarks.isolation import DisposableBenchmarkEnvironment
from benchmarks.schema import (
    BenchmarkRunRecord,
    BenchmarkScore,
    BenchmarkTask,
    RunExecutionStatus,
    SystemUnderTest,
)
from benchmarks.storage import BenchmarkStorage
from benchmarks.tasks.catalog import (
    BENCHMARK_SUITE_VERSION,
    BENCHMARK_TASKS,
    compute_benchmark_suite_hash,
    compute_hidden_evaluator_hash,
    compute_task_definition_hash,
    get_task_by_id,
)


class BenchmarkRunner:
    """Coordinates comparative multi-SUT evaluation runs."""

    def __init__(
        self,
        storage: Optional[BenchmarkStorage] = None,
        evaluator: Optional[BenchmarkEvaluator] = None,
        is_live: bool = False,
        base_temp_dir: Optional[Path] = None,
        custom_adapters: Optional[Dict[SystemUnderTest, BaseSUTAdapter]] = None,
    ):
        self.storage = storage or BenchmarkStorage()
        self.evaluator = evaluator or BenchmarkEvaluator()
        self.is_live = is_live
        self.base_temp_dir = base_temp_dir
        self.custom_adapters = custom_adapters or {}

    def get_adapter(self, sut: SystemUnderTest, solve_tasks: Optional[Set[str]] = None) -> BaseSUTAdapter:
        """Resolve adapter for a System Under Test."""
        if sut in self.custom_adapters:
            return self.custom_adapters[sut]

        if not self.is_live:
            # Deterministic mock adapter for offline harness verification
            return MockSUTAdapter(sut=sut, solve_tasks=solve_tasks)

        if sut == SystemUnderTest.FUSION:
            return FusionSUTAdapter(is_live=True)
        elif sut == SystemUnderTest.CODEX_ALONE:
            return CodexAloneAdapter(is_live=True)
        elif sut == SystemUnderTest.ANTIGRAVITY_ALONE:
            return AntigravityAloneAdapter(is_live=True)
        else:
            raise ValueError(f"Unknown SUT: {sut}")

    def run_matrix(
        self,
        tasks: Optional[List[BenchmarkTask]] = None,
        suts: Optional[List[SystemUnderTest]] = None,
        repetitions: int = 1,
        randomize_order: bool = True,
        solve_tasks_for_mock: Optional[Set[str]] = None,
    ) -> List[BenchmarkRunRecord]:
        """Execute the benchmark matrix with repetition and run order rotation."""
        active_tasks = tasks or BENCHMARK_TASKS
        active_suts = suts or [
            SystemUnderTest.FUSION,
            SystemUnderTest.CODEX_ALONE,
            SystemUnderTest.ANTIGRAVITY_ALONE,
        ]

        suite_hash = compute_benchmark_suite_hash()
        records: List[BenchmarkRunRecord] = []

        print(f"[*] Starting Benchmark Matrix: {len(active_tasks)} tasks, {len(active_suts)} SUTs, {repetitions} repetitions")
        print(f"[*] Benchmark Suite Hash: {suite_hash}")

        for rep_idx in range(repetitions):
            print(f"\n--- Repetition {rep_idx + 1}/{repetitions} ---")

            for task_idx, task in enumerate(active_tasks):
                task_hash = compute_task_definition_hash(task)

                # Rotate or randomize SUT order to eliminate execution bias
                current_suts = list(active_suts)
                if randomize_order:
                    # Deterministic rotation based on repetition and task index
                    rotate_by = (rep_idx + task_idx) % len(current_suts)
                    current_suts = current_suts[rotate_by:] + current_suts[:rotate_by]
                
                print(f"Task {task.task_id} ({task.category.value}) - Execution Order: {[s.value for s in current_suts]}")

                for sut in current_suts:
                    record = self.run_single_task(
                        task=task,
                        sut=sut,
                        repetition_index=rep_idx,
                        suite_hash=suite_hash,
                        task_hash=task_hash,
                        solve_tasks_for_mock=solve_tasks_for_mock,
                    )
                    records.append(record)
                    self.storage.record_run(record)
                    print(f"  -> {sut.value}: Score={record.score.value}, HiddenPassed={record.hidden_tests_passed}, Regressions={record.regressions_count}, Touched={record.files_touched}")

        return records

    def run_single_task(
        self,
        task: BenchmarkTask,
        sut: SystemUnderTest,
        repetition_index: int = 0,
        suite_hash: Optional[str] = None,
        task_hash: Optional[str] = None,
        solve_tasks_for_mock: Optional[Set[str]] = None,
    ) -> BenchmarkRunRecord:
        """Execute a single task run on an isolated disposable environment.
        
        SECURITY & ISOLATION NOTE (V1):
        V1 hidden evaluators are withheld from normal model/repository context by living
        outside the disposable repository during execution. However, they are on the same host
        and are not protected by an OS-level security sandbox (e.g. gVisor, Firecracker, Docker VM).
        Future strong-isolation mode may execute SUTs inside ephemeral containers/VMs.
        """
        suite_hash = suite_hash or compute_benchmark_suite_hash()
        task_hash = task_hash or compute_task_definition_hash(task)
        hidden_hash = compute_hidden_evaluator_hash(task)
        run_id = f"bench_{task.task_id}_{sut.value}_r{repetition_index}_{uuid.uuid4().hex[:8]}"

        start_utc = datetime.now(timezone.utc).isoformat()
        adapter = self.get_adapter(sut, solve_tasks=solve_tasks_for_mock)

        # 1. Clean Snapshot Isolation: create fresh disposable Git repo with deterministic initial commit
        with DisposableBenchmarkEnvironment(task_id=task.task_id, run_id=run_id, base_temp_dir=self.base_temp_dir) as env:
            baseline_hash = env.baseline_snapshot_hash or env.baseline_commit_hash

            # Verify hidden tests do NOT exist in the repository before/during SUT execution
            assert not (env.repo_path / "tests" / "_hidden_eval.py").exists(), "Hidden test leaked into baseline repo"
            assert not (env.repo_path / "benchmarks").exists(), "Benchmark suite leaked into baseline repo"

            # 2. SUT Execution in sanitized disposable repo
            old_pythonpath = os.environ.get("PYTHONPATH")
            try:
                os.environ.pop("PYTHONPATH", None)
                telemetry = adapter.execute(task, env.repo_path)
            finally:
                if old_pythonpath is not None:
                    os.environ["PYTHONPATH"] = old_pythonpath

            # Verify hidden tests were not created during SUT execution
            assert not (env.repo_path / "tests" / "_hidden_eval.py").exists(), "Hidden test created during SUT execution"

            # 3. Capture & freeze candidate state immediately on SUT exit before hidden evaluator injection
            sut_touched_files, sut_git_diff, sut_candidate_tree_hash = env.freeze_sut_candidate_state()
            files_touched = sut_touched_files
            git_diff = sut_git_diff
            sut_tree_hash = sut_candidate_tree_hash

            # 4. Objective Review Evaluation against pre-registered defect criteria
            pre_review_failed, mapped_criteria, reviewer_found_valid = self.evaluator.evaluate_pre_review_criteria(
                task=task,
                repo_path=env.repo_path,
                reviewer_finding_text=telemetry.reviewer_verdict,
            )

            # 5. Hidden Acceptance & Scope Oracle Evaluation (Materialized ONLY post-exit)
            scoring = self.evaluator.evaluate_task(task, env.repo_path, sut_touched_files)

            # 6. Orthogonal Execution Status vs Patch Correctness Separation
            err_lower = (telemetry.error_message or "").lower()
            if "timed out" in err_lower or "timeout" in err_lower:
                run_status = RunExecutionStatus.TIMEOUT
                # Candidate correctness is independently evaluated regardless of timeout
                final_score = scoring.score
            elif any(x in err_lower for x in ["auth", "not logged in", "unauthenticated", "invalid credentials", "login required"]):
                run_status = RunExecutionStatus.AUTH_FAILURE
                final_score = BenchmarkScore.INFRASTRUCTURE_FAILURE
            elif any(x in err_lower for x in ["rate limit", "quota", "provider unavailable", "service unavailable"]):
                run_status = RunExecutionStatus.PROVIDER_UNAVAILABLE
                final_score = BenchmarkScore.INFRASTRUCTURE_FAILURE
            elif any(x in err_lower for x in ["transport", "connection error", "network", "connection refused", "econnrefused"]):
                run_status = RunExecutionStatus.INFRASTRUCTURE_FAILURE
                final_score = BenchmarkScore.INFRASTRUCTURE_FAILURE
            else:
                run_status = RunExecutionStatus.COMPLETED
                final_score = scoring.score

            # Verify hidden test was cleanly unlinked post-exit
            assert not (env.repo_path / "tests" / "_hidden_eval.py").exists(), "Hidden test left behind after evaluation"

        end_utc = datetime.now(timezone.utc).isoformat()

        # 7. Token metrics: calculate estimated provider overhead residual
        residual = None
        if telemetry.native_input_tokens and telemetry.fusion_controlled_context_tokens is not None:
            residual = telemetry.native_input_tokens - telemetry.fusion_controlled_context_tokens

        # Objective repair success: valid defect caught AND passes all hidden criteria without regressions
        repair_successful = (
            reviewer_found_valid
            and scoring.hidden_tests_passed
            and scoring.regressions_passed
            and telemetry.repair_rounds > 0
        )

        # 8. Assemble complete persistent run record
        record = BenchmarkRunRecord(
            run_id=run_id,
            benchmark_suite_version=BENCHMARK_SUITE_VERSION,
            benchmark_suite_hash=suite_hash,
            task_definition_hash=task_hash,
            hidden_evaluator_hash=hidden_hash,
            baseline_snapshot_hash=baseline_hash,
            task_id=task.task_id,
            category=task.category.value,
            system_under_test=sut,
            repetition_index=repetition_index,
            run_status=run_status,
            validity_disposition="VALID",
            invalidation_reasons=[],
            execution_mode="LIVE" if self.is_live else "MOCK",
            experiment_phase="PHASE_B_PILOT" if self.is_live else "MOCK_VALIDATION",
            start_time=start_utc,
            end_time=end_utc,
            wall_clock_duration_seconds=telemetry.wall_clock_duration_seconds,
            active_provider_duration_seconds=telemetry.active_provider_duration_seconds,
            os_platform=f"{platform.system()} {platform.release()} ({platform.machine()})",
            python_version=platform.python_version(),
            provider_model_id=telemetry.provider_model_id,
            cli_version=telemetry.cli_version,
            reasoning_effort=telemetry.reasoning_effort,
            fusion_config_hash=telemetry.fusion_config_hash,
            score=final_score,
            verification_passed=scoring.task_tests_passed,
            hidden_tests_passed=scoring.hidden_tests_passed,
            regressions_count=scoring.regressions_count,
            files_touched=files_touched,
            unintended_files=scoring.unintended_files,
            git_diff=git_diff,
            sut_tree_hash=sut_tree_hash,
            sut_candidate_tree_hash=sut_candidate_tree_hash,
            sut_git_diff=sut_git_diff,
            sut_touched_files=sut_touched_files,
            verification_duration_seconds=telemetry.verification_duration_seconds,
            provider_stages=telemetry.provider_stages,
            reviewer_verdict=telemetry.reviewer_verdict,
            reviewer_found_defect=telemetry.reviewer_found_defect,
            reviewer_found_valid_defect=reviewer_found_valid,
            pre_review_criteria_failed=pre_review_failed,
            reviewer_mapped_defect_criteria=mapped_criteria,
            defect_in_test_passing_patch=telemetry.defect_in_test_passing_patch,
            repair_rounds=telemetry.repair_rounds,
            repair_successful=repair_successful,
            human_promotion_disposition=telemetry.human_promotion_disposition,
            pre_review_patch=telemetry.pre_review_patch,
            pre_review_test_passed=telemetry.pre_review_test_passed,
            reviewer_findings=telemetry.reviewer_findings,
            repair_patch=telemetry.repair_patch,
            fusion_controlled_context_tokens=telemetry.fusion_controlled_context_tokens,
            native_input_tokens=telemetry.native_input_tokens,
            native_output_tokens=telemetry.native_output_tokens,
            native_reasoning_tokens=telemetry.native_reasoning_tokens,
            provider_managed_overhead_residual=residual,
            provider_calls_count=telemetry.provider_calls_count,
            mcp_calls_count=telemetry.mcp_calls_count,
            recovery_events=telemetry.recovery_events,
            policy_denials=telemetry.policy_denials,
            error_message=telemetry.error_message,
        )

        return record


def parse_args():
    parser = argparse.ArgumentParser(description="Milestone 11 Comparative Benchmark Runner")
    parser.add_argument("--tasks", type=str, default="", help="Comma-separated list of task IDs (e.g. TASK-01,TASK-02)")
    parser.add_argument("--systems", type=str, default="", help="Comma-separated SUTs (fusion,codex_alone,antigravity_alone)")
    parser.add_argument("--repetitions", type=int, default=1, help="Number of repetitions per task (default 1)")
    parser.add_argument("--live", action="store_true", help="Run with live LLM / CLI adapters instead of mock")
    return parser.parse_args()


def main():
    args = parse_args()
    tasks = None
    if args.tasks:
        selected_ids = [t.strip() for t in args.tasks.split(",") if t.strip()]
        tasks = [t for t in BENCHMARK_TASKS if t.task_id in selected_ids]

    suts = None
    if args.systems:
        selected_suts = [s.strip().lower() for s in args.systems.split(",") if s.strip()]
        suts = [SystemUnderTest(s) for s in selected_suts]

    runner = BenchmarkRunner(is_live=args.live)
    records = runner.run_matrix(tasks=tasks, suts=suts, repetitions=args.repetitions)
    print(f"\n[+] Benchmark completed. Persisted {len(records)} runs to storage.")


if __name__ == "__main__":
    main()
