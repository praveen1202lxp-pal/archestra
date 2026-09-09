"""Generic safe lexical analyzer fallback for non-Python languages."""

import re
from pathlib import Path
from typing import List, Set

from fusion_agent.repository.analyzers.base import LanguageAnalyzer
from fusion_agent.repository.indexer import SymbolDefinition


class GenericLanguageAnalyzer(LanguageAnalyzer):
    """Fallback lexical analyzer supporting JS/TS, C++, Java, Go, and other source formats."""

    # Regex patterns for common class and function declarations across languages
    CLASS_PATTERN = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:public\s+|private\s+|protected\s+)?class\s+([A-Za-z0-9_]+)", re.MULTILINE)
    FUNC_PATTERN = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?(?:function|def|fn|func)\s+([A-Za-z0-9_]+)\s*\((.*?)\)", re.MULTILINE)
    INTERFACE_PATTERN = re.compile(r"^\s*(?:export\s+)?interface\s+([A-Za-z0-9_]+)", re.MULTILINE)
    IMPORT_PATTERN = re.compile(r"""(?:import\s+.*?from\s+['"](.*?)['"]|#include\s+["<](.*?)[">]|require\(['"](.*?)['"]\))""")

    def supports(self, path: str) -> bool:
        # Fallback analyzer supports everything
        return True

    def extract_symbols(self, rel_path: str, content: str) -> List[SymbolDefinition]:
        """Extract top-level class, interface, and function symbols via regex."""
        symbols: List[SymbolDefinition] = []
        lines = content.splitlines()

        for i, line in enumerate(lines, start=1):
            c_match = self.CLASS_PATTERN.match(line)
            if c_match:
                symbols.append(
                    SymbolDefinition(
                        name=c_match.group(1),
                        kind="class",
                        rel_path=rel_path,
                        line_start=i,
                        line_end=i,
                        signature=line.strip(),
                    )
                )
                continue

            i_match = self.INTERFACE_PATTERN.match(line)
            if i_match:
                symbols.append(
                    SymbolDefinition(
                        name=i_match.group(1),
                        kind="interface",
                        rel_path=rel_path,
                        line_start=i,
                        line_end=i,
                        signature=line.strip(),
                    )
                )
                continue

            f_match = self.FUNC_PATTERN.match(line)
            if f_match:
                symbols.append(
                    SymbolDefinition(
                        name=f_match.group(1),
                        kind="function",
                        rel_path=rel_path,
                        line_start=i,
                        line_end=i,
                        signature=line.strip(),
                    )
                )

        return symbols

    def extract_dependencies(self, rel_path: str, content: str, all_files: Set[str]) -> Set[str]:
        """Discover import references across JS/TS or C/C++ includes."""
        dependencies: Set[str] = set()
        curr_dir = Path(rel_path).parent

        for match in self.IMPORT_PATTERN.finditer(content):
            ref = match.group(1) or match.group(2) or match.group(3)
            if not ref:
                continue

            # Try resolving relative imports like './utils' or '../math'
            if ref.startswith("."):
                resolved_base = (curr_dir / ref).as_posix().replace("\\", "/")
                for ext in [".ts", ".js", ".tsx", ".jsx", ".h", ".hpp", ".cpp", ""]:
                    cand = resolved_base + ext
                    norm = cand.replace("/./", "/").strip("./")
                    if norm in all_files:
                        dependencies.add(norm)
                        break

        return dependencies

    def detect_tests(self, rel_path: str, all_files: Set[str]) -> Set[str]:
        """Detect spec/test files for generic languages."""
        tests: Set[str] = set()
        stem = Path(rel_path).stem
        ext = Path(rel_path).suffix

        is_test = ".test" in rel_path or ".spec" in rel_path or "/test" in rel_path
        if not is_test:
            candidate_names = {
                f"{stem}.test{ext}",
                f"{stem}.spec{ext}",
                f"{stem}_test{ext}",
            }
            for f in all_files:
                if Path(f).name in candidate_names:
                    tests.add(f)
        return tests
