"""Deterministic relevant-file and symbol selector with missing-file convention discovery."""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from fusion_agent.models.context import SymbolReference
from fusion_agent.repository.indexer import RepositoryIndex, SymbolDefinition


@dataclass
class RankedCandidate:
    """A candidate file identified by deterministic relevance rules."""
    rel_path: str
    score: float
    reasons: List[str] = field(default_factory=list)


class RelevantFileSelector:
    """Selects and ranks repository files and symbols deterministically from user task requirements."""

    IDENTIFIER_PATTERN = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b")
    FILE_PATH_PATTERN = re.compile(r"\b[\w\-./\\]+\.[a-zA-Z0-9]{1,5}\b")

    def __init__(self, index: RepositoryIndex):
        self.index = index

    def extract_tokens(self, prompt: str) -> Tuple[Set[str], Set[str]]:
        """Extract candidate identifiers, keywords, and explicit file paths from task prompt."""
        explicit_paths = set()
        for match in self.FILE_PATH_PATTERN.findall(prompt):
            norm = match.replace("\\", "/").strip("./")
            explicit_paths.add(norm)

        identifiers = set()
        for token in self.IDENTIFIER_PATTERN.findall(prompt):
            if token.lower() in {
                "the", "and", "for", "with", "this", "that", "from", "import",
                "class", "def", "file", "test", "task", "code", "run", "must",
                "should", "raise", "return", "function", "write", "between",
            }:
                continue
            identifiers.add(token)
            # Split camelCase / PascalCase into sub-words
            sub_words = re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?=[A-Z][a-z0-9]|\b)", token)
            if len(sub_words) > 1:
                for sw in sub_words:
                    if len(sw) >= 3:
                        identifiers.add(sw)

        return explicit_paths, identifiers

    def select_relevant_files(self, prompt: str) -> Tuple[List[RankedCandidate], List[SymbolReference]]:
        """Rank repository files and extract relevant symbols deterministically."""
        explicit_paths, identifiers = self.extract_tokens(prompt)
        prompt_lower = prompt.lower()

        candidates: Dict[str, RankedCandidate] = {}

        def add_score(path: str, points: float, reason: str):
            norm = path.replace("\\", "/")
            if norm not in self.index.file_tree:
                return
            if norm not in candidates:
                candidates[norm] = RankedCandidate(rel_path=norm, score=0.0, reasons=[])
            candidates[norm].score += points
            candidates[norm].reasons.append(reason)

        # 1. Explicit Path Matches in Prompt (Highest Priority)
        missing_paths = []
        for explicit in explicit_paths:
            found_existing = False
            for rel_path in self.index.file_tree:
                if rel_path == explicit or rel_path.endswith("/" + explicit) or explicit in rel_path:
                    add_score(rel_path, 100.0, f"Explicitly referenced path in prompt ('{explicit}')")
                    found_existing = True
            if not found_existing:
                missing_paths.append(explicit)

        # 2. Convention Discovery for Non-Existent Target Files
        # When user asks to create a new file (e.g. fusion_agent/utils/math_utils.py), inspect:
        # - Sibling files in intended package/directory
        # - Package exports (__init__.py)
        # - Nearest analogous tests or interfaces
        for missing in missing_paths:
            target_p = Path(missing)
            intended_dir = target_p.parent.as_posix().replace("\\", "/")

            if intended_dir and intended_dir != ".":
                # Find sibling files in intended directory
                for rel_path in self.index.file_tree:
                    p_rel = Path(rel_path)
                    if p_rel.parent.as_posix().replace("\\", "/") == intended_dir:
                        if p_rel.name == "__init__.py":
                            add_score(rel_path, 88.0, f"Package export for intended new file (`{missing}`)")
                        else:
                            add_score(rel_path, 80.0, f"Sibling convention file in intended directory `{intended_dir}`")

            # Check if there is an existing test for this missing file
            stem = target_p.stem
            expected_test_names = {f"test_{stem}.py", f"{stem}_test.py"}
            for rel_path in self.index.file_tree:
                if Path(rel_path).name in expected_test_names:
                    add_score(rel_path, 85.0, f"Corresponding test file for intended new file (`{missing}`)")

        # 3. File Stem & Name Matches for Existing Files
        for rel_path in self.index.file_tree:
            file_name = Path(rel_path).name.lower()
            file_stem = Path(rel_path).stem.lower()

            if file_name in prompt_lower or (len(file_stem) >= 4 and file_stem in prompt_lower):
                add_score(rel_path, 90.0, f"File name/stem '{file_stem}' matched in prompt")

            for ident in identifiers:
                if len(ident) >= 3 and ident.lower() == file_stem:
                    add_score(rel_path, 85.0, f"Identifier '{ident}' matches file stem")

        # 4. Symbol Matches in symbol_index
        matched_symbols: List[SymbolDefinition] = []
        for ident in identifiers:
            for sym_name, defs in self.index.symbol_index.items():
                short_name = sym_name.split(".")[-1]
                if ident.lower() == short_name.lower():
                    for d in defs:
                        matched_symbols.append(d)
                        add_score(d.rel_path, 85.0, f"Contains target symbol `{d.kind} {d.name}`")

        # 5. Associate Test Files for matched source files
        current_matched = list(candidates.keys())
        for src_path in current_matched:
            node = self.index.file_tree.get(src_path)
            if node and not node.is_test:
                tests = self.index.test_associations.get(src_path, set())
                for t in tests:
                    add_score(t, 75.0, f"Associated test file for target source `{src_path}`")
            elif node and node.is_test:
                sources = self.index.test_associations.get(src_path, set())
                for s in sources:
                    add_score(s, 75.0, f"Target source file for test `{src_path}`")

        # 6. Graph Expansion: 1-hop direct import dependencies
        for src_path in current_matched:
            deps = self.index.dependency_graph.get(src_path, set())
            for dep in deps:
                add_score(dep, 55.0, f"Direct 1-hop dependency imported by `{src_path}`")

        # 7. Lexical Keyword Occurrences
        for ident in identifiers:
            if len(ident) >= 4 and ident.lower() in prompt_lower:
                kw_matches = self.index.search_keywords(query=ident, max_matches=5)
                for rel_path, line_no, line_snippet in kw_matches:
                    add_score(rel_path, 15.0, f"Contains keyword '{ident}' at line {line_no}")

        # 8. Git uncommitted/changed files boost
        for changed in self.index.git_changed_files:
            if changed in candidates:
                add_score(changed, 20.0, "Recently modified in working tree")

        # Sort candidates descending by score
        ranked = sorted(candidates.values(), key=lambda c: c.score, reverse=True)

        # Build clean SymbolReference objects
        symbol_refs: List[SymbolReference] = []
        seen_syms = set()
        for sym in matched_symbols:
            key = (sym.name, sym.rel_path, sym.line_start)
            if key not in seen_syms:
                seen_syms.add(key)
                symbol_refs.append(
                    SymbolReference(
                        name=sym.name,
                        kind=sym.kind,
                        file_path=sym.rel_path,
                        line_number=sym.line_start,
                        signature=sym.signature,
                        docstring=sym.docstring,
                    )
                )

        return ranked, symbol_refs
