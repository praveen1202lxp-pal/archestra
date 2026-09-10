"""SQLite storage engine for BenchmarkRunRecord persistence.

Captures complete provenance, execution telemetry, review outcomes,
and token accounting across benchmark runs.
"""

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from benchmarks.schema import BenchmarkRunRecord, BenchmarkScore, SystemUnderTest

DEFAULT_RESULTS_DIR = Path(__file__).parent / "results"
DEFAULT_DB_PATH = DEFAULT_RESULTS_DIR / "benchmark_results.db"


class BenchmarkStorage:
    """Manages SQLite storage for benchmark runs with complete provenance."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Create benchmark_runs schema if not exists."""
        with self._get_connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS benchmark_runs (
                    run_id TEXT PRIMARY KEY,
                    benchmark_suite_version TEXT NOT NULL DEFAULT '1.0.0',
                    benchmark_suite_hash TEXT NOT NULL,
                    task_definition_hash TEXT NOT NULL,
                    hidden_evaluator_hash TEXT NOT NULL DEFAULT '',
                    baseline_snapshot_hash TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    category TEXT NOT NULL,
                    system_under_test TEXT NOT NULL,
                    repetition_index INTEGER NOT NULL DEFAULT 0,
                    
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
                    
                    reviewer_verdict TEXT,
                    reviewer_found_defect INTEGER NOT NULL DEFAULT 0,
                    reviewer_found_valid_defect INTEGER NOT NULL DEFAULT 0,
                    pre_review_criteria_failed_json TEXT NOT NULL DEFAULT '[]',
                    reviewer_mapped_defect_criteria_json TEXT NOT NULL DEFAULT '[]',
                    defect_in_test_passing_patch INTEGER NOT NULL DEFAULT 0,
                    repair_rounds INTEGER NOT NULL DEFAULT 0,
                    repair_successful INTEGER NOT NULL DEFAULT 0,
                    human_promotion_disposition TEXT,
                    
                    fusion_controlled_context_tokens INTEGER,
                    native_input_tokens INTEGER NOT NULL DEFAULT 0,
                    native_output_tokens INTEGER NOT NULL DEFAULT 0,
                    native_reasoning_tokens INTEGER,
                    provider_managed_overhead_residual INTEGER,
                    provider_calls_count INTEGER NOT NULL DEFAULT 0,
                    mcp_calls_count INTEGER NOT NULL DEFAULT 0,
                    
                    recovery_events INTEGER NOT NULL DEFAULT 0,
                    policy_denials INTEGER NOT NULL DEFAULT 0,
                    error_message TEXT
                )
                """
            )
            conn.commit()

    def record_run(self, record: BenchmarkRunRecord) -> None:
        """Insert or replace a benchmark run record."""
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO benchmark_runs (
                    run_id, benchmark_suite_version, benchmark_suite_hash, task_definition_hash,
                    hidden_evaluator_hash, baseline_snapshot_hash, task_id, category,
                    system_under_test, repetition_index,
                    start_time, end_time, wall_clock_duration_seconds, active_provider_duration_seconds,
                    os_platform, python_version, provider_model_id, cli_version,
                    reasoning_effort, fusion_config_hash,
                    score, verification_passed, hidden_tests_passed, regressions_count,
                    files_touched_json, unintended_files_json, git_diff,
                    reviewer_verdict, reviewer_found_defect, reviewer_found_valid_defect,
                    pre_review_criteria_failed_json, reviewer_mapped_defect_criteria_json,
                    defect_in_test_passing_patch, repair_rounds, repair_successful,
                    human_promotion_disposition, fusion_controlled_context_tokens,
                    native_input_tokens, native_output_tokens, native_reasoning_tokens,
                    provider_managed_overhead_residual, provider_calls_count, mcp_calls_count,
                    recovery_events, policy_denials, error_message
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
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
                    record.reviewer_verdict,
                    1 if record.reviewer_found_defect else 0,
                    1 if record.reviewer_found_valid_defect else 0,
                    json.dumps(record.pre_review_criteria_failed),
                    json.dumps(record.reviewer_mapped_defect_criteria),
                    1 if record.defect_in_test_passing_patch else 0,
                    record.repair_rounds,
                    1 if record.repair_successful else 0,
                    record.human_promotion_disposition,
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
                ),
            )
            conn.commit()

    def get_runs(
        self,
        task_id: Optional[str] = None,
        sut: Optional[SystemUnderTest] = None,
    ) -> List[BenchmarkRunRecord]:
        """Query stored benchmark runs with optional filters."""
        query = "SELECT * FROM benchmark_runs WHERE 1=1"
        params: List[Any] = []
        if task_id:
            query += " AND task_id = ?"
            params.append(task_id)
        if sut:
            query += " AND system_under_test = ?"
            params.append(sut.value)
        query += " ORDER BY start_time ASC"

        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            records = []
            for r in rows:
                record = BenchmarkRunRecord(
                    run_id=r["run_id"],
                    benchmark_suite_version=r["benchmark_suite_version"] if "benchmark_suite_version" in r.keys() else "1.0.0",
                    benchmark_suite_hash=r["benchmark_suite_hash"],
                    task_definition_hash=r["task_definition_hash"],
                    hidden_evaluator_hash=r["hidden_evaluator_hash"] if "hidden_evaluator_hash" in r.keys() else "",
                    baseline_snapshot_hash=r["baseline_snapshot_hash"],
                    task_id=r["task_id"],
                    category=r["category"],
                    system_under_test=SystemUnderTest(r["system_under_test"]),
                    repetition_index=r["repetition_index"],
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
                    reviewer_verdict=r["reviewer_verdict"],
                    reviewer_found_defect=bool(r["reviewer_found_defect"]),
                    reviewer_found_valid_defect=bool(r["reviewer_found_valid_defect"]) if "reviewer_found_valid_defect" in r.keys() else False,
                    pre_review_criteria_failed=json.loads(r["pre_review_criteria_failed_json"]) if "pre_review_criteria_failed_json" in r.keys() else [],
                    reviewer_mapped_defect_criteria=json.loads(r["reviewer_mapped_defect_criteria_json"]) if "reviewer_mapped_defect_criteria_json" in r.keys() else [],
                    defect_in_test_passing_patch=bool(r["defect_in_test_passing_patch"]),
                    repair_rounds=r["repair_rounds"],
                    repair_successful=bool(r["repair_successful"]),
                    human_promotion_disposition=r["human_promotion_disposition"],
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
                )
                records.append(record)
            return records
