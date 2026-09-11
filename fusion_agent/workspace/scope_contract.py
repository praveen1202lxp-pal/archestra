"""Planned Change Scope Contract for validating code modifications before workspace writes."""

from dataclasses import dataclass, field
from enum import Enum
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


class ScopeExpansionCategory(str, Enum):
    """Categorization of requested scope expansions."""
    TEST_UPDATE_API_CHANGE = "test_update_api_change"
    DEPENDENCY_CONFIG_REQUIRED = "dependency_config_required"
    MULTI_FILE_COMPONENT_MIGRATION = "multi_file_component_migration"
    DOCUMENTATION = "documentation"
    UNAUTHORIZED = "unauthorized"


@dataclass
class ScopeContract:
    """Represents the authorized change scope derived from task requirements and context."""
    expected_files: List[str] = field(default_factory=list)
    allowed_related_files: List[str] = field(default_factory=list)
    new_files_allowed: bool = True
    new_test_files_allowed: bool = True
    scope_expansion_reasons: Dict[str, str] = field(default_factory=dict)

    # Patterns for files that should be rejected unless explicitly requested in task
    DISALLOWED_DOC_EXTENSIONS: Set[str] = field(default_factory=lambda: {
        ".md", ".rst", ".doc", ".docx", ".pdf"
    })
    DISALLOWED_EXTENSIONS: Set[str] = field(default_factory=lambda: {
        ".md", ".rst", ".doc", ".docx", ".pdf"
    })
    DISALLOWED_DEPENDENCY_FILES: Set[str] = field(default_factory=lambda: {
        "requirements.txt", "pyproject.toml", "setup.py", "setup.cfg",
        "pipfile", "pipfile.lock", "package.json", "package-lock.json",
        "yarn.lock", "poetry.lock", "cargo.toml", "cargo.lock", "go.mod", "go.sum"
    })

    @classmethod
    def derive(
        cls,
        task_prompt: str,
        code_context: Optional[Any] = None,
        plan_steps: Optional[List[Any]] = None,
    ) -> "ScopeContract":
        """Derive an initial change scope contract from task wording, context, and conventions."""
        expected: List[str] = []
        allowed_related: List[str] = []

        # 1. Extract file paths explicitly mentioned in task prompt
        path_matches = re.findall(
            r"\b[a-zA-Z0-9_\-./\\]+\.[a-zA-Z0-9_]+\b",
            task_prompt,
        )
        for pm in path_matches:
            norm = pm.replace("\\", "/").strip("./")
            if norm and norm not in expected:
                expected.append(norm)

        # 2. Extract files selected by CodeContext (if available)
        if code_context and hasattr(code_context, "selected_files"):
            for sf in code_context.selected_files:
                p = getattr(sf, "path", sf)
                norm = str(p).replace("\\", "/").strip("./")
                is_test = (
                    norm.startswith("tests/")
                    or norm.startswith("test/")
                    or Path(norm).name.startswith("test_")
                    or Path(norm).name.endswith("_test.py")
                )
                if norm and not is_test and norm not in expected:
                    expected.append(norm)

        # 3. Extract files from plan steps (if available)
        if plan_steps:
            for step in plan_steps:
                step_files = getattr(step, "expected_files", None) or getattr(step, "target_files", None) or []
                for tf in step_files:
                    norm = str(tf).replace("\\", "/").strip("./")
                    if norm and norm not in expected:
                        expected.append(norm)

        # 4. Infer allowed related files based on repository conventions:
        # e.g., for src/<name>.py, allow tests/test_<name>.py or tests/<name>_test.py
        for exp in list(expected):
            exp_path = Path(exp)
            stem = exp_path.stem
            ext = exp_path.suffix

            test_candidates = [
                f"tests/test_{stem}{ext}",
                f"tests/{stem}_test{ext}",
                f"test/test_{stem}{ext}",
                f"{exp_path.parent}/test_{stem}{ext}",
                f"{exp_path.parent}/{stem}_test{ext}",
            ]
            for tc in test_candidates:
                clean_tc = tc.replace("\\", "/").strip("./")
                if clean_tc not in expected and clean_tc not in allowed_related:
                    allowed_related.append(clean_tc)

            if stem.startswith("test_"):
                raw_stem = stem[5:]
                src_candidates = [
                    f"src/{raw_stem}{ext}",
                    f"lib/{raw_stem}{ext}",
                    f"{raw_stem}{ext}",
                ]
                for sc in src_candidates:
                    clean_sc = sc.replace("\\", "/").strip("./")
                    if clean_sc not in expected and clean_sc not in allowed_related:
                        allowed_related.append(clean_sc)

        return cls(
            expected_files=expected,
            allowed_related_files=allowed_related,
            new_files_allowed=True,
            new_test_files_allowed=True,
        )

    def classify_expansion_request(
        self,
        rel_path: str,
        reason: str,
        is_new_file: bool = False,
    ) -> ScopeExpansionCategory:
        """Classify a requested scope expansion into a recognized category."""
        norm_path = rel_path.replace("\\", "/").strip("./")
        filename = Path(norm_path).name.lower()
        ext = Path(norm_path).suffix.lower()

        is_test_file = (
            norm_path.startswith("tests/")
            or norm_path.startswith("test/")
            or filename.startswith("test_")
            or filename.endswith("_test.py")
        )
        if is_test_file and not is_new_file:
            return ScopeExpansionCategory.TEST_UPDATE_API_CHANGE

        if filename in self.DISALLOWED_DEPENDENCY_FILES:
            return ScopeExpansionCategory.DEPENDENCY_CONFIG_REQUIRED

        is_doc = (ext in self.DISALLOWED_DOC_EXTENSIONS) or (
            ext == ".txt" and (
                "doc" in norm_path.lower()
                or any(d in filename for d in ["readme", "notes", "changelog", "license", "todo", "guide"])
            )
        )
        if is_doc:
            return ScopeExpansionCategory.DOCUMENTATION

        if is_new_file:
            return ScopeExpansionCategory.MULTI_FILE_COMPONENT_MIGRATION

        return ScopeExpansionCategory.UNAUTHORIZED

    def request_scope_expansion(
        self,
        rel_path: str,
        reason: str,
        is_new_file: bool = False,
        task_prompt: str = "",
        classification: Optional[ScopeExpansionCategory] = None,
    ) -> Tuple[bool, str]:
        """Audited Fusion-owned evaluation of requested scope expansion.

        Flow:
        1. Classify reason and target file category.
        2. Validate relationship and necessity against requirements and conventions.
        3. Approve or deny with structured justification.
        4. If approved, persist scope_expansion_reason and authorize path.
        """
        norm_path = rel_path.replace("\\", "/").strip("./")
        filename = Path(norm_path).name.lower()
        prompt_lower = (task_prompt or "").lower()
        reason_lower = (reason or "").lower()

        cat = classification or self.classify_expansion_request(norm_path, reason, is_new_file=is_new_file)

        # 1. Existing Test Update Category:
        # Requires verified API/interface/signature change necessity
        if cat == ScopeExpansionCategory.TEST_UPDATE_API_CHANGE:
            api_change_patterns = [
                r"\b(api|interface|signature|parameter|contract|breaking|rename|renamed|return\s+type|method\s+signature)\b",
            ]
            proves_api_change = any(re.search(p, reason_lower) for p in api_change_patterns)

            # Relationship check: Does test file correspond to a component in expected or allowed related files?
            test_stem = Path(norm_path).stem
            if test_stem.startswith("test_"):
                target_stem = test_stem[5:]
            elif test_stem.endswith("_test"):
                target_stem = test_stem[:-5]
            else:
                target_stem = test_stem

            matches_related = (
                norm_path in self.allowed_related_files
                or any(target_stem in ef for ef in self.expected_files)
                or not self.expected_files
            )

            if proves_api_change and matches_related:
                self.scope_expansion_reasons[norm_path] = f"Approved test update for API/interface change: {reason}"
                if norm_path not in self.expected_files:
                    self.expected_files.append(norm_path)
                return True, f"Scope expansion approved: verified API/interface change necessitates updating '{norm_path}'."
            else:
                return False, f"Scope expansion denied for existing test file '{norm_path}': test modification requires verified API/interface change necessity."

        # 2. Dependency / Config File Category:
        # Requires genuine implementation dependency requirement
        if cat == ScopeExpansionCategory.DEPENDENCY_CONFIG_REQUIRED:
            dep_necessity_patterns = [
                r"\b(required\s+(new\s+)?dependenc(y|ies)|new\s+(library|package)|install\s+package|package\s+addition|build\s+dependency|missing\s+dependency|needed\s+for)\b",
            ]
            proves_necessity = any(re.search(p, reason_lower) for p in dep_necessity_patterns)
            # Must not be generic churn
            is_cosmetic_or_churn = any(kw in reason_lower for kw in ["format", "cleanup", "bump version", "tidy", "reorder"])

            if proves_necessity and not is_cosmetic_or_churn:
                self.scope_expansion_reasons[norm_path] = f"Approved dependency/config addition: {reason}"
                if norm_path not in self.expected_files:
                    self.expected_files.append(norm_path)
                return True, f"Scope expansion approved for dependency file '{norm_path}': verified implementation necessity."
            else:
                return False, f"Scope expansion denied for dependency file '{norm_path}': unverified or opportunistic dependency modification."

        # 3. Multi-file Component / Migration Category:
        # Legitimate new implementation files or database migrations for a multi-file feature
        if cat == ScopeExpansionCategory.MULTI_FILE_COMPONENT_MIGRATION:
            is_migration = "migration" in norm_path.lower() or "migrate" in reason_lower
            norm_dir = str(Path(norm_path).parent).replace("\\", "/").strip("./")
            expected_dirs = {
                str(Path(ef).parent).replace("\\", "/").strip("./")
                for ef in self.expected_files
                if Path(ef).parent
            }
            is_valid_dir = (
                is_migration
                or norm_dir in ("src", "lib", "app", "migrations", "packages")
                or norm_dir in expected_dirs
                or not self.expected_files
            )

            component_necessity_patterns = [
                r"\b(migration|component|submodule|service|model|schema|helper|multi-file|feature\s+integration|module)\b",
            ]
            proves_component_need = any(re.search(p, reason_lower) for p in component_necessity_patterns)

            if is_valid_dir and (proves_component_need or is_migration):
                self.scope_expansion_reasons[norm_path] = f"Approved new implementation component: {reason}"
                if norm_path not in self.expected_files:
                    self.expected_files.append(norm_path)
                return True, f"Scope expansion approved for new file '{norm_path}': legitimate feature component."
            else:
                return False, f"Scope expansion denied for file '{norm_path}': unauthorized path or unverified necessity."

        # 4. Documentation Category:
        # Denied unless explicitly requested in task
        if cat == ScopeExpansionCategory.DOCUMENTATION:
            doc_requested = any(kw in prompt_lower for kw in ["readme", "document", "doc", "notes", "changelog"])
            if doc_requested:
                self.scope_expansion_reasons[norm_path] = f"Approved documentation: {reason}"
                if norm_path not in self.expected_files:
                    self.expected_files.append(norm_path)
                return True, f"Scope expansion approved for documentation file '{norm_path}'."
            else:
                return False, f"Scope expansion denied for documentation file '{norm_path}': documentation is not a requested deliverable."

        return False, f"Scope expansion denied for '{norm_path}': unauthorized scope expansion request."

    def validate_file(
        self,
        rel_path: str,
        is_new_file: bool = False,
        task_prompt: str = "",
        explicit_reason: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Validate whether a target file modification is within authorized scope.

        Returns (is_allowed, reason).
        """
        norm_path = rel_path.replace("\\", "/").strip("./")
        filename = Path(norm_path).name.lower()
        ext = Path(norm_path).suffix.lower()
        prompt_lower = task_prompt.lower()

        # 0. Check if file was already approved through an audited scope expansion
        if norm_path in self.scope_expansion_reasons:
            return True, f"File '{norm_path}' authorized via scope expansion: {self.scope_expansion_reasons[norm_path]}"

        # If explicit reason is provided, run through audited request_scope_expansion
        if explicit_reason:
            expanded, exp_reason = self.request_scope_expansion(
                rel_path=norm_path,
                reason=explicit_reason,
                is_new_file=is_new_file,
                task_prompt=task_prompt,
            )
            if expanded:
                return True, exp_reason
            return False, exp_reason

        # 1. Guard against opportunistic dependency file modifications
        if filename in self.DISALLOWED_DEPENDENCY_FILES:
            if filename not in prompt_lower and norm_path not in self.expected_files:
                return False, f"Opportunistic modification of dependency file '{norm_path}' rejected: not requested in task."

        # 2. Guard against opportunistic documentation creation/modification
        is_doc = (ext in self.DISALLOWED_DOC_EXTENSIONS) or (
            ext == ".txt" and (
                "doc" in norm_path.lower()
                or any(d in filename for d in ["readme", "notes", "changelog", "license", "todo", "guide"])
            )
        )
        if is_doc:
            doc_requested = any(kw in prompt_lower for kw in ["readme", "document", "doc", "notes", "changelog"])
            if ext not in prompt_lower and norm_path not in self.expected_files and not doc_requested:
                return False, f"Opportunistic documentation file '{norm_path}' rejected: documentation not requested."

        # 3. Guard against unauthorized test churn / opportunistic modification of existing test files
        is_test_file = (
            norm_path.startswith("tests/")
            or norm_path.startswith("test/")
            or filename.startswith("test_")
            or filename.endswith("_test.py")
        )
        if is_test_file and not is_new_file:
            test_requested = any(
                re.search(p, prompt_lower)
                for p in [
                    r"\bupdate\s+(the\s+)?tests?\b",
                    r"\bfix\s+(the\s+)?tests?\b",
                    r"\bmodify\s+(the\s+)?tests?\b",
                    r"\bchange\s+(the\s+)?tests?\b",
                    r"\btest\s+churn\b",
                    r"\btest\s+suite\b",
                    r"\bfailing\s+tests?\b",
                    r"\breproduce\s+tests?\b",
                ]
            )
            if not test_requested:
                return False, f"Opportunistic modification of existing test file '{norm_path}' rejected: unauthorized test modification not requested in task."
            return True, f"Existing test file '{norm_path}' modification permitted by task instructions."

        # 4. If file is in expected files
        if norm_path in self.expected_files:
            return True, f"File '{norm_path}' is explicitly in expected change scope."

        # 5. If file is in allowed related files (e.g. source file pairing or new test files)
        if norm_path in self.allowed_related_files:
            return True, f"File '{norm_path}' is an allowed related file (e.g. test/source pair)."

        # 6. Legitimate new test files
        if is_test_file and is_new_file:
            if self.new_test_files_allowed:
                return True, f"New test file '{norm_path}' creation permitted."
            return False, f"New test file creation disallowed by scope contract: '{norm_path}'."

        # 7. Legitimate new implementation files if new files allowed
        if is_new_file:
            if not self.new_files_allowed:
                return False, f"Creation of new file '{norm_path}' is disallowed by scope contract."

            expected_dirs = {
                str(Path(ef).parent).replace("\\", "/").strip("./")
                for ef in self.expected_files
                if Path(ef).parent
            }
            file_dir = str(Path(norm_path).parent).replace("\\", "/").strip("./")
            if file_dir in expected_dirs or file_dir in ("src", "lib", "app", "migrations", "packages"):
                return True, f"New implementation file '{norm_path}' is in legitimate source directory '{file_dir}'."

        # 8. Open scope if expected_files is empty
        if not self.expected_files:
            return True, f"Open scope: file '{norm_path}' permitted."

        # 9. Adjacent source file in same directory as an expected file
        expected_dirs = {
            str(Path(ef).parent).replace("\\", "/").strip("./")
            for ef in self.expected_files
        }
        file_dir = str(Path(norm_path).parent).replace("\\", "/").strip("./")
        if file_dir in expected_dirs and ext in (".py", ".js", ".ts", ".go", ".rs", ".java", ".c", ".cpp"):
            return True, f"Adjacent source file '{norm_path}' permitted in expected module directory '{file_dir}'."

        return False, f"File '{norm_path}' is outside authorized scope contract (expected: {self.expected_files})."
