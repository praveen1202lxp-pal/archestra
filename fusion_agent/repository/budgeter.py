"""Strict, deterministic context budgeter and omission manifest tracker for Milestone 7."""

from typing import Dict, List, Optional, Tuple

from fusion_agent.models.context import (
    CodeContext,
    ContextBudgetConfig,
    ContextSliceType,
    OmissionManifest,
    SelectedFile,
    SymbolReference,
)
from fusion_agent.repository.indexer import RepositoryIndex
from fusion_agent.repository.selector import MatchConfidence, RankedCandidate


class ContextBudgeter:
    """Enforces strict character and file counts on CodeContext with zero silent truncation."""

    def __init__(self, config: Optional[ContextBudgetConfig] = None):
        self.config = config or ContextBudgetConfig()

    def build_code_context(
        self,
        task_requirements: str,
        ranked_candidates: List[RankedCandidate],
        symbol_refs: List[SymbolReference],
        index: RepositoryIndex,
        architecture_decisions: str = "",
        test_failures: Optional[str] = None,
        current_diff: Optional[str] = None,
        peer_feedback: Optional[str] = None,
        worktree_file_overrides: Optional[Dict[str, str]] = None,
    ) -> CodeContext:
        """Construct normalized CodeContext obeying strict hierarchical budgets."""
        omissions = OmissionManifest()
        selected_files: List[SelectedFile] = []
        current_source_chars = 0

        # Map symbol definitions by file for targeted slice extraction
        symbols_by_file: Dict[str, List[SymbolReference]] = {}
        for sym in symbol_refs:
            symbols_by_file.setdefault(sym.file_path, []).append(sym)

        # 1. Select and budget source files
        for candidate in ranked_candidates:
            rel_path = candidate.rel_path
            node = index.file_tree.get(rel_path)
            if not node:
                continue

            is_high_priority = getattr(candidate, "confidence", None) in (
                MatchConfidence.EXPLICIT_PATH,
                MatchConfidence.EXACT_SYMBOL,
                MatchConfidence.EXACT_FILENAME,
            )

            # Check max_files limit
            if len(selected_files) >= self.config.max_files:
                if is_high_priority:
                    omissions.omitted_files.append({
                        "path": rel_path,
                        "reason": f"Exceeded max_files budget ({self.config.max_files})",
                        "high_priority": True,
                    })
                else:
                    omissions.omitted_low_confidence_count += 1
                continue

            # Retrieve content: worktree override takes precedence (e.g. during repair)
            content = None
            if worktree_file_overrides and rel_path in worktree_file_overrides:
                content = worktree_file_overrides[rel_path]
            else:
                content = index.get_file_content(rel_path)

            if content is None:
                continue

            orig_len = len(content)
            reasons_str = "; ".join(candidate.reasons[:2]) if candidate.reasons else "Relevance match"

            # Check if file fits within total_source_chars
            remaining_total_budget = self.config.total_source_chars - current_source_chars
            if remaining_total_budget <= 0:
                if is_high_priority:
                    omissions.omitted_files.append({
                        "path": rel_path,
                        "reason": f"Exceeded total_source_chars budget ({self.config.total_source_chars})",
                        "high_priority": True,
                    })
                else:
                    omissions.omitted_low_confidence_count += 1
                continue

            effective_file_budget = min(self.config.max_chars_per_file, remaining_total_budget)

            # Determine whether to use symbol slicing, convention slicing, or full file
            cand_conf = getattr(candidate, "confidence", None)
            file_symbols = symbols_by_file.get(rel_path, [])

            if cand_conf == MatchConfidence.SIBLING_CONVENTION:
                slice_type = ContextSliceType.CONVENTION_SLICE
            elif file_symbols and orig_len > 2_000:
                slice_type = ContextSliceType.SYMBOL_SLICE
            else:
                slice_type = ContextSliceType.FULL_FILE

            if slice_type == ContextSliceType.SYMBOL_SLICE:
                # Prefer matched function/class + nearby imports + +-30 lines
                sliced_content, line_range = self._extract_symbol_slice(
                    content=content,
                    relevant_symbols=file_symbols,
                    max_chars=effective_file_budget,
                )
                sf = SelectedFile(
                    path=rel_path,
                    content=sliced_content,
                    language=node.language,
                    relevance_reason=reasons_str,
                    is_full_file=False,
                    line_range=line_range,
                    char_count=len(sliced_content),
                    slice_type=ContextSliceType.SYMBOL_SLICE,
                )
                selected_files.append(sf)
                current_source_chars += len(sliced_content)
            elif orig_len <= effective_file_budget:
                # File fits completely
                sf = SelectedFile(
                    path=rel_path,
                    content=content,
                    language=node.language,
                    relevance_reason=reasons_str,
                    is_full_file=True,
                    char_count=orig_len,
                    slice_type=slice_type,
                )
                selected_files.append(sf)
                current_source_chars += orig_len
            else:
                # File must be truncated
                truncated_content, line_range = self._truncate_content(
                    content=content,
                    max_chars=effective_file_budget,
                    relevant_symbols=file_symbols,
                )
                sf = SelectedFile(
                    path=rel_path,
                    content=truncated_content,
                    language=node.language,
                    relevance_reason=reasons_str,
                    is_full_file=False,
                    line_range=line_range,
                    char_count=len(truncated_content),
                    slice_type=ContextSliceType.SYMBOL_SLICE if file_symbols else ContextSliceType.FULL_FILE,
                )
                selected_files.append(sf)
                current_source_chars += len(truncated_content)

                omissions.truncated_files.append({
                    "path": rel_path,
                    "original_chars": orig_len,
                    "included_chars": len(truncated_content),
                    "reason": f"Truncated to fit per-file/total budget ({effective_file_budget} chars)",
                })

        # 2. Budget Architectural Decisions
        bounded_arch = architecture_decisions.strip()
        if len(bounded_arch) > self.config.max_architecture_chars:
            bounded_arch = bounded_arch[:self.config.max_architecture_chars] + "\n... [truncated architecture summary]"

        # 3. Budget Test Failures (if present)
        bounded_test_failures = None
        if test_failures and test_failures.strip():
            tf_clean = test_failures.strip()
            if len(tf_clean) > self.config.max_test_output_chars:
                excess = len(tf_clean) - self.config.max_test_output_chars
                omissions.omitted_test_output_chars = excess
                bounded_test_failures = tf_clean[:self.config.max_test_output_chars] + f"\n... [truncated {excess} chars of test output]"
            else:
                bounded_test_failures = tf_clean

        # 4. Budget Peer Feedback (if present)
        bounded_peer_feedback = None
        if peer_feedback and peer_feedback.strip():
            pf_clean = peer_feedback.strip()
            if len(pf_clean) > self.config.max_peer_feedback_chars:
                excess = len(pf_clean) - self.config.max_peer_feedback_chars
                omissions.omitted_peer_feedback_chars = excess
                bounded_peer_feedback = pf_clean[:self.config.max_peer_feedback_chars] + f"\n... [truncated {excess} chars of feedback]"
            else:
                bounded_peer_feedback = pf_clean

        # 5. Assemble CodeContext
        code_context = CodeContext(
            task_requirements=task_requirements,
            selected_files=selected_files,
            relevant_symbols=symbol_refs,
            architecture_decisions=bounded_arch,
            test_failures=bounded_test_failures,
            current_diff=current_diff,
            peer_feedback=bounded_peer_feedback,
            omissions=omissions,
        )

        # Pre-compute prompt context and metrics
        code_context.to_prompt_context()
        return code_context

    def _extract_symbol_slice(
        self,
        content: str,
        relevant_symbols: List[SymbolReference],
        max_chars: int,
    ) -> Tuple[str, Tuple[int, int]]:
        """Extract symbol definition +-30 lines along with package header/imports."""
        lines = content.splitlines(keepends=True)
        total_lines = len(lines)

        # 1. Capture file header / imports (lines 1 to 25)
        header_lines = []
        for i, line in enumerate(lines[:25], 1):
            if line.strip().startswith(("import ", "from ", "#", '"""', "'''")) or not line.strip():
                header_lines.append(line)
            else:
                break
        header_text = "".join(header_lines)

        # 2. Window around primary target symbol (+-30 lines)
        sym = relevant_symbols[0]
        target_line = sym.line_number
        start_line = max(1, target_line - 30)
        end_line = min(total_lines, target_line + 30)

        body_lines = lines[start_line - 1 : end_line]
        body_text = "".join(body_lines)

        slice_text = (
            f"# [SYMBOL_SLICE: Header / Imports]\n{header_text}\n"
            f"# [SYMBOL_SLICE: Lines {start_line} to {end_line} of {total_lines} around {sym.kind} {sym.name}]\n"
            f"{body_text}\n"
            f"# [SYMBOL_SLICE: {total_lines - end_line} trailing lines omitted]"
        )
        if len(slice_text) > max_chars:
            slice_text = slice_text[:max_chars - 50] + "\n# [TRUNCATED]"

        return slice_text, (start_line, end_line)

    def _truncate_content(
        self,
        content: str,
        max_chars: int,
        relevant_symbols: List[SymbolReference],
    ) -> Tuple[str, Optional[Tuple[int, int]]]:
        """Truncate content to respect budget while preserving symbol context when possible."""
        lines = content.splitlines(keepends=True)
        total_lines = len(lines)

        # If a relevant symbol is known, try to center around it
        if relevant_symbols:
            target_line = relevant_symbols[0].line_number
            start_idx = max(0, target_line - 30)
            end_idx = min(total_lines, target_line + 70)

            slice_lines = lines[start_idx:end_idx]
            slice_text = "".join(slice_lines)
            if len(slice_text) <= max_chars:
                banner = (
                    f"# [TRUNCATED: Showing lines {start_idx + 1} to {end_idx} of {total_lines} around symbol {relevant_symbols[0].name}]\n"
                    f"{slice_text}\n"
                    f"# [TRUNCATED: {total_lines - end_idx} lines omitted]"
                )
                return banner, (start_idx + 1, end_idx)

        # Fallback: Head truncation
        accum = []
        accum_chars = 0
        end_line = 0
        for i, line in enumerate(lines, start=1):
            if accum_chars + len(line) > max_chars - 100:
                break
            accum.append(line)
            accum_chars += len(line)
            end_line = i

        banner = (
            "".join(accum)
            + f"\n# [TRUNCATED: Showing first {end_line} lines ({accum_chars} chars) out of {total_lines} lines]"
        )
        return banner, (1, end_line)
