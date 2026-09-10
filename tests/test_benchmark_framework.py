"""Unit and integration tests for Milestone 11 Benchmark Framework & Integrity Architecture."""

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from benchmarks.adapters import MockSUTAdapter
from benchmarks.evaluators import BenchmarkEvaluator, ScopeOracle, SyntaxValidator
from benchmarks.isolation import DisposableBenchmarkEnvironment
from benchmarks.mcp_demo import run_mcp_demonstration
from benchmarks.report import BenchmarkReporter
from benchmarks.runner import BenchmarkRunner
from benchmarks.schema import (
    BenchmarkCategory,
    BenchmarkRunRecord,
    BenchmarkScore,
    BenchmarkTask,
    SystemUnderTest,
)
from benchmarks.storage import BenchmarkStorage
from benchmarks.tasks.catalog import (
    BENCHMARK_TASKS,
    compute_benchmark_suite_hash,
    compute_hidden_evaluator_hash,
    compute_task_definition_hash,
    get_task_by_id,
)


def test_catalog_integrity_and_hashes():
    """Verify catalog has 12 tasks with unique IDs and valid hashes."""
    assert len(BENCHMARK_TASKS) == 12
    task_ids = [t.task_id for t in BENCHMARK_TASKS]
    assert len(set(task_ids)) == 12

    suite_hash = compute_benchmark_suite_hash()
    assert isinstance(suite_hash, str) and len(suite_hash) == 64

    for task in BENCHMARK_TASKS:
        t_hash = compute_task_definition_hash(task)
        assert len(t_hash) == 64
        h_hash = compute_hidden_evaluator_hash(task)
        assert len(h_hash) == 64
        assert task.required_paths is not None
        assert task.hidden_evaluator_module.startswith("eval_task_")


def test_disposable_environment_isolation(tmp_path):
    """Verify clean snapshot isolation: standalone git repo with single baseline commit."""
    task_id = "TASK-01"
    run_id = "test_iso_1"

    with DisposableBenchmarkEnvironment(task_id=task_id, run_id=run_id, base_temp_dir=tmp_path) as env:
        repo_path = env.repo_path
        assert repo_path.exists()
        assert len(env.baseline_snapshot_hash) == 64
        assert (repo_path / "src" / "pagination.py").exists()
        assert (repo_path / "tests" / "test_pagination.py").exists()

        # Hidden tests must NEVER exist in the repo
        assert not (repo_path / "benchmarks").exists()
        assert not (repo_path / "tests" / "_hidden_eval.py").exists()

        # Check git log: exactly 1 baseline commit HEAD
        res = subprocess.run(
            ["git", "log", "--oneline"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            check=True,
        )
        lines = res.stdout.strip().splitlines()
        assert len(lines) == 1
        assert lines[0].startswith(env.baseline_commit_hash[:7])

        # Modify a file and verify diff and touched files
        (repo_path / "src" / "pagination.py").write_text("# modified\n", encoding="utf-8")
        touched = env.get_touched_files()
        assert touched == ["src/pagination.py"]

        diff = env.capture_diff()
        assert "diff --git a/src/pagination.py b/src/pagination.py" in diff

    # Directory is safely torn down
    assert not repo_path.exists()


def test_hidden_evaluator_boundary_injection_and_cleanup(tmp_path):
    """Verify hidden tests live outside repo, are injected post-exit, and cleanly unlinked."""
    task = get_task_by_id("TASK-01")
    assert task is not None

    with DisposableBenchmarkEnvironment(task_id="TASK-01", run_id="test_hidden_1", base_temp_dir=tmp_path) as env:
        # Apply working reference solution
        mock_sut = MockSUTAdapter(solve_tasks={"TASK-01"})
        mock_sut.execute(task, env.repo_path)

        touched = env.get_touched_files()
        evaluator = BenchmarkEvaluator()
        result = evaluator.evaluate_task(task, env.repo_path, touched)

        assert result.hidden_tests_passed is True
        assert result.score == BenchmarkScore.PASS
        assert result.syntax_valid is True
        assert not result.scope_violated

        # Verify hidden test was unlinked from the isolated repo
        assert not (env.repo_path / "tests" / "_hidden_eval.py").exists()


def test_scope_oracle_rules():
    """Verify SUT-independent scope oracle evaluates paths independently."""
    required = ["src/pagination.py"]
    allowed = ["src/utils.py"]
    forbidden = ["config.json", "src/critical_auth.py"]

    # 1. Compliant modification
    violated, f_touched, u_touched, m_req = ScopeOracle.evaluate(
        files_touched=["src/pagination.py"],
        required_paths=required,
        allowed_paths=allowed,
        forbidden_paths=forbidden,
    )
    assert not violated
    assert len(f_touched) == 0 and len(u_touched) == 0 and len(m_req) == 0

    # 2. Forbidden modification
    violated, f_touched, u_touched, m_req = ScopeOracle.evaluate(
        files_touched=["src/pagination.py", "config.json"],
        required_paths=required,
        allowed_paths=allowed,
        forbidden_paths=forbidden,
    )
    assert violated
    assert f_touched == ["config.json"]

    # 3. Unintended modification
    violated, f_touched, u_touched, m_req = ScopeOracle.evaluate(
        files_touched=["src/pagination.py", "unintended.py"],
        required_paths=required,
        allowed_paths=allowed,
        forbidden_paths=forbidden,
    )
    assert violated
    assert u_touched == ["unintended.py"]

    # 4. Missing required modification
    violated, f_touched, u_touched, m_req = ScopeOracle.evaluate(
        files_touched=["src/utils.py"],
        required_paths=required,
        allowed_paths=allowed,
        forbidden_paths=forbidden,
    )
    assert violated
    assert m_req == ["src/pagination.py"]


def test_syntax_validator(tmp_path):
    """Verify Python AST syntax validator catches SyntaxError in touched files."""
    valid_file = tmp_path / "valid.py"
    valid_file.write_text("def hello():\n    return 'world'\n", encoding="utf-8")

    is_valid, errors = SyntaxValidator.validate_files(tmp_path, ["valid.py"])
    assert is_valid is True
    assert len(errors) == 0

    invalid_file = tmp_path / "broken.py"
    invalid_file.write_text("def broken(:\n    pass\n", encoding="utf-8")

    is_valid, errors = SyntaxValidator.validate_files(tmp_path, ["broken.py"])
    assert is_valid is False
    assert len(errors) == 1
    assert "SyntaxError in broken.py" in errors[0]


def test_scoring_engine_pass_partial_fail(tmp_path):
    """Verify objective scoring engine across PASS, PARTIAL, and FAIL conditions."""
    task = get_task_by_id("TASK-01")
    evaluator = BenchmarkEvaluator()

    # 1. PASS case
    with DisposableBenchmarkEnvironment(task_id="TASK-01", run_id="test_pass", base_temp_dir=tmp_path) as env:
        MockSUTAdapter(solve_tasks={"TASK-01"}).execute(task, env.repo_path)
        touched = env.get_touched_files()
        res = evaluator.evaluate_task(task, env.repo_path, touched)
        assert res.score == BenchmarkScore.PASS

    # 2. FAIL case (Syntax error)
    with DisposableBenchmarkEnvironment(task_id="TASK-01", run_id="test_fail_syntax", base_temp_dir=tmp_path) as env:
        MockSUTAdapter(simulate_syntax_error=True).execute(task, env.repo_path)
        touched = env.get_touched_files()
        res = evaluator.evaluate_task(task, env.repo_path, touched)
        assert res.score == BenchmarkScore.FAIL
        assert res.syntax_valid is False

    # 3. FAIL case (Forbidden path touched)
    with DisposableBenchmarkEnvironment(task_id="TASK-01", run_id="test_fail_forbidden", base_temp_dir=tmp_path) as env:
        MockSUTAdapter(solve_tasks={"TASK-01"}, simulate_forbidden_write=True).execute(task, env.repo_path)
        touched = env.get_touched_files()
        res = evaluator.evaluate_task(task, env.repo_path, touched)
        assert res.score == BenchmarkScore.FAIL

    # 4. PARTIAL case (Core passed, but unintended file touched)
    with DisposableBenchmarkEnvironment(task_id="TASK-01", run_id="test_partial", base_temp_dir=tmp_path) as env:
        MockSUTAdapter(solve_tasks={"TASK-01"}, simulate_unintended_write=True).execute(task, env.repo_path)
        touched = env.get_touched_files()
        res = evaluator.evaluate_task(task, env.repo_path, touched)
        assert res.score == BenchmarkScore.PARTIAL


def test_task_11_deterministic_socket_leak_detection(tmp_path):
    """Verify TASK-11 deterministic socket leak detection: leaky patch fails, clean patch passes."""
    task = get_task_by_id("TASK-11")
    evaluator = BenchmarkEvaluator()

    # Case A: Baseline / buggy patch (does not close sockets on failure)
    with DisposableBenchmarkEnvironment(task_id="TASK-11", run_id="t11_buggy", base_temp_dir=tmp_path) as env:
        # The baseline in catalog does not implement cleanup
        touched = env.get_touched_files()
        res = evaluator.evaluate_task(task, env.repo_path, touched)
        assert res.hidden_tests_passed is False
        assert res.score == BenchmarkScore.FAIL

    # Case B: Corrected patch with socket lifecycle cleanup
    with DisposableBenchmarkEnvironment(task_id="TASK-11", run_id="t11_fixed", base_temp_dir=tmp_path) as env:
        MockSUTAdapter(solve_tasks={"TASK-11"}).execute(task, env.repo_path)
        touched = env.get_touched_files()
        res = evaluator.evaluate_task(task, env.repo_path, touched)
        assert res.hidden_tests_passed is True
        assert res.score == BenchmarkScore.PASS


def test_provenance_and_sqlite_storage(tmp_path):
    """Verify complete cryptographic provenance and run records stored in SQLite."""
    db_path = tmp_path / "bench.db"
    storage = BenchmarkStorage(db_path=db_path)

    rec = BenchmarkRunRecord(
        run_id="run_test_001",
        benchmark_suite_version="1.0.0",
        benchmark_suite_hash="suite_hash_123",
        task_definition_hash="task_hash_456",
        hidden_evaluator_hash="hidden_hash_789",
        baseline_snapshot_hash="commit_hash_789",
        task_id="TASK-01",
        category="single_file_bug_fix",
        system_under_test=SystemUnderTest.FUSION,
        repetition_index=1,
        start_time="2026-09-10T12:00:00Z",
        end_time="2026-09-10T12:01:00Z",
        wall_clock_duration_seconds=60.0,
        os_platform="Windows 11",
        python_version="3.11.0",
        score=BenchmarkScore.PASS,
        verification_passed=True,
        hidden_tests_passed=True,
        regressions_count=0,
        files_touched=["src/pagination.py"],
        native_input_tokens=1400,
        native_output_tokens=300,
        fusion_controlled_context_tokens=1100,
        provider_managed_overhead_residual=300,
        reviewer_verdict="APPROVED",
        reviewer_found_defect=False,
        reviewer_found_valid_defect=False,
    )

    storage.record_run(rec)
    runs = storage.get_runs(task_id="TASK-01")
    assert len(runs) == 1
    loaded = runs[0]

    assert loaded.run_id == "run_test_001"
    assert loaded.benchmark_suite_version == "1.0.0"
    assert loaded.benchmark_suite_hash == "suite_hash_123"
    assert loaded.system_under_test == SystemUnderTest.FUSION
    assert loaded.score == BenchmarkScore.PASS
    assert loaded.provider_managed_overhead_residual == 300
    assert loaded.files_touched == ["src/pagination.py"]


def test_mock_matrix_runner_and_repetition_rotation(tmp_path):
    """Verify matrix runner executes repetitions, rotates SUT order, and persists records."""
    db_path = tmp_path / "matrix_bench.db"
    storage = BenchmarkStorage(db_path=db_path)
    runner = BenchmarkRunner(storage=storage, base_temp_dir=tmp_path, is_live=False)

    sample_tasks = [get_task_by_id("TASK-01"), get_task_by_id("TASK-02")]
    suts = [SystemUnderTest.FUSION, SystemUnderTest.CODEX_ALONE, SystemUnderTest.ANTIGRAVITY_ALONE]

    # Run 2 repetitions with mock solver for TASK-01
    records = runner.run_matrix(
        tasks=sample_tasks,
        suts=suts,
        repetitions=2,
        solve_tasks_for_mock={"TASK-01"},
    )

    # 2 tasks * 3 SUTs * 2 repetitions = 12 run records
    assert len(records) == 12
    stored = storage.get_runs()
    assert len(stored) == 12

    # Check repetition indices
    rep0 = [r for r in records if r.repetition_index == 0]
    rep1 = [r for r in records if r.repetition_index == 1]
    assert len(rep0) == 6
    assert len(rep1) == 6

    # Verify TASK-01 solved records scored PASS
    t1_runs = [r for r in records if r.task_id == "TASK-01"]
    for r in t1_runs:
        assert r.score == BenchmarkScore.PASS


def test_reporter_metrics_and_markdown(tmp_path):
    """Verify reporting engine outputs Markdown tables, medians, and token residuals."""
    db_path = tmp_path / "report_bench.db"
    storage = BenchmarkStorage(db_path=db_path)
    runner = BenchmarkRunner(storage=storage, base_temp_dir=tmp_path, is_live=False)

    sample_tasks = [get_task_by_id("TASK-01")]
    runner.run_matrix(tasks=sample_tasks, repetitions=1, solve_tasks_for_mock={"TASK-01"})

    reporter = BenchmarkReporter(storage=storage)
    md = reporter.generate_report_markdown()

    assert "# Comparative Evaluation Report — Milestone 11" in md
    assert "Controlled Comparative Protocol Notice" in md
    assert "Executive Performance Summary" in md
    assert "Cross-Model Review Value (Fusion Agent)" in md
    assert "Context Efficiency & Token Accounting" in md
    assert "TASK-01" in md


def test_hidden_evaluator_lifecycle_strict_isolation(tmp_path):
    """Prove hidden evaluators never exist during SUT execution and leave no git traces."""
    task = get_task_by_id("TASK-01")
    lifecycle_checks = {"during_execution_clean": False}

    class InspectingAdapter(MockSUTAdapter):
        def execute(self, t, repo_path):
            # Assert hidden test is completely absent during SUT execution
            assert not (repo_path / "tests" / "_hidden_eval.py").exists()
            assert not (repo_path / "benchmarks").exists()
            lifecycle_checks["during_execution_clean"] = True
            return super().execute(t, repo_path)

    runner = BenchmarkRunner(
        storage=BenchmarkStorage(db_path=tmp_path / "iso.db"),
        base_temp_dir=tmp_path,
        is_live=False,
        custom_adapters={SystemUnderTest.FUSION: InspectingAdapter(solve_tasks={"TASK-01"})},
    )

    record = runner.run_single_task(task=task, sut=SystemUnderTest.FUSION)
    assert lifecycle_checks["during_execution_clean"] is True
    assert record.score == BenchmarkScore.PASS
    assert record.hidden_tests_passed is True


def test_task_12_comparative_fairness():
    """Prove TASK-12 provides equivalent resolved issue content in prompt for all SUTs."""
    task = get_task_by_id("TASK-12")
    assert task is not None
    # Prompt must contain the full issue description, title, and reproduction hint
    assert "Markdown parser retains trailing '#' and whitespace in headers" in task.prompt
    assert "When parsing '### Section Header ###'" in task.prompt
    assert "Section Header" in task.prompt


def test_objective_review_defect_validation(tmp_path):
    """Prove reviewer_found_valid_defect is awarded only when pre-review code fails registered criteria."""
    task = get_task_by_id("TASK-11")
    evaluator = BenchmarkEvaluator()

    with DisposableBenchmarkEnvironment(task_id="TASK-11", run_id="obj_rev_1", base_temp_dir=tmp_path) as env:
        # Case A: Reviewer mentions socket leak, and pre-review code actually leaks sockets
        failed, mapped, valid = evaluator.evaluate_pre_review_criteria(
            task=task,
            repo_path=env.repo_path,
            reviewer_finding_text="Detected unclosed socket resource leak on connection failure",
        )
        assert "criterion_socket_resource_leak_on_failure" in failed
        assert "criterion_socket_resource_leak_on_failure" in mapped
        assert valid is True

        # Case B: Reviewer makes an ungrounded claim about an unrelated issue
        failed, mapped, valid = evaluator.evaluate_pre_review_criteria(
            task=task,
            repo_path=env.repo_path,
            reviewer_finding_text="Detected incorrect json formatting in response",
        )
        assert valid is False


def test_complete_36_run_mock_benchmark_matrix(tmp_path):
    """Execute complete 36-run mock benchmark matrix (12 tasks x 3 SUTs x 1 repetition)."""
    db_path = tmp_path / "complete_36_matrix.db"
    storage = BenchmarkStorage(db_path=db_path)
    runner = BenchmarkRunner(storage=storage, base_temp_dir=tmp_path, is_live=False)

    records = runner.run_matrix(
        tasks=BENCHMARK_TASKS,
        suts=[SystemUnderTest.FUSION, SystemUnderTest.CODEX_ALONE, SystemUnderTest.ANTIGRAVITY_ALONE],
        repetitions=1,
        solve_tasks_for_mock={"TASK-01", "TASK-03", "TASK-05", "TASK-11"},
    )

    # Exactly 36 runs
    assert len(records) == 36
    stored = storage.get_runs()
    assert len(stored) == 36

    # Verify all 12 tasks represented
    task_ids_present = {r.task_id for r in records}
    assert len(task_ids_present) == 12

    # Verify all 3 SUTs represented
    suts_present = {r.system_under_test for r in records}
    assert suts_present == {SystemUnderTest.FUSION, SystemUnderTest.CODEX_ALONE, SystemUnderTest.ANTIGRAVITY_ALONE}

    # Verify report generation includes all tasks and systems
    reporter = BenchmarkReporter(storage=storage)
    report_md = reporter.generate_report_markdown(records)
    for t in BENCHMARK_TASKS:
        assert t.task_id in report_md
    for s in ["FUSION", "CODEX_ALONE", "ANTIGRAVITY_ALONE"]:
        assert s in report_md


def test_mcp_demo_execution(tmp_path):
    """Verify non-scored real read-only GitHub MCP demo succeeds under M10 trust boundary."""
    trust_file = tmp_path / "mcp_trust.json"
    result = run_mcp_demonstration(issue_number=42, custom_trust_store_path=trust_file)

    assert result["status"] == "success"
    assert result["is_trusted"] is True
    assert "[EXTERNAL_MCP_DATA: github.get_issue]" in result["evidence"]
    assert "UNTRUSTED EXTERNAL EVIDENCE" in result["evidence"]
