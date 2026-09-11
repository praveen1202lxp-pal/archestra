"""Milestone 11 - Phase B Small Live Comparative Pilot Runner.

Executes exactly 6 runs (TASK-01 and TASK-10 across FUSION, CODEX_ALONE, ANTIGRAVITY_ALONE).
Enforces frozen benchmark inputs, strict post-exit hidden evaluation, clean disposable repositories,
and complete provenance persistence with full SHA-256 digests.
"""

import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from benchmarks.adapters import (
    AntigravityAloneAdapter,
    CodexAloneAdapter,
    FusionSUTAdapter,
)
from benchmarks.evaluators import BenchmarkEvaluator
from benchmarks.isolation import DisposableBenchmarkEnvironment
from benchmarks.schema import (
    BenchmarkRunRecord,
    BenchmarkScore,
    BenchmarkTask,
    RunExecutionStatus,
    SystemUnderTest,
    ValidityDisposition,
)
from benchmarks.storage import BenchmarkStorage
from benchmarks.tasks.catalog import (
    BENCHMARK_TASKS,
    compute_benchmark_suite_hash,
    compute_hidden_evaluator_hash,
    compute_task_definition_hash,
    get_task_by_id,
)


def run_pilot():
    print("================================================================================")
    print("  ARCHESTRA BENCHMARK: MILESTONE 11 PHASE B LIVE COMPARATIVE PILOT")
    print("================================================================================")

    # 1. Compute and verify frozen benchmark inputs
    suite_version = "1.1.0"
    suite_hash = compute_benchmark_suite_hash()
    commit_sha = subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip()

    task_01 = get_task_by_id("TASK-01")
    task_10 = get_task_by_id("TASK-10")

    task_01_hash = compute_task_definition_hash(task_01)
    task_10_hash = compute_task_definition_hash(task_10)

    task_01_hidden_hash = compute_hidden_evaluator_hash(task_01)
    task_10_hidden_hash = compute_hidden_evaluator_hash(task_10)

    # Baseline snapshot hashes
    with DisposableBenchmarkEnvironment(task_id="TASK-01", run_id="probe_01") as env:
        task_01_baseline_hash = env.baseline_snapshot_hash
    with DisposableBenchmarkEnvironment(task_id="TASK-10", run_id="probe_10") as env:
        task_10_baseline_hash = env.baseline_snapshot_hash

    print(f"Benchmark Suite Version:   {suite_version}")
    print(f"Benchmark Suite SHA-256:   {suite_hash}")
    print(f"Benchmark Code Commit SHA: {commit_sha}")
    print(f"TASK-01 Def SHA-256:       {task_01_hash}")
    print(f"TASK-01 Hidden SHA-256:    {task_01_hidden_hash}")
    print(f"TASK-01 Baseline SHA-256:  {task_01_baseline_hash}")
    print(f"TASK-10 Def SHA-256:       {task_10_hash}")
    print(f"TASK-10 Hidden SHA-256:    {task_10_hidden_hash}")
    print(f"TASK-10 Baseline SHA-256:  {task_10_baseline_hash}")
    print("================================================================================\n")

    # Set up storage (dedicated pilot db and primary db)
    pilot_db_path = Path("benchmarks/results/live_pilot_results.db")
    primary_db_path = Path("benchmarks/results/benchmark_results.db")
    pilot_storage = BenchmarkStorage(db_path=pilot_db_path)
    primary_storage = BenchmarkStorage(db_path=primary_db_path)
    evaluator = BenchmarkEvaluator()

    # Pre-flight provider health checks (non-generative only, zero model calls)
    print("[*] Verifying provider health (strictly non-generative)...")
    codex_adapter = CodexAloneAdapter(is_live=True, reasoning_effort="medium")
    agy_adapter = AntigravityAloneAdapter(is_live=True, reasoning_effort="medium")
    fusion_adapter = FusionSUTAdapter(is_live=True)

    from fusion_agent.providers.codex_cli import CodexCLIProvider

    h_codex = CodexCLIProvider().health_check()
    proc_agy_dock = subprocess.run(
        ["wsl", "-u", "root", "docker", "run", "--rm", "-i", "antigravity-benchmark:1.2.0", "--version"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )
    agy_docker_healthy = (proc_agy_dock.returncode == 0 and "1.2.0" in proc_agy_dock.stdout)

    print(f"  Codex CLI health:       {'ONLINE' if h_codex.healthy else 'OFFLINE'} - {h_codex.message}")
    print(f"  Antigravity Docker:     {'ONLINE' if agy_docker_healthy else 'OFFLINE'} - version {proc_agy_dock.stdout.strip()}")

    if not h_codex.healthy or not agy_docker_healthy:
        print("[!] Stopping condition triggered: Provider health check failed.")
        sys.exit(1)

    # 6 Planned Execution Matrix with Deterministic Rotation
    # Task 01: rep=0, task_idx=0 -> rot=0 -> [FUSION, CODEX_ALONE, ANTIGRAVITY_ALONE]
    # Task 10: rep=0, task_idx=1 -> rot=1 -> [CODEX_ALONE, ANTIGRAVITY_ALONE, FUSION]
    planned_runs = [
        (task_01, SystemUnderTest.FUSION, 0, task_01_hash, task_01_hidden_hash, task_01_baseline_hash, fusion_adapter),
        (task_01, SystemUnderTest.CODEX_ALONE, 0, task_01_hash, task_01_hidden_hash, task_01_baseline_hash, codex_adapter),
        (task_01, SystemUnderTest.ANTIGRAVITY_ALONE, 0, task_01_hash, task_01_hidden_hash, task_01_baseline_hash, agy_adapter),
        (task_10, SystemUnderTest.CODEX_ALONE, 0, task_10_hash, task_10_hidden_hash, task_10_baseline_hash, codex_adapter),
        (task_10, SystemUnderTest.ANTIGRAVITY_ALONE, 0, task_10_hash, task_10_hidden_hash, task_10_baseline_hash, agy_adapter),
        (task_10, SystemUnderTest.FUSION, 0, task_10_hash, task_10_hidden_hash, task_10_baseline_hash, fusion_adapter),
    ]

    executed_records: List[BenchmarkRunRecord] = []

    for run_idx, (task, sut, rep_idx, task_hash, hidden_hash, baseline_hash, adapter) in enumerate(planned_runs, start=1):
        run_id = f"pilot_{task.task_id}_{sut.value}_r{rep_idx}_{int(time.time())}"
        print(f"\n[{run_idx}/6] EXECUTING LIVE RUN: Task={task.task_id} | SUT={sut.value} | RunID={run_id}")

        start_utc = datetime.now(timezone.utc).isoformat()

        # Step A: Clean Snapshot Isolation
        with DisposableBenchmarkEnvironment(task_id=task.task_id, run_id=run_id) as env:
            # Stopping condition check: verify baseline snapshot hash matches frozen baseline
            if env.baseline_snapshot_hash != baseline_hash:
                print(f"[!] Stopping condition: Baseline snapshots differ! Expected {baseline_hash}, got {env.baseline_snapshot_hash}")
                sys.exit(1)

            # Stopping condition check: verify hidden tests do NOT leak into SUT-visible context
            if (env.repo_path / "tests" / "_hidden_eval.py").exists():
                print("[!] Stopping condition: Hidden evaluator leaked into baseline repository!")
                sys.exit(1)

            # Step B: SUT Live Execution
            telemetry = adapter.execute(task, env.repo_path)

            # Verify hidden test was not created during SUT run
            if (env.repo_path / "tests" / "_hidden_eval.py").exists():
                print("[!] Stopping condition: Hidden evaluator leaked during SUT execution!")
                sys.exit(1)

            # Step C: Capture and freeze candidate state before hidden evaluation
            files_touched, git_diff, candidate_tree_hash = env.freeze_sut_candidate_state()

            # Step D: Objective Pre-Review Evaluation
            pre_review_failed, mapped_criteria, reviewer_found_valid = evaluator.evaluate_pre_review_criteria(
                task=task,
                repo_path=env.repo_path,
                reviewer_finding_text=telemetry.reviewer_findings,
            )

            # Step E: Post-Exit Hidden Evaluation (Injected ONLY after candidate is frozen)
            scoring = evaluator.evaluate_task(
                task=task,
                repo_path=env.repo_path,
                files_touched=files_touched,
            )

            # Verify hidden test was cleanly unlinked
            if (env.repo_path / "tests" / "_hidden_eval.py").exists():
                print("[!] Stopping condition: Hidden evaluator left behind after evaluation!")
                sys.exit(1)

        end_utc = datetime.now(timezone.utc).isoformat()

        # Determine execution status (separate from correctness)
        if telemetry.error_message:
            if "timeout" in telemetry.error_message.lower():
                run_status = RunExecutionStatus.TIMEOUT
            elif any(x in telemetry.error_message.lower() for x in ["auth", "unauthenticated", "not logged in"]):
                run_status = RunExecutionStatus.AUTH_FAILURE
            elif any(x in telemetry.error_message.lower() for x in ["rate limit", "transport", "connection error"]):
                run_status = RunExecutionStatus.INFRASTRUCTURE_FAILURE
            else:
                run_status = RunExecutionStatus.COMPLETED
        else:
            run_status = RunExecutionStatus.COMPLETED

        # Infrastructure vs Engineering failure classification
        final_score = scoring.score
        if telemetry.error_message and any(
            x in telemetry.error_message.lower()
            for x in ["rate limit", "transport", "auth", "unauthenticated", "not logged in", "connection error"]
        ):
            final_score = BenchmarkScore.INFRASTRUCTURE_FAILURE

        residual = None
        if telemetry.native_input_tokens and telemetry.fusion_controlled_context_tokens is not None:
            residual = telemetry.native_input_tokens - telemetry.fusion_controlled_context_tokens

        repair_successful = (
            reviewer_found_valid
            and scoring.hidden_tests_passed
            and scoring.regressions_passed
            and telemetry.repair_rounds > 0
        )

        container_overhead = (
            max(0.0, telemetry.wall_clock_duration_seconds - telemetry.active_provider_duration_seconds)
            if sut == SystemUnderTest.ANTIGRAVITY_ALONE
            else None
        )
        initial_routing_strategy = "CODEX_HEAVY" if sut == SystemUnderTest.FUSION else None
        initial_routing_snapshot_hash = "4ad58f2641066b1b6da7f9145ee1c1aae9afa5e3d56aa48c49e0fb435df32102" if sut == SystemUnderTest.FUSION else None

        record = BenchmarkRunRecord(
            run_id=run_id,
            benchmark_suite_version=suite_version,
            benchmark_suite_hash=suite_hash,
            task_definition_hash=task_hash,
            hidden_evaluator_hash=hidden_hash,
            baseline_snapshot_hash=baseline_hash,
            task_id=task.task_id,
            category=task.category.value,
            system_under_test=sut,
            repetition_index=rep_idx,
            run_status=run_status,
            validity_disposition=ValidityDisposition.VALID.value,
            execution_mode="LIVE",
            experiment_phase="PHASE_B_PILOT",
            start_time=start_utc,
            end_time=end_utc,
            wall_clock_duration_seconds=telemetry.wall_clock_duration_seconds,
            active_provider_duration_seconds=telemetry.active_provider_duration_seconds,
            container_overhead_seconds=container_overhead,
            os_platform=f"{platform.system()} {platform.release()} ({platform.machine()})",
            python_version=platform.python_version(),
            provider_model_id=telemetry.provider_model_id,
            cli_version=telemetry.cli_version,
            reasoning_effort=telemetry.reasoning_effort,
            fusion_config_hash=telemetry.fusion_config_hash,
            benchmark_harness_commit=commit_sha,
            initial_routing_strategy=initial_routing_strategy,
            initial_routing_snapshot_hash=initial_routing_snapshot_hash,
            score=final_score,
            verification_passed=scoring.task_tests_passed,
            hidden_tests_passed=scoring.hidden_tests_passed,
            regressions_count=scoring.regressions_count,
            files_touched=files_touched,
            unintended_files=scoring.unintended_files,
            scope_violated=scoring.scope_violated,
            git_diff=git_diff,
            sut_candidate_tree_hash=candidate_tree_hash,
            sut_tree_hash=candidate_tree_hash,
            sut_git_diff=git_diff,
            sut_touched_files=files_touched,
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
            native_cache_read_tokens=getattr(telemetry, "native_cache_read_tokens", None),
            native_cache_write_tokens=getattr(telemetry, "native_cache_write_tokens", None),
            provider_managed_overhead_residual=residual,
            provider_calls_count=telemetry.provider_calls_count,
            provider_stages=telemetry.provider_stages,
            mcp_calls_count=telemetry.mcp_calls_count,
            recovery_events=telemetry.recovery_events,
            policy_denials=telemetry.policy_denials,
            error_message=telemetry.error_message,
        )

        # Persist to SQLite
        pilot_storage.record_run(record)
        primary_storage.record_run(record)
        executed_records.append(record)

        print(f"  -> Result: Score={record.score.value} | VisibleTests={record.verification_passed} | HiddenPassed={record.hidden_tests_passed} | Regressions={record.regressions_count}")
        print(f"     Touched={record.files_touched} | Unintended={record.unintended_files} | WallTime={record.wall_clock_duration_seconds:.2f}s")
        print(f"     Tokens: in={record.native_input_tokens}, out={record.native_output_tokens}, reasoning={record.native_reasoning_tokens}, fusion_ctx={record.fusion_controlled_context_tokens}")
        if record.reviewer_verdict:
            print(f"     Fusion Review: Verdict={record.reviewer_verdict}, FoundDefect={record.reviewer_found_defect}, Repairs={record.repair_rounds}")

    print("\n[+] All 6 live pilot executions successfully completed and persisted.")
    return executed_records


if __name__ == "__main__":
    run_pilot()
