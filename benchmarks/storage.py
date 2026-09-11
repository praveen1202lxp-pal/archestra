"""SQLite storage engine for BenchmarkRunRecord persistence.

Captures complete provenance, execution telemetry, review outcomes,
token accounting, and validity audit trails across benchmark runs.
"""

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from benchmarks.schema import (
    BenchmarkRunRecord,
    BenchmarkScore,
    RunExecutionStatus,
    SystemUnderTest,
    ValidityDisposition,
)

DEFAULT_RESULTS_DIR = Path(__file__).parent / "results"
DEFAULT_DB_PATH = DEFAULT_RESULTS_DIR / "benchmark_results.db"


class BenchmarkStorage:
    """Manages SQLite storage for benchmark runs with complete provenance and validity audit trail."""

    def __init__(self, db_path: Optional[Any] = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Create benchmark_runs schema if not exists and perform migrations."""
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS benchmark_runs (
                    run_id TEXT PRIMARY KEY,
                    benchmark_suite_version TEXT NOT NULL DEFAULT '1.1.0',
                    benchmark_suite_hash TEXT NOT NULL,
                    task_definition_hash TEXT NOT NULL,
                    hidden_evaluator_hash TEXT NOT NULL DEFAULT '',
                    baseline_snapshot_hash TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    system_under_test TEXT NOT NULL,
                    repetition_index INTEGER NOT NULL DEFAULT 0,

                    run_status TEXT NOT NULL DEFAULT 'COMPLETED',
                    validity_disposition TEXT NOT NULL DEFAULT 'VALID',
                    invalidation_reasons_json TEXT NOT NULL DEFAULT '[]',
                    execution_mode TEXT NOT NULL DEFAULT 'MOCK',
                    experiment_phase TEXT NOT NULL DEFAULT 'PHASE_B_PILOT',
                    
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    wall_clock_duration_seconds REAL NOT NULL DEFAULT 0.0,
                    active_provider_duration_seconds REAL NOT NULL DEFAULT 0.0,
                    os_platform TEXT NOT NULL,
                    python_version TEXT NOT NULL,
                    provider_model_id TEXT,
                    cli_version TEXT,
                    reasoning_effort TEXT,
                    fusion_config_hash TEXT,
                    
                    score TEXT NOT NULL,
                    verification_passed INTEGER NOT NULL DEFAULT 0,
                    hidden_tests_passed INTEGER NOT NULL DEFAULT 0,
                    regressions_count INTEGER NOT NULL DEFAULT 0,
                    files_touched_json TEXT NOT NULL DEFAULT '[]',
                    unintended_files_json TEXT NOT NULL DEFAULT '[]',
                    git_diff TEXT,
                    sut_tree_hash TEXT,
                    sut_candidate_tree_hash TEXT,
                    sut_git_diff TEXT,
                    sut_touched_files_json TEXT NOT NULL DEFAULT '[]',
                    verification_duration_seconds REAL,
                    provider_stages_json TEXT NOT NULL DEFAULT '[]',
                    
                    reviewer_verdict TEXT,
                    reviewer_found_defect INTEGER NOT NULL DEFAULT 0,
                    reviewer_found_valid_defect INTEGER NOT NULL DEFAULT 0,
                    pre_review_criteria_failed_json TEXT NOT NULL DEFAULT '[]',
                    reviewer_mapped_defect_criteria_json TEXT NOT NULL DEFAULT '[]',
                    defect_in_test_passing_patch INTEGER NOT NULL DEFAULT 0,
                    repair_rounds INTEGER NOT NULL DEFAULT 0,
                    repair_successful INTEGER NOT NULL DEFAULT 0,
                    human_promotion_disposition TEXT,
                    pre_review_patch TEXT,
                    pre_review_test_passed INTEGER,
                    reviewer_findings TEXT,
                    repair_patch TEXT,
                    
                    fusion_controlled_context_tokens INTEGER,
                    native_input_tokens INTEGER,
                    native_output_tokens INTEGER,
                    native_reasoning_tokens INTEGER,
                    provider_managed_overhead_residual INTEGER,
                    provider_calls_count INTEGER NOT NULL DEFAULT 0,
                    mcp_calls_count INTEGER NOT NULL DEFAULT 0,
                    
                    recovery_events INTEGER NOT NULL DEFAULT 0,
                    policy_denials INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT,

                    benchmark_harness_commit TEXT,
                    initial_routing_strategy TEXT,
                    initial_routing_snapshot_hash TEXT,
                    scope_violated INTEGER NOT NULL DEFAULT 0,
                    native_cache_read_tokens INTEGER,
                    native_cache_write_tokens INTEGER,
                    container_overhead_seconds REAL,
                    baseline_tree_hash TEXT,
                    candidate_required_files_present_json TEXT NOT NULL DEFAULT '[]',
                    scope_violation_reasons_json TEXT NOT NULL DEFAULT '[]'
                )
                """
            )
            # Automatic schema migration for new audit and state columns
            existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(benchmark_runs);").fetchall()}
            migrations = [
                ("run_status", "TEXT NOT NULL DEFAULT 'COMPLETED'"),
                ("validity_disposition", "TEXT NOT NULL DEFAULT 'VALID'"),
                ("invalidation_reasons_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("execution_mode", "TEXT NOT NULL DEFAULT 'MOCK'"),
                ("experiment_phase", "TEXT NOT NULL DEFAULT 'PHASE_B_PILOT'"),
                ("sut_tree_hash", "TEXT"),
                ("sut_candidate_tree_hash", "TEXT"),
                ("sut_git_diff", "TEXT"),
                ("sut_touched_files_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("verification_duration_seconds", "REAL"),
                ("provider_stages_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("pre_review_patch", "TEXT"),
                ("pre_review_test_passed", "INTEGER"),
                ("reviewer_findings", "TEXT"),
                ("repair_patch", "TEXT"),
                ("benchmark_harness_commit", "TEXT"),
                ("initial_routing_strategy", "TEXT"),
                ("initial_routing_snapshot_hash", "TEXT"),
                ("scope_violated", "INTEGER NOT NULL DEFAULT 0"),
                ("native_cache_read_tokens", "INTEGER"),
                ("native_cache_write_tokens", "INTEGER"),
                ("container_overhead_seconds", "REAL"),
                ("baseline_tree_hash", "TEXT"),
                ("candidate_required_files_present_json", "TEXT NOT NULL DEFAULT '[]'"),
                ("scope_violation_reasons_json", "TEXT NOT NULL DEFAULT '[]'"),
            ]
            for col, col_def in migrations:
                if col not in existing_cols:
                    conn.execute(f"ALTER TABLE benchmark_runs ADD COLUMN {col} {col_def}")
            conn.commit()

    def record_run(self, record: BenchmarkRunRecord) -> None:
        """Insert or replace a benchmark run record."""
        run_status_str = (
            record.run_status.value
            if isinstance(record.run_status, RunExecutionStatus)
            else str(record.run_status)
        )
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO benchmark_runs (
                    run_id, benchmark_suite_version, benchmark_suite_hash,
                    task_definition_hash, hidden_evaluator_hash, baseline_snapshot_hash,
                    task_id, category, system_under_test, repetition_index,
                    run_status, validity_disposition, invalidation_reasons_json,
                    execution_mode, experiment_phase,
                    start_time, end_time, wall_clock_duration_seconds, active_provider_duration_seconds,
                    os_platform, python_version, provider_model_id, cli_version,
                    reasoning_effort, fusion_config_hash,
                    score, verification_passed, hidden_tests_passed, regressions_count,
                    files_touched_json, unintended_files_json, git_diff, sut_tree_hash,
                    sut_candidate_tree_hash, sut_git_diff, sut_touched_files_json,
                    verification_duration_seconds, provider_stages_json,
                    reviewer_verdict, reviewer_found_defect, reviewer_found_valid_defect,
                    pre_review_criteria_failed_json, reviewer_mapped_defect_criteria_json,
                    defect_in_test_passing_patch, repair_rounds, repair_successful,
                    human_promotion_disposition, pre_review_patch, pre_review_test_passed,
                    reviewer_findings, repair_patch, fusion_controlled_context_tokens,
                    native_input_tokens, native_output_tokens, native_reasoning_tokens,
                    provider_managed_overhead_residual, provider_calls_count, mcp_calls_count,
                    recovery_events, policy_denials, error_message,
                    benchmark_harness_commit, initial_routing_strategy, initial_routing_snapshot_hash,
                    scope_violated, native_cache_read_tokens, native_cache_write_tokens,
                    container_overhead_seconds, baseline_tree_hash,
                    candidate_required_files_present_json, scope_violation_reasons_json
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?
                )
                """,
                (
                    record.run_id,
                    record.benchmark_suite_version,
                    record.benchmark_suite_hash,
                    record.task_definition_hash,
                    record.hidden_evaluator_hash,
                    record.baseline_snapshot_hash,
                    record.task_id,
                    record.category,
                    record.system_under_test.value,
                    record.repetition_index,
                    run_status_str,
                    record.validity_disposition,
                    json.dumps(record.invalidation_reasons),
                    record.execution_mode,
                    record.experiment_phase,
                    record.start_time,
                    record.end_time,
                    record.wall_clock_duration_seconds,
                    record.active_provider_duration_seconds,
                    record.os_platform,
                    record.python_version,
                    record.provider_model_id,
                    record.cli_version,
                    record.reasoning_effort,
                    record.fusion_config_hash,
                    record.score.value,
                    1 if record.verification_passed else 0,
                    1 if record.hidden_tests_passed else 0,
                    record.regressions_count,
                    json.dumps(record.files_touched),
                    json.dumps(record.unintended_files),
                    record.git_diff,
                    record.sut_tree_hash,
                    record.sut_candidate_tree_hash or record.sut_tree_hash,
                    record.sut_git_diff or record.git_diff,
                    json.dumps(record.sut_touched_files or record.files_touched),
                    record.verification_duration_seconds,
                    json.dumps(record.provider_stages),
                    record.reviewer_verdict,
                    1 if record.reviewer_found_defect else 0,
                    1 if record.reviewer_found_valid_defect else 0,
                    json.dumps(record.pre_review_criteria_failed),
                    json.dumps(record.reviewer_mapped_defect_criteria),
                    1 if record.defect_in_test_passing_patch else 0,
                    record.repair_rounds,
                    1 if record.repair_successful else 0,
                    record.human_promotion_disposition,
                    record.pre_review_patch,
                    1 if record.pre_review_test_passed is True else (0 if record.pre_review_test_passed is False else None),
                    record.reviewer_findings,
                    record.repair_patch,
                    record.fusion_controlled_context_tokens,
                    record.native_input_tokens,
                    record.native_output_tokens,
                    record.native_reasoning_tokens,
                    record.provider_managed_overhead_residual,
                    record.provider_calls_count,
                    record.mcp_calls_count,
                    record.recovery_events,
                    record.policy_denials,
                    record.error_message,
                    record.benchmark_harness_commit,
                    record.initial_routing_strategy,
                    record.initial_routing_snapshot_hash,
                    1 if record.scope_violated else 0,
                    record.native_cache_read_tokens,
                    record.native_cache_write_tokens,
                    record.container_overhead_seconds,
                    record.baseline_tree_hash,
                    json.dumps(record.candidate_required_files_present),
                    json.dumps(record.scope_violation_reasons),
                ),
            )
            conn.commit()

    def invalidate_run(self, run_id: str, disposition: str, reasons: List[str]) -> bool:
        """Mark an existing run as invalidated with explicit audit reasons."""
        with self._get_connection() as conn:
            cur = conn.execute(
                """
                UPDATE benchmark_runs
                SET validity_disposition = ?, invalidation_reasons_json = ?
                WHERE run_id = ?
                """,
                (disposition, json.dumps(reasons), run_id),
            )
            conn.commit()
            return cur.rowcount > 0

    def get_runs(
        self,
        task_id: Optional[str] = None,
        sut: Optional[SystemUnderTest] = None,
        include_invalidated: bool = False,
        suite_version: Optional[str] = None,
        suite_hash: Optional[str] = None,
        execution_mode: Optional[str] = None,
        experiment_phase: Optional[str] = None,
    ) -> List[BenchmarkRunRecord]:
        """Query stored benchmark runs with audit filters. Defaults to excluding invalidated runs."""
        query = "SELECT * FROM benchmark_runs WHERE 1=1"
        params: List[Any] = []
        if not include_invalidated:
            query += " AND validity_disposition = 'VALID'"
        if task_id:
            query += " AND task_id = ?"
            params.append(task_id)
        if sut:
            query += " AND system_under_test = ?"
            params.append(sut.value)
        if suite_version:
            query += " AND benchmark_suite_version = ?"
            params.append(suite_version)
        if suite_hash:
            query += " AND benchmark_suite_hash = ?"
            params.append(suite_hash)
        if execution_mode:
            query += " AND execution_mode = ?"
            params.append(execution_mode)
        if experiment_phase:
            query += " AND experiment_phase = ?"
            params.append(experiment_phase)
        query += " ORDER BY start_time ASC"

        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            records = []
            for r in rows:
                r_keys = r.keys()
                # Run execution status
                raw_status = r["run_status"] if "run_status" in r_keys else "COMPLETED"
                try:
                    run_status = RunExecutionStatus(raw_status)
                except Exception:
                    run_status = RunExecutionStatus.COMPLETED

                cand_tree = r["sut_candidate_tree_hash"] if ("sut_candidate_tree_hash" in r_keys and r["sut_candidate_tree_hash"]) else (r["sut_tree_hash"] if "sut_tree_hash" in r_keys else None)
                cand_diff = r["sut_git_diff"] if ("sut_git_diff" in r_keys and r["sut_git_diff"]) else (r["git_diff"] if "git_diff" in r_keys else "")
                cand_touched = json.loads(r["sut_touched_files_json"]) if ("sut_touched_files_json" in r_keys and r["sut_touched_files_json"]) else (json.loads(r["files_touched_json"]) if "files_touched_json" in r_keys else [])
                verif_dur = r["verification_duration_seconds"] if ("verification_duration_seconds" in r_keys and r["verification_duration_seconds"] is not None) else None
                stages = json.loads(r["provider_stages_json"]) if ("provider_stages_json" in r_keys and r["provider_stages_json"]) else []

                record = BenchmarkRunRecord(
                    run_id=r["run_id"],
                    benchmark_suite_version=r["benchmark_suite_version"] if "benchmark_suite_version" in r_keys else "1.1.0",
                    benchmark_suite_hash=r["benchmark_suite_hash"],
                    task_definition_hash=r["task_definition_hash"],
                    hidden_evaluator_hash=r["hidden_evaluator_hash"] if "hidden_evaluator_hash" in r_keys else "",
                    baseline_snapshot_hash=r["baseline_snapshot_hash"],
                    task_id=r["task_id"],
                    category=r["category"],
                    system_under_test=SystemUnderTest(r["system_under_test"]),
                    repetition_index=r["repetition_index"],
                    run_status=run_status,
                    validity_disposition=r["validity_disposition"] if "validity_disposition" in r_keys else "VALID",
                    invalidation_reasons=json.loads(r["invalidation_reasons_json"]) if "invalidation_reasons_json" in r_keys else [],
                    execution_mode=r["execution_mode"] if "execution_mode" in r_keys else "MOCK",
                    experiment_phase=r["experiment_phase"] if "experiment_phase" in r_keys else "PHASE_B_PILOT",
                    start_time=r["start_time"],
                    end_time=r["end_time"],
                    wall_clock_duration_seconds=r["wall_clock_duration_seconds"],
                    active_provider_duration_seconds=r["active_provider_duration_seconds"],
                    os_platform=r["os_platform"],
                    python_version=r["python_version"],
                    provider_model_id=r["provider_model_id"],
                    cli_version=r["cli_version"],
                    reasoning_effort=r["reasoning_effort"],
                    fusion_config_hash=r["fusion_config_hash"],
                    score=BenchmarkScore(r["score"]),
                    verification_passed=bool(r["verification_passed"]),
                    hidden_tests_passed=bool(r["hidden_tests_passed"]),
                    regressions_count=r["regressions_count"],
                    files_touched=json.loads(r["files_touched_json"]),
                    unintended_files=json.loads(r["unintended_files_json"]),
                    git_diff=r["git_diff"] or "",
                    sut_tree_hash=r["sut_tree_hash"] if "sut_tree_hash" in r_keys else None,
                    sut_candidate_tree_hash=cand_tree,
                    sut_git_diff=cand_diff,
                    sut_touched_files=cand_touched,
                    verification_duration_seconds=verif_dur,
                    provider_stages=stages,
                    reviewer_verdict=r["reviewer_verdict"],
                    reviewer_found_defect=bool(r["reviewer_found_defect"]),
                    reviewer_found_valid_defect=bool(r["reviewer_found_valid_defect"]) if "reviewer_found_valid_defect" in r_keys else False,
                    pre_review_criteria_failed=json.loads(r["pre_review_criteria_failed_json"]) if "pre_review_criteria_failed_json" in r_keys else [],
                    reviewer_mapped_defect_criteria=json.loads(r["reviewer_mapped_defect_criteria_json"]) if "reviewer_mapped_defect_criteria_json" in r_keys else [],
                    defect_in_test_passing_patch=bool(r["defect_in_test_passing_patch"]),
                    repair_rounds=r["repair_rounds"],
                    repair_successful=bool(r["repair_successful"]),
                    human_promotion_disposition=r["human_promotion_disposition"],
                    pre_review_patch=r["pre_review_patch"] if "pre_review_patch" in r_keys else None,
                    pre_review_test_passed=bool(r["pre_review_test_passed"]) if ("pre_review_test_passed" in r_keys and r["pre_review_test_passed"] is not None) else None,
                    reviewer_findings=r["reviewer_findings"] if "reviewer_findings" in r_keys else None,
                    repair_patch=r["repair_patch"] if "repair_patch" in r_keys else None,
                    fusion_controlled_context_tokens=r["fusion_controlled_context_tokens"],
                    native_input_tokens=r["native_input_tokens"],
                    native_output_tokens=r["native_output_tokens"],
                    native_reasoning_tokens=r["native_reasoning_tokens"],
                    provider_managed_overhead_residual=r["provider_managed_overhead_residual"],
                    provider_calls_count=r["provider_calls_count"],
                    mcp_calls_count=r["mcp_calls_count"],
                    recovery_events=r["recovery_events"],
                    policy_denials=r["policy_denials"],
                    error_message=r["error_message"],
                    benchmark_harness_commit=r["benchmark_harness_commit"] if "benchmark_harness_commit" in r_keys else None,
                    initial_routing_strategy=r["initial_routing_strategy"] if "initial_routing_strategy" in r_keys else None,
                    initial_routing_snapshot_hash=r["initial_routing_snapshot_hash"] if "initial_routing_snapshot_hash" in r_keys else None,
                    scope_violated=bool(r["scope_violated"]) if "scope_violated" in r_keys else False,
                    scope_violation_reasons=json.loads(r["scope_violation_reasons_json"]) if "scope_violation_reasons_json" in r_keys and r["scope_violation_reasons_json"] else [],
                    native_cache_read_tokens=r["native_cache_read_tokens"] if "native_cache_read_tokens" in r_keys else None,
                    native_cache_write_tokens=r["native_cache_write_tokens"] if "native_cache_write_tokens" in r_keys else None,
                    container_overhead_seconds=r["container_overhead_seconds"] if "container_overhead_seconds" in r_keys else None,
                    baseline_tree_hash=r["baseline_tree_hash"] if "baseline_tree_hash" in r_keys else None,
                    candidate_required_files_present=json.loads(r["candidate_required_files_present_json"]) if "candidate_required_files_present_json" in r_keys and r["candidate_required_files_present_json"] else [],
                )
                records.append(record)
            return records
