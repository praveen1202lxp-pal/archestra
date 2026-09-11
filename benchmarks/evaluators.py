"""Evaluation and Scoring Engine for Benchmark Tasks.

Implements:
1. Python AST syntax validation across touched files.
2. SUT-independent scope oracle (required, allowed, forbidden paths).
3. Pre-existing regression test verification.
4. Hidden acceptance test runner with post-exit mounting & unlinking.
5. Unambiguous PASS / PARTIAL / FAIL scoring engine.
"""

import ast
import fnmatch
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Set, Tuple

from benchmarks.schema import BenchmarkScore, BenchmarkTask, ScoringResult

HIDDEN_EVALUATORS_DIR = Path(__file__).parent / "hidden_evaluators"


class ArtifactPolicy:
    """Defines files and directories that are ignored during scope checking (deterministic runtime artifacts)."""

    IGNORED_PATTERNS = [
        "__pycache__/*",
        "*/__pycache__/*",
        "*.pyc",
        "*.pyo",
        ".pytest_cache/*",
        "*/.pytest_cache/*",
        ".coverage*",
        "tests/_hidden_eval.py",
        ".gitignore",
        ".git/*",
        "*/.git/*",
    ]

    @classmethod
    def is_ignored(cls, path_str: str) -> bool:
        norm = path_str.replace("\\", "/")
        if norm.startswith("./"):
            norm = norm[2:]
        for pat in cls.IGNORED_PATTERNS:
            if fnmatch.fnmatch(norm, pat) or fnmatch.fnmatch(Path(norm).name, pat):
                return True
        return False


class ScopeOracle:
    """Evaluates whether changes adhere to task-specific file path constraints."""

    @staticmethod
    def evaluate(
        files_touched: List[str],
        required_paths: List[str],
        allowed_paths: List[str],
        forbidden_paths: List[str],
        protected_paths: Optional[List[str]] = None,
        allowed_new_test_paths: Optional[List[str]] = None,
        allowed_source_paths: Optional[List[str]] = None,
    ) -> Tuple[bool, List[str], List[str], List[str], List[str]]:
        """Evaluate file scope.
        
        Returns:
            (scope_violated, forbidden_touched, unintended_files, missing_required, protected_test_mutations)
        """
        protected_set = set(p.replace("\\", "/") for p in (protected_paths or []))
        new_test_patterns = allowed_new_test_paths or []
        allowed_src_set = set(p.replace("\\", "/") for p in (allowed_source_paths or []))

        # Filter out ignored deterministic runtime/evaluator artifacts
        meaningful_touched = [
            p.replace("\\", "/") for p in files_touched if not ArtifactPolicy.is_ignored(p)
        ]
        touched_set = set(meaningful_touched)

        required_set = set(p.replace("\\", "/") for p in required_paths)
        allowed_set = set(p.replace("\\", "/") for p in allowed_paths)
        forbidden_set = set(p.replace("\\", "/") for p in forbidden_paths)

        # Base permitted paths
        permitted_set = required_set | allowed_set | allowed_src_set

        # Check for mutations to protected preexisting tests/files
        protected_test_mutations = sorted(list(touched_set & protected_set))

        # Check for forbidden paths
        forbidden_touched_list = []
        for p in touched_set:
            if p in forbidden_set:
                forbidden_touched_list.append(p)
            else:
                for f_pat in forbidden_set:
                    if fnmatch.fnmatch(p, f_pat):
                        forbidden_touched_list.append(p)
                        break
        forbidden_touched = sorted(list(set(forbidden_touched_list)))

        # Unintended files are touched files that are not permitted and don't match allowed new test patterns
        unintended_list = []
        for p in touched_set:
            if p in permitted_set:
                continue
            matches_new_test = any(fnmatch.fnmatch(p, pat) for pat in new_test_patterns)
            if not matches_new_test:
                unintended_list.append(p)
        unintended_files = sorted(list(set(unintended_list)))

        missing_required = sorted(list(required_set - touched_set))

        scope_violated = bool(forbidden_touched or unintended_files or missing_required or protected_test_mutations)
        return scope_violated, forbidden_touched, unintended_files, missing_required, protected_test_mutations


class SyntaxValidator:
    """Validates Python syntax of all touched .py files."""

    @staticmethod
    def validate_files(repo_path: Path, files_touched: List[str]) -> Tuple[bool, List[str]]:
        """Return (is_valid, errors)."""
        errors = []
        for rel_path in files_touched:
            if not rel_path.endswith(".py"):
                continue
            file_path = repo_path / rel_path
            if not file_path.exists():
                continue
            try:
                code = file_path.read_text(encoding="utf-8")
                ast.parse(code, filename=str(file_path))
            except SyntaxError as se:
                errors.append(f"SyntaxError in {rel_path}: {se.msg} (line {se.lineno})")
            except Exception as e:
                errors.append(f"Parse error in {rel_path}: {str(e)}")

        return len(errors) == 0, errors


class BenchmarkEvaluator:
    """Coordinates post-exit validation and computes objective task scores."""

    def __init__(self, python_executable: Optional[str] = None):
        self.python_exec = python_executable or sys.executable

    def run_command(self, cmd: str, cwd: Path, timeout: float = 60.0) -> Tuple[int, str]:
        """Execute test command inside isolated repository."""
        # Replace 'pytest' with active virtualenv python -m pytest to ensure correct environment
        if cmd.startswith("pytest"):
            parts = [self.python_exec, "-m", "pytest"] + cmd.split()[1:]
        else:
            parts = cmd.split()

        try:
            res = subprocess.run(
                parts,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = (res.stdout or "") + "\n" + (res.stderr or "")
            return res.returncode, output.strip()
        except subprocess.TimeoutExpired:
            return -1, f"Command timed out after {timeout} seconds"
        except Exception as e:
            return -2, f"Failed to execute command '{cmd}': {str(e)}"

    def evaluate_task(
        self,
        task: BenchmarkTask,
        repo_path: Path,
        files_touched: List[str],
        timeout: float = 90.0,
        baseline_tree_hash: Optional[str] = None,
        candidate_tree_hash: Optional[str] = None,
    ) -> ScoringResult:
        """Run complete post-exit evaluation sequence."""
        failure_reasons = []

        # 1. AST Syntax Check on non-ignored files
        meaningful_files = [f for f in files_touched if not ArtifactPolicy.is_ignored(f)]
        syntax_valid, syntax_errors = SyntaxValidator.validate_files(repo_path, meaningful_files)
        if not syntax_valid:
            failure_reasons.extend(syntax_errors)

        # 2. SUT-Independent Scope Oracle
        scope_violated, forbidden_touched, unintended_files, missing_required, protected_mutations = ScopeOracle.evaluate(
            files_touched=files_touched,
            required_paths=task.required_paths,
            allowed_paths=task.allowed_paths,
            forbidden_paths=task.forbidden_paths,
            protected_paths=task.protected_paths,
            allowed_new_test_paths=task.allowed_new_test_paths,
            allowed_source_paths=task.allowed_source_paths,
        )

        scope_violation_reasons = []
        if missing_required:
            msg = f"Missing required path modifications: {sorted(missing_required)}"
            scope_violation_reasons.append(msg)
            failure_reasons.append(msg)
        if forbidden_touched:
            msg = f"Modified forbidden paths: {sorted(forbidden_touched)}"
            scope_violation_reasons.append(msg)
            failure_reasons.append(msg)
        if protected_mutations:
            msg = f"Modified protected test/source paths: {sorted(protected_mutations)}"
            scope_violation_reasons.append(msg)
            failure_reasons.append(msg)
        if unintended_files:
            msg = f"Modified unintended paths: {sorted(unintended_files)}"
            scope_violation_reasons.append(msg)
            failure_reasons.append(msg)

        # Check for required files missing on disk in candidate tree
        missing_on_disk = [p for p in task.required_paths if not (repo_path / p).is_file()]
        if missing_on_disk:
            msg = f"Required files missing from candidate filesystem: {sorted(missing_on_disk)}"
            scope_violation_reasons.append(msg)
            failure_reasons.append(msg)
            scope_violated = True

        # 3. Visible task tests (if defined)
        task_tests_passed = True
        visible_output = ""
        if task.visible_test_command:
            v_code, v_out = self.run_command(task.visible_test_command, cwd=repo_path, timeout=timeout)
            visible_output = v_out
            if v_code != 0:
                task_tests_passed = False
                failure_reasons.append(f"Visible tests failed (exit {v_code})")

        # 4. Hidden Acceptance Tests (Mount, execute, unlink)
        hidden_tests_passed = False
        hidden_output = ""
        hidden_src = HIDDEN_EVALUATORS_DIR / task.hidden_evaluator_module
        if hidden_src.exists():
            hidden_dest = repo_path / "tests" / "_hidden_eval.py"
            hidden_dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(str(hidden_src), str(hidden_dest))
                h_code, h_out = self.run_command(
                    f"{self.python_exec} -m pytest tests/_hidden_eval.py",
                    cwd=repo_path,
                    timeout=timeout,
                )
                hidden_output = h_out
                hidden_tests_passed = (h_code == 0)
                if not hidden_tests_passed:
                    failure_reasons.append("Hidden acceptance tests failed")
            finally:
                if hidden_dest.exists():
                    hidden_dest.unlink()
        else:
            failure_reasons.append(f"Hidden evaluator missing: {task.hidden_evaluator_module}")

        # Baseline Invariant Check:
        # If candidate tree is identical to the baseline tree, an edit task must not
        # report a hidden functional PASS.
        if (
            baseline_tree_hash is not None
            and candidate_tree_hash is not None
            and candidate_tree_hash == baseline_tree_hash
        ):
            if hidden_tests_passed:
                hidden_tests_passed = False
                failure_reasons.append(
                    "Candidate tree is identical to baseline tree; hidden functional PASS rejected by baseline discrimination invariant"
                )

        # 5. Full Pre-existing Regression Suite
        regressions_count = 0
        regressions_passed = True
        if task.full_regression_command:
            r_code, r_out = self.run_command(task.full_regression_command, cwd=repo_path, timeout=timeout)
            if r_code != 0:
                regressions_passed = False
                regressions_count = 1
                failure_reasons.append("Pre-existing regressions detected")

        # 6. Unambiguous Pre-frozen Scoring Oracle
        # PASS: 100% required hidden pass, 0 regressions, syntax valid, 0 scope violations, no missing required
        if (
            syntax_valid
            and hidden_tests_passed
            and regressions_passed
            and not forbidden_touched
            and not protected_mutations
            and not unintended_files
            and not missing_required
            and not missing_on_disk
        ):
            score = BenchmarkScore.PASS
        # FAIL: Syntax error, touched forbidden paths, protected test mutations, missing required when hidden tests failed, or both hidden and visible failed
        elif (
            not syntax_valid
            or bool(forbidden_touched)
            or bool(protected_mutations)
            or (not hidden_tests_passed and bool(missing_required))
            or (not hidden_tests_passed and bool(missing_on_disk))
            or (not hidden_tests_passed and not task_tests_passed)
        ):
            score = BenchmarkScore.FAIL
        # PARTIAL: Core task passes but has non-catastrophic scope violation or minor regressions
        elif (hidden_tests_passed and (unintended_files or not regressions_passed)) or (
            task_tests_passed and not hidden_tests_passed and not forbidden_touched and not protected_mutations and not missing_required and not missing_on_disk
        ):
            score = BenchmarkScore.PARTIAL
        else:
            score = BenchmarkScore.FAIL

        verification_output = f"=== VISIBLE TESTS ===\n{visible_output}\n=== HIDDEN EVALUATION ===\n{hidden_output}"

        return ScoringResult(
            score=score,
            task_tests_passed=task_tests_passed,
            hidden_tests_passed=hidden_tests_passed,
            regressions_count=regressions_count,
            regressions_passed=regressions_passed,
            syntax_valid=syntax_valid,
            scope_valid=not scope_violated,
            files_touched=meaningful_files,
            scope_violated=scope_violated,
            scope_violation_reasons=scope_violation_reasons,
            unintended_files=unintended_files,
            verification_exit_code=0 if score == BenchmarkScore.PASS else 1,
            verification_output=verification_output,
            failure_reasons=failure_reasons,
        )

    def evaluate_pre_review_criteria(
        self,
        task: BenchmarkTask,
        repo_path: Path,
        reviewer_finding_text: Optional[str] = None,
        timeout: float = 60.0,
    ) -> Tuple[List[str], List[str], bool]:
        """Objective validation of reviewer defect detection against pre-registered criteria.
        
        Returns:
            (pre_review_criteria_failed, reviewer_mapped_criteria, reviewer_found_valid_defect)
        """
        if not task.registered_defect_criteria:
            return [], [], False

        failed_criteria = []
        mapped_criteria = []

        hidden_src = HIDDEN_EVALUATORS_DIR / task.hidden_evaluator_module
        if hidden_src.exists():
            hidden_dest = repo_path / "tests" / "_hidden_eval.py"
            hidden_dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(str(hidden_src), str(hidden_dest))
                for crit in task.registered_defect_criteria:
                    kw = crit.replace("criterion_", "test_hidden_")
                    cmd = f"{self.python_exec} -m pytest tests/_hidden_eval.py -k {kw}"
                    code, _ = self.run_command(cmd, cwd=repo_path, timeout=timeout)
                    if code != 0:
                        failed_criteria.append(crit)
            finally:
                if hidden_dest.exists():
                    hidden_dest.unlink()

        if reviewer_finding_text:
            text_lower = reviewer_finding_text.lower()
            for crit in task.registered_defect_criteria:
                kw_parts = crit.replace("criterion_", "").split("_")
                matches = sum(1 for p in kw_parts if p in text_lower)
                if matches >= min(2, len(kw_parts)):
                    mapped_criteria.append(crit)

        valid_catches = set(failed_criteria) & set(mapped_criteria)
        reviewer_found_valid_defect = len(valid_catches) > 0
        return failed_criteria, mapped_criteria, reviewer_found_valid_defect
