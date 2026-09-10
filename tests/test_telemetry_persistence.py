"""Tests for Milestone 8 Telemetry & Lifecycle Persistence Corrective Pass.

Verifies:
1. Every provider invocation produces exactly one persisted stage record.
2. Planner telemetry persists (plan_generation and plan_repair).
3. Final repair telemetry persists.
4. Both review rounds persist native usage and context metrics.
5. Fusion context metrics persist (chars, tokens, selected files, selected symbols).
6. Step and final verification runs persist in the verifications table.
7. Successful plan ends in COMPLETED status (not PREPARED).
8. --no-promote records promotion disposition DECLINED.
9. Zero duplicate agent_runs records.
10. Unavailable native metrics persist as NULL, not fabricated zeroes.
11. Database migration idempotency across fresh, pre-M8, and M8 databases.
"""

import json
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

from fusion_agent.config.loader import ConfigLoader, DeliberationConfig, FusionConfig
from fusion_agent.core.orchestrator import FusionOrchestrator
from fusion_agent.memory.database import Database
from fusion_agent.models.deliberation import ReviewResult, ReviewStatus
from fusion_agent.models.plan import ExecutionPlan, PlanStatus, PlanStep, StepResult, StepStatus, VerificationType
from fusion_agent.models.task import Complexity, PromotionDisposition, Task, TaskStatus, TaskType
from fusion_agent.providers.base import ReviewResponse
from fusion_agent.providers.mock import MockProvider


@pytest.fixture
def clean_git_project(tmp_path: Path) -> Path:
    """Initialize a git repository with an initial commit for isolated testing."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text("def add(a, b): return a + b\n", encoding="utf-8")

    subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@test.local", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@test.local", "commit", "-m", "Initial commit"], cwd=str(repo), check=True, capture_output=True)
    return repo


def test_migration_fresh_database(tmp_path: Path):
    """Test migration against a fresh database."""
    db_path = tmp_path / "fresh" / "fusion.db"
    db = Database(db_path)
    conn = db.connect()

    # Verify tables
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
    assert "schema_version" in tables
    assert "tasks" in tables
    assert "agent_runs" in tables
    assert "plans" in tables
    assert "plan_steps" in tables
    assert "checkpoints" in tables
    assert "verifications" in tables

    # Verify schema version is at least 3 (now 4 in Milestone 9)
    ver = conn.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1;").fetchone()
    assert ver[0] >= 3

    # Verify columns in agent_runs
    cols = [r[1] for r in conn.execute("PRAGMA table_info(agent_runs);").fetchall()]
    for expected in (
        "plan_id", "step_id", "stage", "round_number",
        "reasoning_tokens", "cached_tokens", "visible_output_tokens", "raw_usage",
        "fusion_context_chars", "fusion_context_tokens", "selected_file_count",
        "selected_files", "selected_symbols", "context_expansion_round",
    ):
        assert expected in cols, f"Missing column {expected} in agent_runs"

    # Verify columns in tasks
    task_cols = [r[1] for r in conn.execute("PRAGMA table_info(tasks);").fetchall()]
    for expected in ("promotion_disposition", "active_stage", "last_checkpoint_sha"):
        assert expected in task_cols, f"Missing column {expected} in tasks"

    db.close()


def test_migration_pre_m8_database(tmp_path: Path):
    """Test idempotent migration against an existing pre-M8 database (v1)."""
    db_path = tmp_path / "pre_m8" / "fusion.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Manually create pre-M8 schema
    raw_conn = sqlite3.connect(str(db_path))
    raw_conn.execute("""
        CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT);
    """)
    raw_conn.execute("INSERT INTO schema_version VALUES (1, CURRENT_TIMESTAMP);")
    raw_conn.execute("""
        CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, root_path TEXT NOT NULL);
    """)
    raw_conn.execute("""
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, title TEXT NOT NULL,
            description TEXT, status TEXT NOT NULL, task_type TEXT NOT NULL,
            complexity TEXT NOT NULL, selected_strategy TEXT, created_at TEXT
        );
    """)
    raw_conn.execute("""
        CREATE TABLE agent_runs (
            id TEXT PRIMARY KEY, task_id TEXT NOT NULL, provider_name TEXT NOT NULL,
            role TEXT NOT NULL, prompt_summary TEXT, response_content TEXT NOT NULL,
            input_tokens INTEGER, output_tokens INTEGER, duration_ms REAL,
            status TEXT NOT NULL, timestamp TEXT
        );
    """)
    raw_conn.execute("""
        CREATE TABLE reviews (
            id TEXT PRIMARY KEY, task_id TEXT NOT NULL, reviewer_provider TEXT NOT NULL,
            subject_agent TEXT NOT NULL, status TEXT NOT NULL, comments TEXT, timestamp TEXT
        );
    """)
    raw_conn.execute("""
        CREATE TABLE decisions (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, task_id TEXT, title TEXT NOT NULL,
            decision TEXT NOT NULL, rationale TEXT, agent_source TEXT, timestamp TEXT
        );
    """)
    # Insert sample pre-M8 task and run
    raw_conn.execute("INSERT INTO projects VALUES ('proj1', 'Project 1', '.');")
    raw_conn.execute("INSERT INTO tasks VALUES ('task-old', 'proj1', 'Old Task', 'Desc', 'COMPLETED', 'FEATURE', 'SIMPLE', 'DIRECT', '2026-01-01');")
    raw_conn.execute("INSERT INTO agent_runs VALUES ('run-old', 'task-old', 'MockProv', 'coder', 'prompt', 'code', 100, 50, 10.0, 'SUCCESS', '2026-01-01');")
    raw_conn.commit()
    raw_conn.close()

    # Now open with Database to run migrations
    db = Database(db_path)
    conn = db.connect()

    # Verify migration applied
    ver = conn.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1;").fetchone()
    assert ver[0] >= 3

    # Verify old data survived
    old_task = conn.execute("SELECT * FROM tasks WHERE id = 'task-old';").fetchone()
    assert old_task["title"] == "Old Task"
    assert old_task["promotion_disposition"] in (None, "NOT_OFFERED")

    old_run = conn.execute("SELECT * FROM agent_runs WHERE id = 'run-old';").fetchone()
    assert old_run["input_tokens"] == 100
    assert old_run["output_tokens"] == 50
    assert old_run["reasoning_tokens"] is None
    assert old_run["stage"] is None

    # Verify new tables exist
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
    assert "plans" in tables
    assert "plan_steps" in tables
    assert "checkpoints" in tables
    assert "verifications" in tables

    # Running init_schema() again must be completely idempotent
    db.init_schema()
    ver2 = conn.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1;").fetchone()
    assert ver2[0] >= 3

    db.close()


def test_migration_m8_database(tmp_path: Path):
    """Test idempotent migration against an existing early M8 database (v2)."""
    db_path = tmp_path / "m8" / "fusion.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    raw_conn = sqlite3.connect(str(db_path))
    raw_conn.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY, applied_at TEXT);")
    raw_conn.execute("INSERT INTO schema_version VALUES (2, CURRENT_TIMESTAMP);")
    raw_conn.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, root_path TEXT NOT NULL);")
    raw_conn.execute("""
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, title TEXT NOT NULL,
            description TEXT, status TEXT NOT NULL, task_type TEXT NOT NULL,
            complexity TEXT NOT NULL, selected_strategy TEXT, verification_passed INTEGER,
            repair_rounds INTEGER, created_at TEXT
        );
    """)
    raw_conn.execute("""
        CREATE TABLE agent_runs (
            id TEXT PRIMARY KEY, task_id TEXT NOT NULL, provider_name TEXT NOT NULL,
            role TEXT NOT NULL, prompt_summary TEXT, response_content TEXT NOT NULL,
            input_tokens INTEGER, output_tokens INTEGER, duration_ms REAL,
            status TEXT NOT NULL, timestamp TEXT
        );
    """)
    raw_conn.execute("""
        CREATE TABLE plans (
            id TEXT PRIMARY KEY, task_id TEXT NOT NULL, title TEXT NOT NULL,
            summary TEXT, status TEXT NOT NULL, max_steps INTEGER, amendments_count INTEGER,
            created_at TEXT, updated_at TEXT
        );
    """)
    raw_conn.execute("""
        CREATE TABLE plan_steps (
            id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, step_index INTEGER NOT NULL,
            objective TEXT NOT NULL, rationale TEXT, expected_files TEXT, expected_symbols TEXT,
            dependencies TEXT, verification_expectations TEXT, risk_level TEXT NOT NULL,
            estimated_complexity TEXT NOT NULL, status TEXT NOT NULL, provider_name TEXT,
            repair_rounds INTEGER, created_at TEXT, completed_at TEXT
        );
    """)
    raw_conn.execute("""
        CREATE TABLE checkpoints (
            id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, task_id TEXT NOT NULL,
            step_id TEXT NOT NULL, commit_sha TEXT NOT NULL, base_commit_sha TEXT NOT NULL,
            files_changed TEXT, diff_summary TEXT, verification_passed INTEGER,
            provider_name TEXT, input_tokens INTEGER, output_tokens INTEGER,
            fusion_context_tokens INTEGER, duration_ms REAL, created_at TEXT
        );
    """)
    raw_conn.execute("INSERT INTO projects VALUES ('p1', 'P1', '.');")
    raw_conn.execute("INSERT INTO tasks VALUES ('t1', 'p1', 'T1', 'D', 'COMPLETED', 'FEATURE', 'COMPLEX', 'CHECKPOINTED_PLAN', 1, 0, '2026-01-01');")
    raw_conn.execute("INSERT INTO plans VALUES ('plan-1', 't1', 'Plan 1', 'Summary', 'PREPARED', 2, 0, '2026-01-01', '2026-01-01');")
    raw_conn.execute("INSERT INTO plan_steps VALUES ('step-1', 'plan-1', 0, 'Obj', 'Rat', '[\"a.py\"]', '[]', '[]', 'None', 'LOW', 'SIMPLE', 'COMPLETED', 'Prov', 0, '2026-01-01', '2026-01-01');")
    raw_conn.commit()
    raw_conn.close()

    # Migrate to v3
    db = Database(db_path)
    conn = db.connect()

    ver = conn.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1;").fetchone()
    assert ver[0] >= 3

    # Check that plan_steps composite primary key is working
    plan_steps_info = conn.execute("PRAGMA table_info(plan_steps);").fetchall()
    pk_cols = [r[1] for r in plan_steps_info if r[5] > 0]
    assert "plan_id" in pk_cols and "id" in pk_cols

    # Verify old data survived
    step_row = conn.execute("SELECT * FROM plan_steps WHERE plan_id = 'plan-1' AND id = 'step-1';").fetchone()
    assert step_row is not None
    assert step_row["objective"] == "Obj"

    # Verify verifications table was created
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
    assert "verifications" in tables

    db.close()


def test_full_multi_step_telemetry_lifecycle_and_verification_persistence(clean_git_project: Path):
    """Verify that an end-to-end multi-step task with repair and multiple review rounds:
    1. Persists EVERY provider invocation exactly once.
    2. Persists planner telemetry (plan_generation).
    3. Persists final repair telemetry.
    4. Persists both review rounds with native usage.
    5. Persists Fusion context metrics across all stages.
    6. Persists step and final verification runs.
    7. Sets plan status to COMPLETED (not PREPARED).
    8. Sets promotion disposition to DECLINED under --no-promote.
    9. Produces zero duplicate agent_runs records.
    10. Stores unavailable metrics as NULL, never fabricated zeroes.
    """
    db = Database(clean_git_project / ".fusion" / "fusion.db")
    config = FusionConfig(
        project_name="TestProject",
        project_root=str(clean_git_project),
        verification_command="python -c \"import sys; sys.exit(0)\"",
        deliberation=DeliberationConfig(
            allow_multi_step_planning=True,
            max_provider_calls=15,
            max_repair_rounds=2,
        ),
    )

    plan_json = json.dumps({
        "title": "Provider Performance Telemetry Feature",
        "summary": "Implement schema, service, and verification.",
        "steps": [
            {
                "id": "step-1",
                "objective": "Add performance schema",
                "expected_files": ["perf_schema.py"],
                "dependencies": [],
            },
            {
                "id": "step-2",
                "objective": "Add performance recorder",
                "expected_files": ["perf_recorder.py"],
                "dependencies": ["step-1"],
            },
        ],
    })

    call_log = []

    def planner_handler(prompt, context=None):
        call_log.append("planner")
        return plan_json

    def implementer_handler(prompt, context=None):
        call_log.append("implementer")
        if "final repair" in prompt.lower() or "consolidated repair" in prompt.lower() or "repair" in prompt.lower():
            # Final repair response
            return "### File: perf_recorder.py\n```python\nfrom perf_schema import SCHEMA\nRECORDER = 'repaired_v2'\n```"
        if "step-1" in prompt:
            return "### File: perf_schema.py\n```python\nSCHEMA = 'perf_v1'\n```"
        # Step-2 initial implementation
        return "### File: perf_recorder.py\n```python\nfrom perf_schema import SCHEMA\nRECORDER = 'v1'\n```"

    review_call_count = [0]

    def reviewer_handler(*args, **kwargs):
        review_call_count[0] += 1
        call_log.append(f"reviewer_round_{review_call_count[0]}")
        if review_call_count[0] == 1:
            # Round 1 rejects with NEEDS_REVISION to trigger final consolidated repair
            return ReviewResponse(
                status=ReviewStatus.NEEDS_REVISION,
                comments="Please update RECORDER to repaired_v2.",
                input_tokens=150,
                output_tokens=75,
                reasoning_tokens=25,
                cached_tokens=10,
                visible_output_tokens=50,
            )
        # Round 2 approves
        return ReviewResponse(
            status=ReviewStatus.APPROVED,
            comments="Approved after repair.",
            input_tokens=160,
            output_tokens=40,
            reasoning_tokens=15,
            cached_tokens=20,
            visible_output_tokens=30,
        )

    mock_planner = MockProvider(name="PlannerMock")
    mock_planner.response_generator = planner_handler
    mock_impl = MockProvider(name="ImplMock")
    mock_impl.response_generator = implementer_handler
    mock_rev = MockProvider(name="ReviewerMock")
    mock_rev.review_generator = reviewer_handler

    providers = {
        "planner": mock_planner,
        "implementer": mock_impl,
        "reviewer": mock_rev,
    }

    orchestrator = FusionOrchestrator(config=config, database=db, providers=providers)
    user_prompt = "Implement multi-step feature: extend persistent schema and add a query service and tests for provider performance."

    # Execute task
    result = orchestrator.run_task(user_prompt=user_prompt)

    # Emulate CLI --no-promote rejection
    orchestrator.state_manager.update_task_promotion(result.task.id, PromotionDisposition.DECLINED)

    # 1. Total provider calls in test:
    # 1 (plan_generation) + 1 (step 1) + 1 (step 2) + 1 (review round 1) + 1 (final repair) + 1 (review round 2) = 6 calls
    assert len(call_log) == 6

    conn = db.connect()

    # Query all agent_runs for this task
    runs = conn.execute(
        "SELECT * FROM agent_runs WHERE task_id = ? ORDER BY timestamp ASC;",
        (result.task.id,),
    ).fetchall()

    # Exact count matching: Every provider call produces exactly one persisted stage record
    assert len(runs) == 6, f"Expected exactly 6 agent_runs records, got {len(runs)}"

    stages = [r["stage"] for r in runs]
    assert stages == [
        "plan_generation",
        "step_implementation",
        "step_implementation",
        "final_review",
        "final_repair",
        "final_rereview",
    ]

    # Verify structured stage identity
    run_plan = runs[0]
    assert run_plan["stage"] == "plan_generation"
    assert run_plan["role"] == "planner"
    assert run_plan["plan_id"] is not None
    assert run_plan["provider_name"] == "PlannerMock"
    assert run_plan["round_number"] == 0
    assert run_plan["status"] == "SUCCESS"
    assert run_plan["fusion_context_chars"] is not None and run_plan["fusion_context_chars"] > 0
    assert run_plan["fusion_context_tokens"] is not None and run_plan["fusion_context_tokens"] > 0

    # Step 1 run
    run_s1 = runs[1]
    assert run_s1["stage"] == "step_implementation"
    assert run_s1["role"] == "implementer"
    assert run_s1["step_id"] == "step-1"
    assert run_s1["selected_file_count"] >= 1
    assert run_s1["selected_files"] is not None

    # Step 2 run
    run_s2 = runs[2]
    assert run_s2["stage"] == "step_implementation"
    assert run_s2["role"] == "implementer"
    assert run_s2["step_id"] == "step-2"

    # Final review Round 1
    run_rev1 = runs[3]
    assert run_rev1["stage"] == "final_review"
    assert run_rev1["role"] == "reviewer"
    assert run_rev1["round_number"] == 1
    assert run_rev1["input_tokens"] == 150
    assert run_rev1["output_tokens"] == 75
    assert run_rev1["reasoning_tokens"] == 25
    assert run_rev1["cached_tokens"] == 10
    assert run_rev1["visible_output_tokens"] == 50

    # Final repair run
    run_rep = runs[4]
    assert run_rep["stage"] == "final_repair"
    assert run_rep["role"] == "implementer"
    assert run_rep["round_number"] == 1
    assert run_rep["fusion_context_chars"] is not None and run_rep["fusion_context_chars"] > 0

    # Final review Round 2 (rereview)
    run_rev2 = runs[5]
    assert run_rev2["stage"] == "final_rereview"
    assert run_rev2["role"] == "reviewer"
    assert run_rev2["round_number"] == 2
    assert run_rev2["input_tokens"] == 160
    assert run_rev2["output_tokens"] == 40
    assert run_rev2["reasoning_tokens"] == 15
    assert run_rev2["cached_tokens"] == 20
    assert run_rev2["visible_output_tokens"] == 30

    # Verify verifications table
    verifs = conn.execute(
        "SELECT * FROM verifications WHERE task_id = ? ORDER BY timestamp ASC;",
        (result.task.id,),
    ).fetchall()

    # Verifications should include:
    # 1. Step 1 verif (STEP)
    # 2. Step 2 verif (STEP)
    # 3. Final verif before review (FINAL)
    # 4. Post-repair verif (POST_REPAIR)
    # 5. Final verif after repair (FINAL)
    assert len(verifs) >= 4
    v_types = [v["verification_type"] for v in verifs]
    assert "STEP" in v_types
    assert "FINAL" in v_types
    assert "POST_REPAIR" in v_types

    for v in verifs:
        assert v["exit_code"] == 0
        assert v["passed"] == 1
        assert v["duration_seconds"] >= 0.0
        assert v["command"] is not None

    # Check plan status in SQLite
    plan_row = conn.execute("SELECT * FROM plans WHERE task_id = ?;", (result.task.id,)).fetchone()
    assert plan_row is not None
    # Requirement 6: Successful plan ends COMPLETED, not PREPARED
    assert plan_row["status"] == "COMPLETED"

    # Check task promotion disposition in SQLite
    task_row = conn.execute("SELECT * FROM tasks WHERE id = ?;", (result.task.id,)).fetchone()
    assert task_row["status"] == "COMPLETED"
    assert task_row["promotion_disposition"] == "DECLINED"
    assert task_row["last_checkpoint_sha"] is not None

    # Requirement 7: Task lifecycle summary reconstructs state authoritatively
    summary = orchestrator.state_manager.get_task_lifecycle_summary(result.task.id)
    assert summary["task_id"] == result.task.id
    assert summary["status"] == "COMPLETED"
    assert summary["plan_status"] == "COMPLETED"
    assert summary["finished_steps"] == ["step-1", "step-2"]
    assert summary["total_steps"] == 2
    assert summary["last_verified_checkpoint"] is not None
    assert summary["full_verification_passed"] is True
    assert summary["peer_review_approved"] is True
    assert summary["promotion_disposition"] == "DECLINED"
    assert summary["last_completed_provider_call"]["stage"] == "final_rereview"
    assert summary["last_completed_provider_call"]["provider"] == "ReviewerMock"

    # Clean up workspace
    if result.workspace_session:
        result.workspace_session.teardown(delete_branch=True)
    db.close()


def test_unavailable_native_metrics_persist_as_null_never_zeroes(tmp_path: Path):
    """Requirement 3 & 9: Verify unavailable native metrics persist as NULL, not fabricated zeroes."""
    db = Database(tmp_path / "test.db")
    conn = db.connect()

    from fusion_agent.memory.project_state import ProjectStateManager
    sm = ProjectStateManager(db)

    # Record stage with None tokens
    run_id = sm.record_provider_stage(
        task_id="task-null-test",
        stage="plan_generation",
        provider_name="TestProv",
        role="planner",
        response_content="output",
        input_tokens=None,
        output_tokens=None,
        reasoning_tokens=None,
        cached_tokens=None,
        visible_output_tokens=None,
        raw_usage=None,
    )

    row = conn.execute("SELECT * FROM agent_runs WHERE id = ?;", (run_id,)).fetchone()
    assert row["input_tokens"] is None
    assert row["output_tokens"] is None
    assert row["reasoning_tokens"] is None
    assert row["cached_tokens"] is None
    assert row["visible_output_tokens"] is None
    assert row["raw_usage"] is None

    # Now record stage with explicit 0 vs None to verify 0 is preserved only if explicitly provided
    run_id_zero = sm.record_provider_stage(
        task_id="task-null-test",
        stage="plan_generation",
        provider_name="TestProv",
        role="planner",
        response_content="output",
        input_tokens=0,
        output_tokens=0,
        reasoning_tokens=0,
        cached_tokens=None,  # missing
    )

    row_zero = conn.execute("SELECT * FROM agent_runs WHERE id = ?;", (run_id_zero,)).fetchone()
    assert row_zero["input_tokens"] == 0
    assert row_zero["output_tokens"] == 0
    assert row_zero["reasoning_tokens"] == 0
    assert row_zero["cached_tokens"] is None

    db.close()


def test_plan_repair_telemetry_persists(clean_git_project: Path):
    """Verify that plan_repair provider invocation generates an agent_runs record."""
    db = Database(clean_git_project / ".fusion" / "fusion.db")
    config = FusionConfig(
        project_name="TestProject",
        project_root=str(clean_git_project),
        verification_command="python -c \"import sys; sys.exit(0)\"",
        deliberation=DeliberationConfig(allow_multi_step_planning=True),
    )

    plan_call_count = [0]

    def planner_handler(prompt, context=None):
        plan_call_count[0] += 1
        if plan_call_count[0] == 1:
            # First call returns invalid json to force plan_repair
            return "This is not valid json for a plan."
        # Second call (repair) returns valid plan
        return json.dumps({
            "title": "Repaired Plan",
            "summary": "Plan fixed by repair.",
            "steps": [
                {
                    "id": "step-1",
                    "objective": "Step 1",
                    "expected_files": ["step1.py"],
                    "dependencies": [],
                },
            ],
        })

    mock_planner = MockProvider(name="PlannerMock")
    mock_planner.response_generator = planner_handler
    mock_impl = MockProvider(name="ImplMock", default_response="### File: step1.py\n```python\nS=1\n```")
    mock_rev = MockProvider(name="ReviewerMock", default_review_status=ReviewStatus.APPROVED)

    orch = FusionOrchestrator(
        config=config,
        database=db,
        providers={"planner": mock_planner, "implementer": mock_impl, "reviewer": mock_rev},
    )

    res = orch.run_task("Implement multi-step feature: extend schema and add a query service and tests.")
    assert res.task.status == TaskStatus.COMPLETED

    conn = db.connect()
    runs = conn.execute(
        "SELECT * FROM agent_runs WHERE task_id = ? ORDER BY timestamp ASC;",
        (res.task.id,),
    ).fetchall()

    stages = [r["stage"] for r in runs]
    assert "plan_generation" in stages
    assert "plan_repair" in stages

    repair_run = next(r for r in runs if r["stage"] == "plan_repair")
    assert repair_run["role"] == "planner"
    assert repair_run["status"] == "SUCCESS"
    assert repair_run["round_number"] == 1

    if res.workspace_session:
        res.workspace_session.teardown(delete_branch=True)
    db.close()


def test_step_repair_telemetry_and_failed_verification(clean_git_project: Path):
    """Verify that a failing step triggers step_repair, and both the failure and the repair persist in agent_runs and verifications."""
    db = Database(clean_git_project / ".fusion" / "fusion.db")
    config = FusionConfig(
        project_name="TestProject",
        project_root=str(clean_git_project),
        verification_command="python -c \"import sys, os; sys.exit(0 if os.path.exists('fixed.txt') else 1)\"",
        deliberation=DeliberationConfig(allow_multi_step_planning=True, max_repair_rounds=2),
    )

    plan_json = json.dumps({
        "title": "Repair Test Feature",
        "summary": "Step that fails then repairs.",
        "steps": [
            {
                "id": "step-1",
                "objective": "Create file",
                "expected_files": ["broken.txt"],
                "dependencies": [],
            },
        ],
    })

    impl_count = [0]

    def implementer_handler(prompt, context=None):
        impl_count[0] += 1
        if impl_count[0] == 1:
            # Initially creates broken file that fails verification
            return "### File: broken.txt\n```\nbroken\n```"
        # Repair creates fixed.txt to satisfy verification
        return "### File: fixed.txt\n```\nfixed\n```"

    mock_planner = MockProvider(name="PlannerMock", default_response=plan_json)
    mock_impl = MockProvider(name="ImplMock")
    mock_impl.response_generator = implementer_handler
    mock_rev = MockProvider(name="ReviewerMock", default_review_status=ReviewStatus.APPROVED)

    orch = FusionOrchestrator(
        config=config,
        database=db,
        providers={"planner": mock_planner, "implementer": mock_impl, "reviewer": mock_rev},
    )

    res = orch.run_task("Implement multi-step feature: extend schema and add a query service and tests.")
    assert res.task.status == TaskStatus.COMPLETED

    conn = db.connect()
    runs = conn.execute(
        "SELECT * FROM agent_runs WHERE task_id = ? ORDER BY timestamp ASC;",
        (res.task.id,),
    ).fetchall()

    stages = [r["stage"] for r in runs]
    assert "step_implementation" in stages
    assert "step_repair" in stages

    # Check verifications table
    verifs = conn.execute(
        "SELECT * FROM verifications WHERE task_id = ? ORDER BY timestamp ASC;",
        (res.task.id,),
    ).fetchall()

    # Step 1 failed first, then succeeded on repair
    step_verifs = [v for v in verifs if v["step_id"] == "step-1"]
    assert len(step_verifs) >= 2
    assert step_verifs[0]["passed"] == 0
    assert step_verifs[0]["exit_code"] == 1
    assert step_verifs[1]["passed"] == 1
    assert step_verifs[1]["exit_code"] == 0

    if res.workspace_session:
        res.workspace_session.teardown(delete_branch=True)
    db.close()


def test_deliberation_stages_persisted_uniformly(clean_git_project: Path):
    """Verify that deliberation strategies (e.g. DEBATE) persist proposals, critiques, and synthesis uniformly."""
    db = Database(clean_git_project / ".fusion" / "fusion.db")
    config = FusionConfig(
        project_name="TestProject",
        project_root=str(clean_git_project),
    )

    mock_a = MockProvider(name="ProviderA", default_response="Proposal from A")
    mock_b = MockProvider(name="ProviderB", default_response="Proposal from B")

    orch = FusionOrchestrator(
        config=config,
        database=db,
        providers={"provider_a": mock_a, "provider_b": mock_b},
    )

    # Force DEBATE strategy via prompt or run
    res = orch.run_task("Analyze architecture trade-offs between microservices and monolith.")

    conn = db.connect()
    runs = conn.execute(
        "SELECT * FROM agent_runs WHERE task_id = ? ORDER BY timestamp ASC;",
        (res.task.id,),
    ).fetchall()

    # Must contain deliberation records
    assert len(runs) >= 1
    for r in runs:
        assert r["stage"] in ("deliberation_proposal", "deliberation_critique", "deliberation_synthesis", "direct_execution")
        assert r["task_id"] == res.task.id
        assert r["status"] == "SUCCESS"

    db.close()

