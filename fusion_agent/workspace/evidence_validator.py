"""Affirmative Evidence Validator for No-Op / NO_CHANGE_REQUIRED outcomes.

Ensures that implementation tasks producing an empty diff are never marked
as NO_CHANGE_REQUIRED merely because unrelated existing tests happen to pass.
Requires affirmative, request-specific evidence tied directly to the requested condition.
"""

from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Any, List, Optional, Set, Tuple

from fusion_agent.models.task import Task
from fusion_agent.workspace.verifier import VerificationResult


@dataclass
class AffirmativeEvidenceResult:
    """Outcome of affirmative evidence evaluation."""
    is_satisfied: bool
    reason: str
    evidence_type: str = "none"  # "targeted_test", "direct_inspection", "assertion", "none"


class AffirmativeEvidenceValidator:
    """Validates whether affirmative evidence proves requested behavior is already satisfied."""

    # Common generic test commands that run full suites without targeted filters
    GENERIC_TEST_COMMAND_PATTERNS = [
        r"^pytest\s*$",
        r"^pytest\s+-q\s*$",
        r"^pytest\s+-v\s*$",
        r"^python\s+-m\s+pytest\s*$",
        r"^python\s+-m\s+pytest\s+-q\s*$",
        r"^npm\s+test\s*$",
        r"^mvn\s+test\s*$",
        r"^cargo\s+test\s*$",
        r"^go\s+test\s+\./\.\.\.\s*$",
    ]

    @classmethod
    def is_generic_test_command(cls, command: Optional[str]) -> bool:
        """Return True if command is a broad project-wide test run rather than a targeted check."""
        if not command:
            return True
        cmd_clean = command.strip().lower()
        for pat in cls.GENERIC_TEST_COMMAND_PATTERNS:
            if re.match(pat, cmd_clean):
                return True
        return False

    @classmethod
    def extract_requested_symbols(cls, task_text: str) -> List[Tuple[str, str]]:
        """Extract explicit symbols requested in the task prompt.

        Returns list of (symbol_type, symbol_name) e.g. ('function', 'calculate_tax').
        """
        symbols = []
        # Functions: def foo, function foo, method foo, `foo()`
        func_matches = re.findall(r"\b(?:def|function|method)\s+([a-zA-Z0-9_]+)", task_text)
        for fn in func_matches:
            if fn not in [s[1] for s in symbols]:
                symbols.append(("function", fn))

        # Classes: class Foo, class `Foo`
        class_matches = re.findall(r"\bclass\s+([A-Za-z0-9_]+)", task_text)
        for cn in class_matches:
            if cn not in [s[1] for s in symbols]:
                symbols.append(("class", cn))

        # Explicit backticked function/method calls: `foo_bar(...)`
        call_matches = re.findall(r"`([a-zA-Z0-9_]+)\([^`]*\)`", task_text)
        for cm in call_matches:
            if cm not in [s[1] for s in symbols]:
                symbols.append(("call", cm))

        return symbols

    @classmethod
    def extract_target_files(cls, task_text: str) -> List[str]:
        """Extract target file paths mentioned in the prompt."""
        path_matches = re.findall(r"\b([a-zA-Z0-9_\-./\\]+\.[a-zA-Z0-9_]+)\b", task_text)
        target_files = []
        for pm in path_matches:
            norm = pm.replace("\\", "/").strip("./")
            ext = Path(norm).suffix.lower()
            if ext in (".py", ".js", ".ts", ".go", ".rs", ".java", ".c", ".cpp"):
                if norm not in target_files:
                    target_files.append(norm)
        return target_files

    @classmethod
    def validate_no_change_evidence(
        cls,
        task: Task,
        repo_path: Path,
        verification_result: Optional[VerificationResult],
        response_text: str,
        broker: Optional[Any] = None,
    ) -> AffirmativeEvidenceResult:
        """Evaluate whether affirmative, request-specific evidence exists proving requirements are satisfied.

        Returns AffirmativeEvidenceResult with is_satisfied=True only if direct evidence exists.
        """
        task_text = f"{task.title}\n{task.description}".strip()
        resp_lower = (response_text or "").lower()

        # 1. Did the agent claim the behavior was already satisfied?
        already_satisfied_signals = [
            "already satisfied",
            "already implemented",
            "already exists",
            "already present",
            "no change required",
            "no changes needed",
            "no change is required",
            "already correct",
            "no modification needed",
            "no modification required",
        ]
        has_satisfied_claim = any(sig in resp_lower for sig in already_satisfied_signals)
        if not has_satisfied_claim:
            return AffirmativeEvidenceResult(
                is_satisfied=False,
                reason="No affirmative claim or proof provided that requirements are already satisfied.",
                evidence_type="none",
            )

        # 2. Extract requested symbols and target files
        requested_symbols = cls.extract_requested_symbols(task_text)
        target_files = cls.extract_target_files(task_text)

        # 3. Direct Repository Inspection for requested symbols
        # If the task explicitly asks to implement/add/fix specific functions/classes in target files:
        if requested_symbols and target_files:
            for tf in target_files:
                file_path = repo_path / tf
                if not file_path.is_file():
                    for parent in [repo_path.parent, repo_path.parent.parent, repo_path.parent.parent.parent]:
                        if (parent / tf).is_file():
                            file_path = parent / tf
                            break
                if not file_path.is_file():
                    return AffirmativeEvidenceResult(
                        is_satisfied=False,
                        reason=f"Target file '{tf}' does not exist in repository; requested implementation is absent.",
                        evidence_type="direct_inspection",
                    )
                try:
                    content = file_path.read_text(encoding="utf-8", errors="replace")
                except Exception as exc:
                    return AffirmativeEvidenceResult(
                        is_satisfied=False,
                        reason=f"Could not read target file '{tf}': {exc}",
                        evidence_type="direct_inspection",
                    )

                for stype, sname in requested_symbols:
                    if stype in ("function", "call"):
                        pattern = rf"\bdef\s+{sname}\b"
                    elif stype == "class":
                        pattern = rf"\bclass\s+{sname}\b"
                    else:
                        pattern = rf"\b{sname}\b"

                    if not re.search(pattern, content):
                        return AffirmativeEvidenceResult(
                            is_satisfied=False,
                            reason=(
                                f"Requested {stype} '{sname}' is absent from target file '{tf}'. "
                                "Repository inspection proves requested implementation does not exist."
                            ),
                            evidence_type="direct_inspection",
                        )

        # 4. Evaluate Test Verification Evidence
        verif_passed = (verification_result.passed if verification_result else False)
        verif_cmd = (verification_result.command if verification_result else "") or ""
        verif_stdout = (verification_result.stdout if verification_result else "") or ""

        # Is the verification command a targeted, task-specific check?
        is_generic = cls.is_generic_test_command(verif_cmd)

        # Check if task specifies a trusted targeted test command in metadata
        task_metadata = getattr(task, "metadata", {}) or {}
        targeted_cmd = task_metadata.get("targeted_test_command") or task_metadata.get("visible_test_command")
        is_trusted_targeted_cmd = bool(targeted_cmd and verif_cmd and targeted_cmd.strip() in verif_cmd.strip())

        # Check if test stdout explicitly records passing tests matching requested behavior
        stdout_has_targeted_test = False
        if verif_passed and verif_stdout:
            for stype, sname in requested_symbols:
                if re.search(rf"\btest_{sname}\b|\b{sname}_test\b", verif_stdout, re.IGNORECASE):
                    stdout_has_targeted_test = True
                    break
            # Also check if target file's sister test is reported in stdout
            for tf in target_files:
                stem = Path(tf).stem
                if f"test_{stem}" in verif_stdout.lower() or f"{stem}_test" in verif_stdout.lower():
                    stdout_has_targeted_test = True
                    break

        if verif_passed and (is_trusted_targeted_cmd or not is_generic or stdout_has_targeted_test):
            # Targeted test affirmatively passed
            evidence_desc = (
                f"Targeted test verification passed: command='{verif_cmd}'"
                if not is_generic
                else f"Targeted tests matching requirement executed and passed in stdout"
            )
            return AffirmativeEvidenceResult(
                is_satisfied=True,
                reason=f"Affirmative verification established: {evidence_desc}.",
                evidence_type="targeted_test",
            )

        # 5. Direct Repository Inspection as Affirmative Evidence
        # If requested symbols were found in the file, and direct inspection proves they exist:
        if requested_symbols and target_files:
            # We confirmed all requested symbols exist in target files above
            symbols_str = ", ".join(f"{s[0]} '{s[1]}'" for s in requested_symbols)
            return AffirmativeEvidenceResult(
                is_satisfied=True,
                reason=f"Direct repository inspection verified requested {symbols_str} is already implemented in {', '.join(target_files)}.",
                evidence_type="direct_inspection",
            )

        # 6. Fallback: Generic test success alone is insufficient
        if verif_passed and is_generic:
            return AffirmativeEvidenceResult(
                is_satisfied=False,
                reason=(
                    "Generic project test suite passed, but no targeted reproduction or request-specific "
                    "evidence was provided proving the requested behavior is already satisfied."
                ),
                evidence_type="none",
            )

        return AffirmativeEvidenceResult(
            is_satisfied=False,
            reason="Affirmative evidence unavailable: no targeted test pass or verified implementation found.",
            evidence_type="none",
        )
