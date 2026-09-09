"""Deterministic relevant-file and symbol selector with confidence-gated graph expansion."""

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from fusion_agent.models.context import SymbolReference
from fusion_agent.repository.indexer import RepositoryIndex, SymbolDefinition


class MatchConfidence(str, Enum):
    """Source and confidence level of a file match."""
    EXPLICIT_PATH = "EXPLICIT_PATH"
    EXACT_SYMBOL = "EXACT_SYMBOL"
    EXACT_FILENAME = "EXACT_FILENAME"
    SIBLING_CONVENTION = "SIBLING_CONVENTION"
    DEPENDENCY = "DEPENDENCY"
    LEXICAL_KEYWORD = "LEXICAL_KEYWORD"


CONFIDENCE_PRIORITY: Dict[MatchConfidence, int] = {
    MatchConfidence.EXPLICIT_PATH: 6,
    MatchConfidence.EXACT_SYMBOL: 5,
    MatchConfidence.EXACT_FILENAME: 4,
    MatchConfidence.SIBLING_CONVENTION: 3,
    MatchConfidence.DEPENDENCY: 2,
    MatchConfidence.LEXICAL_KEYWORD: 1,
}


@dataclass
class RankedCandidate:
    """A candidate file identified by deterministic relevance rules."""
    rel_path: str
    score: float
    confidence: MatchConfidence = MatchConfidence.LEXICAL_KEYWORD
    reasons: List[str] = field(default_factory=list)


class RelevantFileSelector:
    """Selects and ranks repository files and symbols deterministically from user task requirements."""

    IDENTIFIER_PATTERN = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b")
    FILE_PATH_PATTERN = re.compile(r"\b[\w\-./\\]+\.[a-zA-Z0-9]{1,5}\b")

    GENERIC_STOPWORDS: Set[str] = {
        "the", "and", "for", "with", "this", "that", "from", "import",
        "class", "def", "file", "test", "tests", "testing", "task", "code", "run", "must",
        "should", "raise", "return", "function", "functions", "method", "methods", "write",
        "between", "type", "types", "value", "values", "error", "errors", "input", "inputs",
        "output", "outputs", "argument", "arguments", "parameter", "parameters", "name",
        "names", "true", "false", "none", "boolean", "booleans", "string", "strings",
        "integer", "integers", "float", "floats", "number", "numbers", "check", "checks",
        "case", "cases", "unit", "suite", "implement", "create", "robust", "new", "patch",
        "diff", "review", "passed", "failed", "valid", "invalid", "help", "doc", "docs",
        "python", "py", "javascript", "typescript", "json", "yaml", "toml", "bash", "sh",
        "sql", "html", "css", "markdown", "md",
    }

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
            if token.lower() in self.GENERIC_STOPWORDS:
                continue
            identifiers.add(token)
            # Split camelCase / PascalCase into sub-words
            sub_words = re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?=[A-Z][a-z0-9]|\b)", token)
            if len(sub_words) > 1:
                for sw in sub_words:
                    if len(sw) >= 3 and sw.lower() not in self.GENERIC_STOPWORDS:
                        identifiers.add(sw)

        return explicit_paths, identifiers

    def select_relevant_files(self, prompt: str) -> Tuple[List[RankedCandidate], List[SymbolReference]]:
        """Rank repository files and extract relevant symbols deterministically."""
        explicit_paths, identifiers = self.extract_tokens(prompt)
        prompt_lower = prompt.lower()

        candidates: Dict[str, RankedCandidate] = {}

        def add_score(
            path: str,
            points: float,
            reason: str,
            confidence: MatchConfidence = MatchConfidence.LEXICAL_KEYWORD,
        ):
            norm = path.replace("\\", "/")
            if norm not in self.index.file_tree:
                return
            if norm not in candidates:
                candidates[norm] = RankedCandidate(
                    rel_path=norm,
                    score=0.0,
                    confidence=confidence,
                    reasons=[],
                )
            else:
                # Upgrade confidence if new confidence has higher priority
                if CONFIDENCE_PRIORITY[confidence] > CONFIDENCE_PRIORITY[candidates[norm].confidence]:
                    candidates[norm].confidence = confidence

            candidates[norm].score += points
            candidates[norm].reasons.append(reason)

        # 1. Explicit Path Matches in Prompt (Highest Priority)
        missing_paths = []
        for explicit in explicit_paths:
            found_existing = False
            for rel_path in self.index.file_tree:
                if rel_path == explicit or rel_path.endswith("/" + explicit) or explicit in rel_path:
                    add_score(rel_path, 100.0, f"Explicitly referenced path in prompt ('{explicit}')", MatchConfidence.EXPLICIT_PATH)
                    found_existing = True
            if not found_existing:
                missing_paths.append(explicit)

        # 2. Convention Discovery for Non-Existent Target Files
        # When user asks to create a new file (e.g. fusion_agent/utils/math_utils.py):
        # - Inspect sibling files ONLY if intended directory actually exists in the index
        # - Top-level test directories ("tests", "test") are not treated as sibling directories
        # - Package exports (__init__.py)
        # - Nearest analogous tests on disk
        for missing in missing_paths:
            target_p = Path(missing)
            intended_dir = target_p.parent.as_posix().replace("\\", "/")

            is_top_level_test_dir = intended_dir.lower() in ("tests", "test", "testing")
            if intended_dir and intended_dir != "." and not is_top_level_test_dir:
                dir_exists = any(Path(p).parent.as_posix().replace("\\", "/") == intended_dir for p in self.index.file_tree)
                if dir_exists:
                    for rel_path in self.index.file_tree:
                        p_rel = Path(rel_path)
                        if p_rel.parent.as_posix().replace("\\", "/") == intended_dir:
                            if p_rel.name == "__init__.py":
                                add_score(rel_path, 88.0, f"Package export for intended new file (`{missing}`)", MatchConfidence.SIBLING_CONVENTION)
                            else:
                                add_score(rel_path, 80.0, f"Sibling convention file in intended directory `{intended_dir}`", MatchConfidence.SIBLING_CONVENTION)

            # Check if there is an existing test for this missing file
            stem = target_p.stem
            expected_test_names = {f"test_{stem}.py", f"{stem}_test.py"}
            for rel_path in self.index.file_tree:
                if Path(rel_path).name in expected_test_names:
                    add_score(rel_path, 85.0, f"Corresponding test file for intended new file (`{missing}`)", MatchConfidence.SIBLING_CONVENTION)

        # 3. File Stem & Exact Name Matches for Existing Files
        for rel_path in self.index.file_tree:
            file_name = Path(rel_path).name.lower()
            file_stem = Path(rel_path).stem.lower()

            if file_stem in self.GENERIC_STOPWORDS:
                continue

            if file_name in prompt_lower or (len(file_stem) >= 4 and file_stem in prompt_lower):
                add_score(rel_path, 90.0, f"File name/stem '{file_stem}' matched in prompt", MatchConfidence.EXACT_FILENAME)

            for ident in identifiers:
                if len(ident) >= 3 and ident.lower() == file_stem:
                    add_score(rel_path, 85.0, f"Identifier '{ident}' matches file stem", MatchConfidence.EXACT_FILENAME)

        # 4. Symbol Matches in symbol_index
        matched_symbols: List[SymbolDefinition] = []
        for ident in identifiers:
            for sym_name, defs in self.index.symbol_index.items():
                short_name = sym_name.split(".")[-1]
                if ident.lower() == short_name.lower():
                    for d in defs:
                        matched_symbols.append(d)
                        add_score(d.rel_path, 85.0, f"Contains target symbol `{d.kind} {d.name}`", MatchConfidence.EXACT_SYMBOL)

        # 5. Seed-Gated Graph Expansion & Test Associations
        # CRITICAL SAFETY INVARIANT: Only high-confidence seeds (explicit paths, exact symbols, exact filenames)
        # are permitted to seed test associations and dependency graph expansion.
        # Weak lexical matches MUST NEVER recursively trigger graph expansion!
        high_confidence_seeds = [
            rel_path for rel_path, cand in candidates.items()
            if cand.confidence in (
                MatchConfidence.EXPLICIT_PATH,
                MatchConfidence.EXACT_SYMBOL,
                MatchConfidence.EXACT_FILENAME,
            )
        ]

        # 5a. Associate Test Files for high-confidence source files
        for src_path in high_confidence_seeds:
            node = self.index.file_tree.get(src_path)
            if node and not node.is_test:
                tests = self.index.test_associations.get(src_path, set())
                for t in tests:
                    add_score(t, 75.0, f"Associated test file for target source `{src_path}`", MatchConfidence.DEPENDENCY)
            elif node and node.is_test:
                sources = self.index.test_associations.get(src_path, set())
                for s in sources:
                    add_score(s, 75.0, f"Target source file for test `{src_path}`", MatchConfidence.DEPENDENCY)

        # 5b. Graph Expansion: 1-hop direct import dependencies for high-confidence source files
        for src_path in high_confidence_seeds:
            deps = self.index.dependency_graph.get(src_path, set())
            for dep in deps:
                add_score(dep, 55.0, f"Direct 1-hop dependency imported by `{src_path}`", MatchConfidence.DEPENDENCY)

        # 6. Lexical Keyword Occurrences (strictly isolated, non-expanding)
        # Only evaluate lexical keywords if high-confidence matches were found or explicit paths were specified
        has_anchor = bool(high_confidence_seeds) or bool(missing_paths)
        if has_anchor:
            for ident in identifiers:
                if len(ident) >= 4 and ident.lower() in prompt_lower:
                    kw_matches = self.index.search_keywords(query=ident, max_matches=3)
                    for rel_path, line_no, line_snippet in kw_matches:
                        add_score(rel_path, 15.0, f"Contains keyword '{ident}' at line {line_no}", MatchConfidence.LEXICAL_KEYWORD)

        # 7. Git uncommitted/changed files boost (only if already candidate)
        for changed in self.index.git_changed_files:
            if changed in candidates:
                add_score(changed, 20.0, "Recently modified in working tree", candidates[changed].confidence)

        # 8. Filter low-confidence clutter:
        # If there are NO high-confidence seeds or sibling conventions, do NOT return low-confidence lexical matches!
        # "An empty relevant context is better than irrelevant context."
        filtered_candidates: List[RankedCandidate] = []
        has_high_or_convention = any(
            c.confidence in (
                MatchConfidence.EXPLICIT_PATH,
                MatchConfidence.EXACT_SYMBOL,
                MatchConfidence.EXACT_FILENAME,
                MatchConfidence.SIBLING_CONVENTION,
            )
            for c in candidates.values()
        )

        for c in candidates.values():
            if not has_high_or_convention and c.confidence == MatchConfidence.LEXICAL_KEYWORD:
                continue
            if c.confidence == MatchConfidence.LEXICAL_KEYWORD and c.score < 50.0:
                continue
            filtered_candidates.append(c)

        # Sort candidates descending by score and confidence priority
        ranked = sorted(
            filtered_candidates,
            key=lambda c: (CONFIDENCE_PRIORITY[c.confidence], c.score),
            reverse=True,
        )

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
