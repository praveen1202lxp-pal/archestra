"""Phase C-DEV Development Tasks Evaluation Runner.

Evaluates Fusion alone ONLY on development tasks:
- TASK-02 (Failing unit test repair)
- TASK-04 (Multi-file feature)
- TASK-07 (Edge-case bug)

Strictly enforces:
- ZERO access or execution of held-out tasks (TASK-03, 05, 06, 08, 09, 11, 12).
- Clean disposable snapshot isolation for each run.
- Capture of routing decisions, scope decisions, provider calls, tokens, durations, and hidden evaluation.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure repo root on path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from benchmarks.adapters.fusion_adapter import FusionSUTAdapter
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
    compute_benchmark_suite_hash,
    compute_hidden_evaluator_hash,
    compute_task_definition_hash,
    get_task_by_id,
)

# STRICT GUARD: Permitted development tasks only
ALLOWED_DEV_TASK_IDS = ["TASK-02", "TASK-04", "TASK-07"]


def run_dev_eval():
    print("=" * 80)
    print("  ARCHESTRA BENCHMARK: PHASE C-DEV DEVELOPMENT EVALUATION (FUSION ONLY)")
    print("=" * 80)

    # 1. Verify tasks
    dev_tasks = []
    for tid in ALLOWED_DEV_TASK_IDS:
        t = get_task_by_id(tid)
        assert t is not None, f"Could not find task {tid}"
        dev_tasks.append(t)

    print(f"[*] Development Tasks to Evaluate: {[t.task_id for t in dev_tasks]}")
    print(f"[*] Benchmark Suite Hash: {compute_benchmark_suite_hash()}")
    print("-" * 80)

    # 2. Check provider health
    from fusion_agent.providers.codex_cli import CodexCLIProvider
    from fusion_agent.providers.antigravity_cli import AntigravityCLIProvider
    import subprocess

    print("[*] Verifying provider health (non-generative)...")
    codex_provider = CodexCLIProvider()
    h_codex = codex_provider.health_check()
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
        print("[!] Pre-flight failure: Provider not healthy.")
        sys.exit(1)

    evaluator = BenchmarkEvaluator()
    dev_storage = BenchmarkStorage(db_path=Path("benchmarks/results/c_dev_results.db"))

    results = []

    for idx, task in enumerate(dev_tasks, 1):
        print(f"\n[{idx}/{len(dev_tasks)}] RUNNING DEVELOPMENT TASK: {task.task_id} ({task.title})")
        run_id = f"c_dev_{task.task_id}_{int(time.time())}"
        adapter = FusionSUTAdapter(is_live=True)

        t_start = time.time()
        with DisposableBenchmarkEnvironment(task_id=task.task_id, run_id=run_id) as env:
            # Baseline integrity assertion
            assert not (env.repo_path / "tests" / "_hidden_eval.py").exists(), "Hidden test leaked into baseline repo"

            # Execute Fusion SUT
            telemetry = adapter.execute(task, env.repo_path)

            # Freeze candidate state
            sut_touched_files, sut_git_diff, sut_candidate_tree_hash = env.freeze_sut_candidate_state()

            # Pre-review evaluation
            pre_review_failed, mapped_criteria, reviewer_found_valid = evaluator.evaluate_pre_review_criteria(
                task=task,
                repo_path=env.repo_path,
                reviewer_finding_text=telemetry.reviewer_findings,
            )

            # Post-exit hidden evaluation
            scoring = evaluator.evaluate_task(task, env.repo_path, sut_touched_files)

            # Verify hidden test was cleanly unlinked
            assert not (env.repo_path / "tests" / "_hidden_eval.py").exists(), "Hidden test left behind after evaluation"

        wall_time = time.time() - t_start

        record = BenchmarkRunRecord(
            run_id=run_id,
            benchmark_suite_version="1.1.0",
            benchmark_suite_hash=compute_benchmark_suite_hash(),
            task_id=task.task_id,
            category=task.category.value,
            task_definition_hash=compute_task_definition_hash(task),
            hidden_evaluator_hash=compute_hidden_evaluator_hash(task),
            baseline_snapshot_hash=sut_candidate_tree_hash or "",
            system_under_test=SystemUnderTest.FUSION,
            repetition_index=0,
            run_status=RunExecutionStatus.COMPLETED if not telemetry.error_message else RunExecutionStatus.INFRASTRUCTURE_FAILURE,
            validity_disposition=ValidityDisposition.VALID.value,
            execution_mode="LIVE",
            experiment_phase="PHASE_C_DEV",
            start_time=datetime.now(timezone.utc).isoformat(),
            score=scoring.score,
            verification_passed=scoring.task_tests_passed,
            hidden_tests_passed=scoring.hidden_tests_passed,
            regressions_count=scoring.regressions_count,
            files_touched=sut_touched_files,
            unintended_files=scoring.unintended_files,
            scope_violated=scoring.scope_violated,
            git_diff=sut_git_diff,
            sut_tree_hash=sut_candidate_tree_hash,
            wall_clock_duration_seconds=wall_time,
            active_provider_duration_seconds=telemetry.active_provider_duration_seconds,
            fusion_controlled_context_tokens=telemetry.fusion_controlled_context_tokens,
            native_input_tokens=telemetry.native_input_tokens,
            native_output_tokens=telemetry.native_output_tokens,
            native_reasoning_tokens=telemetry.native_reasoning_tokens,
            provider_calls_count=telemetry.provider_calls_count,
            repair_rounds=telemetry.repair_rounds,
            reviewer_verdict=telemetry.reviewer_verdict,
            reviewer_findings=telemetry.reviewer_findings,
            reviewer_found_valid_defect=reviewer_found_valid,
            error_message=telemetry.error_message,
        )
        dev_storage.record_run(record)

        strict_scope = not record.scope_violated
        hidden_passed = record.hidden_tests_passed
        print(f"  Result: Score={record.score.value} | HiddenPassed={hidden_passed} | StrictScope={strict_scope}")
        print(f"  Touched Files: {sut_touched_files}")
        print(f"  Provider Calls: {record.provider_calls_count} | Repairs: {record.repair_rounds}")
        print(f"  Tokens: in={record.native_input_tokens} | out={record.native_output_tokens} | reasoning={record.native_reasoning_tokens} | fusion_ctx={record.fusion_controlled_context_tokens}")
        print(f"  Timing: active={record.active_provider_duration_seconds:.2f}s | wall={record.wall_clock_duration_seconds:.2f}s")
        if scoring.failure_reasons:
            print(f"  Failure reasons: {scoring.failure_reasons}")

        results.append((task, record, sut_git_diff, scoring))

    print("\n" + "=" * 80)
    print("  PHASE C-DEV SUMMARY RESULTS")
    print("=" * 80)
    for task, rec, diff, sc in results:
        print(f"\nTask: {task.task_id} ({task.title})")
        print(f"  Score:                    {rec.score.value}")
        print(f"  Hidden Tests Passed:      {rec.hidden_tests_passed}")
        print(f"  Strict Scope Compliance:  {not rec.scope_violated}")
        print(f"  Files Touched:            {rec.files_touched}")
        print(f"  Provider Calls:           {rec.provider_calls_count}")
        print(f"  Repair Attempts:          {rec.repair_rounds}")
        print(f"  Native Input Tokens:      {rec.native_input_tokens}")
        print(f"  Native Output Tokens:     {rec.native_output_tokens}")
        print(f"  Native Reasoning Tokens:  {rec.native_reasoning_tokens}")
        print(f"  Fusion Context Tokens:    {rec.fusion_controlled_context_tokens}")
        print(f"  Active Provider Time:     {rec.active_provider_duration_seconds:.2f}s")
        print(f"  Wall Clock Time:          {rec.wall_clock_duration_seconds:.2f}s")
        print(f"  Diff lines:               {len(diff.splitlines()) if diff else 0}")
        if sc.failure_reasons:
            print(f"  Scoring Failure Reasons:  {sc.failure_reasons}")
    print("=" * 80)


if __name__ == "__main__":
    run_dev_eval()
