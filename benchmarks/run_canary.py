"""Milestone 11 — Phase C Frozen-Model Infrastructure Canary.

Executes exactly one non-scored canary on development task TASK-04 across
all three Systems Under Test (CODEX_ALONE, ANTIGRAVITY_ALONE, FUSION).
Records all execution and isolation telemetry into benchmarks/results/canary_results.db.
"""

import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from benchmarks.adapters.agy_adapter import AntigravityAloneAdapter
from benchmarks.adapters.codex_adapter import CodexAloneAdapter
from benchmarks.adapters.fusion_adapter import FusionSUTAdapter
from benchmarks.evaluators import BenchmarkEvaluator
from benchmarks.isolation import DisposableBenchmarkEnvironment
from benchmarks.schema import (
    BenchmarkRunRecord,
    BenchmarkScore,
    RunExecutionStatus,
    SystemUnderTest,
    ValidityDisposition,
)
from benchmarks.storage import BenchmarkStorage
from benchmarks.tasks.catalog import (
    BENCHMARK_SUITE_VERSION,
    compute_benchmark_suite_hash,
    compute_hidden_evaluator_hash,
    compute_task_definition_hash,
    get_task_by_id,
)

AUTH_SEED_EXPECTED_DIGEST = "7faefcc101341123da84bb46e9223fb71021f502d899e3ca0d4fc281362bbb80"
FUSION_ROUTING_SNAPSHOT_HASH = "4ad58f2641066b1b6da7f9145ee1c1aae9afa5e3d56aa48c49e0fb435df32102"


def get_git_commit(cwd: Path) -> str:
    """Retrieve full git HEAD commit SHA."""
    res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(cwd), capture_output=True, text=True, check=True)
    return res.stdout.strip()


def compute_auth_seed_digest() -> str:
    """Compute deterministic SHA-256 digest of AUTH_SEED docker volume files."""
    res = subprocess.run(
        [
            "wsl", "-u", "root", "docker", "run", "--rm",
            "-v", "antigravity_benchmark_auth:/seed:ro",
            "python:3.11-slim", "sh", "-c",
            "find /seed -type f -exec sha256sum {} + | sort | sha256sum",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return res.stdout.strip().split()[0]


def get_docker_image_id(image_tag: str) -> str:
    """Get exact image ID for docker container."""
    res = subprocess.run(
        ["wsl", "-u", "root", "docker", "inspect", "--format={{.Id}}", image_tag],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    return res.stdout.strip()


def run_phase_c_canary():
    """Execute the Phase C frozen-model canary on TASK-04."""
    print("=" * 80)
    print("ARCHESTRA BENCHMARK HARNESS — PHASE C FROZEN-MODEL INFRASTRUCTURE CANARY")
    print("=" * 80)

    # 1. Verify Git cleanliness and product commit
    repo_root = Path(__file__).resolve().parent.parent
    current_commit = get_git_commit(repo_root)
    print(f"[*] Harness Working Directory: {repo_root}")
    print(f"[*] Current Git Commit:        {current_commit}")

    # Check git status
    status_proc = subprocess.run(["git", "status", "--porcelain"], cwd=str(repo_root), capture_output=True, text=True)
    if status_proc.stdout.strip():
        print(f"[!] Warning: Working tree is not completely clean before canary:\n{status_proc.stdout}")

    # 2. Canonical Phase C Pinned Model Configuration
    CODEX_PINNED_MODEL = "gpt-5.6-sol"
    CODEX_PINNED_EFFORT = "medium"
    AGY_PINNED_MODEL = "gemini-3.8-flash-high"
    AGY_PINNED_EFFORT = "high"

    print("\n[*] CANONICAL FROZEN PROVIDER CONFIGURATION:")
    print(f"  Codex Model:       {CODEX_PINNED_MODEL} | Effort: {CODEX_PINNED_EFFORT}")
    print(f"  Antigravity Model: {AGY_PINNED_MODEL} | Effort: {AGY_PINNED_EFFORT}")

    # 3. Check AUTH_SEED content digest before canary
    print("\n[*] Verifying immutable AUTH_SEED volume digest...")
    seed_digest_before = compute_auth_seed_digest()
    print(f"  AUTH_SEED Digest:  {seed_digest_before}")
    if seed_digest_before != AUTH_SEED_EXPECTED_DIGEST:
        print(f"[!] FATAL: AUTH_SEED digest mismatch! Expected {AUTH_SEED_EXPECTED_DIGEST}, got {seed_digest_before}")
        sys.exit(1)

    # 4. Check Docker image ID
    agy_image_id = get_docker_image_id("antigravity-benchmark:1.2.0")
    print(f"  AGY Docker Image:  {agy_image_id}")

    # 5. Load development task TASK-04
    task_04 = get_task_by_id("TASK-04")
    if not task_04:
        print("[!] FATAL: Could not find TASK-04 in benchmark catalog!")
        sys.exit(1)

    task_hash = compute_task_definition_hash(task_04)
    hidden_hash = compute_hidden_evaluator_hash(task_04)
    with DisposableBenchmarkEnvironment(task_id="TASK-04", run_id="probe_04") as probe_env:
        baseline_hash = probe_env.baseline_snapshot_hash
        baseline_tree_hash = probe_env.baseline_tree_hash
    suite_hash = compute_benchmark_suite_hash()

    print(f"\n[*] Target Task: TASK-04 (Multi-File Feature: LRUCache integration)")
    print(f"  Task Definition Hash: {task_hash}")
    print(f"  Hidden Evaluator Hash: {hidden_hash}")
    print(f"  Baseline Hash:         {baseline_hash}")
    print(f"  Baseline Tree Hash:    {baseline_tree_hash}")
    print(f"  Suite Hash:            {suite_hash}")

    # 6. Initialize dedicated canary database
    results_dir = repo_root / "benchmarks" / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    canary_db_path = results_dir / "canary_results.db"
    canary_storage = BenchmarkStorage(db_path=canary_db_path)
    evaluator = BenchmarkEvaluator()

    # Define adapters with pinned model configurations
    codex_adapter = CodexAloneAdapter(
        is_live=True,
        reasoning_effort=CODEX_PINNED_EFFORT,
        model_id=CODEX_PINNED_MODEL,
    )
    agy_adapter = AntigravityAloneAdapter(
        is_live=True,
        reasoning_effort=AGY_PINNED_EFFORT,
        model_id=AGY_PINNED_MODEL,
    )
    fusion_adapter = FusionSUTAdapter(
        is_live=True,
        codex_model_id=CODEX_PINNED_MODEL,
        agy_model_id=AGY_PINNED_MODEL,
    )

    planned_canaries = [
        (SystemUnderTest.CODEX_ALONE, codex_adapter, CODEX_PINNED_MODEL, CODEX_PINNED_EFFORT),
        (SystemUnderTest.ANTIGRAVITY_ALONE, agy_adapter, AGY_PINNED_MODEL, AGY_PINNED_EFFORT),
        (SystemUnderTest.FUSION, fusion_adapter, f"{CODEX_PINNED_MODEL}+{AGY_PINNED_MODEL}", f"codex:{CODEX_PINNED_EFFORT},agy:{AGY_PINNED_EFFORT}"),
    ]

    canary_reports = []
    all_healthy = True

    for idx, (sut, adapter, req_model, req_effort) in enumerate(planned_canaries, start=1):
        run_id = f"canary_TASK-04_{sut.value}_{int(time.time())}"
        print(f"\n[{idx}/3] EXECUTING NON-SCORED CANARY: SUT={sut.value} | RunID={run_id}")
        print(f"  Requested Model:  {req_model}")
        print(f"  Requested Effort: {req_effort}")

        if sut == SystemUnderTest.CODEX_ALONE:
            cmd_repr = f"codex exec --approve-for-me --model {req_model} -c model_reasoning_effort=\"{req_effort}\" --json -"
        elif sut == SystemUnderTest.ANTIGRAVITY_ALONE:
            cmd_repr = f"docker run --rm -i -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0=* -v <wsl_repo>:/workspace:rw -v <disposable_vol>:/home/agy/.gemini:rw -w /workspace antigravity-benchmark:1.2.0 --dangerously-skip-permissions --mode accept-edits --effort {req_effort} --model {req_model} --output-format json -p <prompt>"
        else:
            cmd_repr = f"Fusion Orchestrated: [Codex Stage: codex exec --ephemeral --skip-git-repo-check -s read-only --json - --model {CODEX_PINNED_MODEL} -c model_reasoning_effort=\"{CODEX_PINNED_EFFORT}\"] | [Antigravity Stage: agy -p <prompt> --output-format json --model {AGY_PINNED_MODEL} --effort {AGY_PINNED_EFFORT}]"
        print(f"  CLI Representation (sanitized): {cmd_repr}")

        start_utc = datetime.now(timezone.utc).isoformat()
        t_start = time.time()
        infra_warnings = []

        # Fresh isolated disposable baseline environment
        with DisposableBenchmarkEnvironment(task_id=task_04.task_id, run_id=run_id) as env:
            # Baseline snapshot validation
            if env.baseline_snapshot_hash != baseline_hash:
                err = f"Baseline snapshot hash mismatch: expected {baseline_hash}, got {env.baseline_snapshot_hash}"
                print(f"  [!] {err}")
                infra_warnings.append(err)
                all_healthy = False

            if env.baseline_tree_hash != baseline_tree_hash:
                err = f"Baseline tree hash mismatch: expected {baseline_tree_hash}, got {env.baseline_tree_hash}"
                print(f"  [!] {err}")
                infra_warnings.append(err)
                all_healthy = False

            # Verify no hidden test leak
            if (env.repo_path / "tests" / "_hidden_eval.py").exists():
                err = "Hidden evaluator leaked into baseline repo"
                print(f"  [!] {err}")
                infra_warnings.append(err)
                all_healthy = False

            # Model execution
            print(f"  Launching {sut.value} execution on isolated repository...")
            telemetry = adapter.execute(task_04, env.repo_path)
            print(f"  Execution finished in {telemetry.wall_clock_duration_seconds:.2f}s. Active provider time: {telemetry.active_provider_duration_seconds:.2f}s")

            # 1. Capture and freeze authoritative candidate state BEFORE hidden evaluator is materialized
            files_touched, git_diff, candidate_tree_hash = env.freeze_sut_candidate_state()
            req_present = [p for p in task_04.required_paths if (env.repo_path / p).is_file()]
            req_missing = [p for p in task_04.required_paths if not (env.repo_path / p).is_file()]
            print(f"  Authoritative candidate frozen. Tree: {candidate_tree_hash} | Touched files ({len(files_touched)}): {files_touched}")
            print(f"  Required files present: {req_present} | Missing: {req_missing}")

            # 2. DEV functional & regression evaluation on authoritative frozen candidate
            print("  Running DEV functional, hidden, and regression evaluation...")
            scoring = evaluator.evaluate_task(
                task=task_04,
                repo_path=env.repo_path,
                files_touched=files_touched,
                baseline_tree_hash=env.baseline_tree_hash,
                candidate_tree_hash=candidate_tree_hash,
            )
            print(f"  Visible Tests Passed: {scoring.task_tests_passed} | Hidden Passed: {scoring.hidden_tests_passed} | Regressions: {scoring.regressions_count} | Scope Violated: {scoring.scope_violated}")
            if scoring.scope_violation_reasons:
                print(f"  Scope Violation Reasons: {scoring.scope_violation_reasons}")

            # 3. Verify hidden test was unlinked
            if (env.repo_path / "tests" / "_hidden_eval.py").exists():
                err = "Hidden evaluator remained in workspace after evaluation"
                print(f"  [!] {err}")
                infra_warnings.append(err)
                all_healthy = False

            # 4. Prove hidden evaluation operated strictly against frozen candidate tree
            _, _, post_eval_tree_hash = env.freeze_sut_candidate_state()
            if post_eval_tree_hash != candidate_tree_hash:
                err = f"Post-evaluation tree hash mismatch! Expected {candidate_tree_hash}, got {post_eval_tree_hash}"
                print(f"  [!] {err}")
                infra_warnings.append(err)
                all_healthy = False
            else:
                print(f"  Candidate Tree Provenance Proven: Post-eval tree matches frozen candidate ({candidate_tree_hash})")

        end_utc = datetime.now(timezone.utc).isoformat()
        wall_time = time.time() - t_start

        # Execution status
        if telemetry.error_message:
            if "timeout" in telemetry.error_message.lower():
                run_status = RunExecutionStatus.TIMEOUT
            elif any(x in telemetry.error_message.lower() for x in ["auth", "unauthenticated", "not logged in"]):
                run_status = RunExecutionStatus.AUTH_FAILURE
                all_healthy = False
            elif any(x in telemetry.error_message.lower() for x in ["rate limit", "transport", "connection"]):
                run_status = RunExecutionStatus.INFRASTRUCTURE_FAILURE
                all_healthy = False
            else:
                run_status = RunExecutionStatus.COMPLETED
        else:
            run_status = RunExecutionStatus.COMPLETED

        container_overhead = (
            max(0.0, telemetry.wall_clock_duration_seconds - telemetry.active_provider_duration_seconds)
            if sut == SystemUnderTest.ANTIGRAVITY_ALONE
            else None
        )

        initial_routing_strategy = "CODEX_HEAVY" if sut == SystemUnderTest.FUSION else None

        # Build record: marked explicitly as execution_mode="CANARY" and score is not stored as held-out
        record = BenchmarkRunRecord(
            run_id=run_id,
            benchmark_suite_version=BENCHMARK_SUITE_VERSION,
            benchmark_suite_hash=suite_hash,
            task_definition_hash=task_hash,
            hidden_evaluator_hash=hidden_hash,
            baseline_snapshot_hash=baseline_hash,
            baseline_tree_hash=baseline_tree_hash,
            task_id=task_04.task_id,
            category=task_04.category.value,
            system_under_test=sut,
            repetition_index=0,
            run_status=run_status,
            validity_disposition=ValidityDisposition.VALID.value,
            execution_mode="CANARY",
            experiment_phase="PHASE_C_CANARY",
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
            benchmark_harness_commit=current_commit,
            initial_routing_strategy=initial_routing_strategy,
            initial_routing_snapshot_hash=FUSION_ROUTING_SNAPSHOT_HASH if sut == SystemUnderTest.FUSION else None,
            score=scoring.score,
            verification_passed=scoring.task_tests_passed,
            hidden_tests_passed=scoring.hidden_tests_passed,
            regressions_count=scoring.regressions_count,
            files_touched=files_touched,
            unintended_files=scoring.unintended_files,
            scope_violated=scoring.scope_violated,
            scope_violation_reasons=scoring.scope_violation_reasons,
            git_diff=git_diff,
            sut_candidate_tree_hash=candidate_tree_hash,
            sut_tree_hash=candidate_tree_hash,
            sut_git_diff=git_diff,
            sut_touched_files=files_touched,
            candidate_required_files_present=req_present,
            native_input_tokens=telemetry.native_input_tokens,
            native_output_tokens=telemetry.native_output_tokens,
            native_reasoning_tokens=telemetry.native_reasoning_tokens,
            fusion_controlled_context_tokens=telemetry.fusion_controlled_context_tokens,
            provider_calls_count=telemetry.provider_calls_count,
            mcp_calls_count=telemetry.mcp_calls_count,
            reviewer_verdict=telemetry.reviewer_verdict,
            reviewer_found_defect=telemetry.reviewer_found_defect,
            defect_in_test_passing_patch=telemetry.defect_in_test_passing_patch,
            repair_rounds=telemetry.repair_rounds,
            repair_successful=telemetry.repair_successful,
            human_promotion_disposition=telemetry.human_promotion_disposition,
            pre_review_patch=telemetry.pre_review_patch,
            pre_review_test_passed=telemetry.pre_review_test_passed,
            reviewer_findings=telemetry.reviewer_findings,
            repair_patch=telemetry.repair_patch,
            recovery_events=telemetry.recovery_events,
            policy_denials=telemetry.policy_denials,
            error_message=telemetry.error_message,
        )

        canary_storage.record_run(record)
        print(f"  Saved canary run record to {canary_db_path}")

        # Check disposable volume destruction
        if sut == SystemUnderTest.ANTIGRAVITY_ALONE:
            time.sleep(1.0)
            vol_check = subprocess.run(
                ["wsl", "-u", "root", "docker", "volume", "ls", "--format={{.Name}}"],
                capture_output=True,
                text=True,
            )
            agy_vols = [v for v in vol_check.stdout.splitlines() if v.startswith("agy_trial_auth_")]
            if agy_vols:
                err = f"Disposable auth volumes not destroyed: {agy_vols}"
                print(f"  [!] {err}")
                infra_warnings.append(err)
                all_healthy = False

        canary_reports.append({
            "sut": sut.value,
            "run_id": run_id,
            "baseline_tree_hash": env.baseline_tree_hash,
            "candidate_tree_hash": candidate_tree_hash,
            "exact_candidate_diff": git_diff,
            "touched_files": files_touched,
            "required_files_present": req_present,
            "required_files_missing": req_missing,
            "scope_violation_reasons": scoring.scope_violation_reasons,
            "visible_result": "PASS" if scoring.task_tests_passed else "FAIL",
            "hidden_result": "PASS" if scoring.hidden_tests_passed else "FAIL",
            "regression_result": "PASS" if scoring.regressions_passed else "FAIL",
            "overall_score": scoring.score.value,
            "requested_model_id": req_model,
            "requested_effort": req_effort,
            "cli_command_representation": cmd_repr,
            "routing_strategy": initial_routing_strategy or "N/A",
            "provider_stages": getattr(telemetry, "provider_stages", ["direct"]),
            "provider_calls": telemetry.provider_calls_count,
            "execution_status": run_status.value,
            "input_tokens": telemetry.native_input_tokens if telemetry.native_input_tokens is not None else "NULL",
            "output_tokens": telemetry.native_output_tokens if telemetry.native_output_tokens is not None else "NULL",
            "reasoning_tokens": telemetry.native_reasoning_tokens if telemetry.native_reasoning_tokens is not None else "NULL",
            "active_provider_time": f"{telemetry.active_provider_duration_seconds:.2f}s",
            "harness_container_overhead": f"{container_overhead:.2f}s" if container_overhead is not None else "0.00s",
            "wall_time": f"{telemetry.wall_clock_duration_seconds:.2f}s",
            "tree_integrity_proven": (post_eval_tree_hash == candidate_tree_hash),
            "infra_warnings": infra_warnings,
        })

    # 7. Check AUTH_SEED content digest after all canary runs
    print("\n[*] Verifying immutable AUTH_SEED volume digest after all canary runs...")
    seed_digest_after = compute_auth_seed_digest()
    print(f"  AUTH_SEED Digest Before: {seed_digest_before}")
    print(f"  AUTH_SEED Digest After:  {seed_digest_after}")
    if seed_digest_after != seed_digest_before:
        print("[!] FATAL: AUTH_SEED content digest changed during canary run!")
        all_healthy = False

    # 8. Print comprehensive report table
    print("\n" + "=" * 80)
    print("PHASE C CANARY EXECUTION SUMMARY")
    print("=" * 80)
    print(json.dumps(canary_reports, indent=2))
    print("=" * 80)
    print(f"CANARY_INFRASTRUCTURE_READY = {'YES' if all_healthy else 'NO'}")
    print("=" * 80)


if __name__ == "__main__":
    run_phase_c_canary()
