"""Language-extensible, secret-aware repository indexer for Milestone 7."""

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from fusion_agent.repository.ignore import IgnoreManager, SecretFilter


@dataclass
class FileNode:
    """Metadata for an indexed repository file."""
    path: str
    rel_path: str
    language: str
    size_bytes: int
    line_count: int
    is_test: bool = False


@dataclass
class SymbolDefinition:
    """A class, function, or method definition discovered in the repository."""
    name: str
    kind: str  # "class", "function", "method", "interface"
    rel_path: str
    line_start: int
    line_end: int
    signature: str = ""
    docstring: str = ""


@dataclass
class ImportDependency:
    """An import relationship between files in the repository."""
    source_file: str
    imported_module: str
    imported_symbols: List[str] = field(default_factory=list)
    resolved_rel_path: Optional[str] = None


@dataclass
class RepositoryIndex:
    """In-memory deterministic index of a codebase."""
    root_path: str
    file_tree: Dict[str, FileNode] = field(default_factory=dict)
    symbol_index: Dict[str, List[SymbolDefinition]] = field(default_factory=dict)
    dependency_graph: Dict[str, Set[str]] = field(default_factory=dict)  # file -> files it imports
    reverse_dependency_graph: Dict[str, Set[str]] = field(default_factory=dict)  # file -> files importing it
    test_associations: Dict[str, Set[str]] = field(default_factory=dict)  # source file <-> test file
    git_changed_files: List[str] = field(default_factory=list)

    def get_file_content(self, rel_path: str) -> Optional[str]:
        """Read full content of a relative file path."""
        norm = rel_path.replace("\\", "/")
        node = self.file_tree.get(norm)
        if not node:
            return None
        try:
            with open(node.path, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
        except Exception:
            return None

    def search_symbols(self, query: str) -> List[SymbolDefinition]:
        """Search symbol definitions by symbol name substring."""
        results = []
        q_low = query.lower()
        for name, defs in self.symbol_index.items():
            if q_low == name.lower() or q_low in name.lower():
                results.extend(defs)
        return results

    def search_keywords(
        self,
        query: str,
        max_matches: int = 10,
    ) -> List[Tuple[str, int, str]]:
        """Search raw file contents for keyword occurrences."""
        matches = []
        q_low = query.lower()
        for rel_path, node in self.file_tree.items():
            content = self.get_file_content(rel_path)
            if content and q_low in content.lower():
                for i, line in enumerate(content.splitlines(), start=1):
                    if q_low in line.lower():
                        matches.append((rel_path, i, line.strip()))
                        if len(matches) >= max_matches:
                            return matches
                        break
        return matches


class RepositoryIndexer:
    """Discovers, indexes, and analyzes repository layout and dependencies delegating to LanguageAnalyzers."""

    LANGUAGE_MAP = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".jsx": "javascript",
        ".tsx": "typescript",
        ".json": "json",
        ".toml": "toml",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".md": "markdown",
        ".sh": "bash",
        ".ps1": "powershell",
        ".html": "html",
        ".css": "css",
        ".sql": "sql",
        ".cpp": "cpp",
        ".c": "c",
        ".h": "c",
        ".hpp": "cpp",
        ".java": "java",
        ".go": "go",
        ".rs": "rust",
        ".txt": "text",
    }

    def __init__(self, analyzer_registry: Optional[Any] = None):
        # Lazy import to avoid circular dependencies
        if analyzer_registry is None:
            from fusion_agent.repository.analyzers import AnalyzerRegistry
            self.analyzer_registry = AnalyzerRegistry()
        else:
            self.analyzer_registry = analyzer_registry

    def index_project(self, repo_root: Union[str, Path]) -> RepositoryIndex:
        """Deterministically index an entire project directory."""
        root = Path(repo_root).resolve()
        index = RepositoryIndex(root_path=str(root))

        if not root.is_dir():
            return index

        ignore_mgr = IgnoreManager(root)

        # 1. File tree traversal with secret-aware ignore filtering
        for current_root, dirs, files in os.walk(root):
            # Exclude ignored dirs in-place
            dirs[:] = [
                d for d in dirs
                if not ignore_mgr.should_ignore(os.path.relpath(os.path.join(current_root, d), root))
            ]

            for file_name in files:
                abs_path = os.path.join(current_root, file_name)
                rel_path = os.path.relpath(abs_path, root).replace("\\", "/")

                if ignore_mgr.should_ignore(rel_path):
                    continue

                try:
                    stat = os.stat(abs_path)
                    size = stat.st_size
                except OSError:
                    continue

                line_count = 0
                try:
                    with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                        line_count = sum(1 for _ in f)
                except Exception:
                    pass

                ext = os.path.splitext(file_name)[1].lower()
                lang = self.LANGUAGE_MAP.get(ext, "text")
                is_test = self._is_test_file(rel_path, file_name)

                node = FileNode(
                    path=abs_path,
                    rel_path=rel_path,
                    language=lang,
                    size_bytes=size,
                    line_count=line_count,
                    is_test=is_test,
                )
                index.file_tree[rel_path] = node

        # 2. Extract Symbols, Dependencies, and Tests via modular LanguageAnalyzers
        all_files = set(index.file_tree.keys())

        for rel_path, node in index.file_tree.items():
            analyzer = self.analyzer_registry.get_analyzer(rel_path)
            content = index.get_file_content(rel_path)
            if not content:
                continue

            # Extract symbols
            symbols = analyzer.extract_symbols(rel_path, content)
            for sym in symbols:
                if sym.name not in index.symbol_index:
                    index.symbol_index[sym.name] = []
                index.symbol_index[sym.name].append(sym)

            # Extract dependencies
            deps = analyzer.extract_dependencies(rel_path, content, all_files)
            index.dependency_graph[rel_path] = deps
            for dep in deps:
                index.reverse_dependency_graph.setdefault(dep, set()).add(rel_path)

            # Detect test associations
            tests = analyzer.detect_tests(rel_path, all_files)
            if tests:
                index.test_associations.setdefault(rel_path, set()).update(tests)
                for t in tests:
                    index.test_associations.setdefault(t, set()).add(rel_path)

        # 3. Detect Git uncommitted changes if in a git repo
        index.git_changed_files = self._detect_git_changes(str(root))

        return index

    def _is_test_file(self, rel_path: str, file_name: str) -> bool:
        low_name = file_name.lower()
        low_path = rel_path.lower()
        if low_name.startswith("test_") or low_name.endswith("_test.py") or ".test." in low_name or ".spec." in low_name:
            return True
        if "/tests/" in low_path or low_path.startswith("tests/") or "/test/" in low_path:
            return True
        return False

    def _detect_git_changes(self, repo_root: str) -> List[str]:
        try:
            proc = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if proc.returncode != 0:
                return []
            changes = []
            for line in proc.stdout.splitlines():
                line = line.strip()
                if len(line) >= 3:
                    target = line[2:].strip().split("->")[-1].strip().replace("\\", "/")
                    changes.append(target)
            return changes
        except Exception:
            return []

    def search_symbols(self, index: RepositoryIndex, query: str) -> List[SymbolDefinition]:
        results = []
        q_low = query.lower()
        for name, defs in index.symbol_index.items():
            if q_low == name.lower() or q_low in name.lower():
                results.extend(defs)
        return results

    def search_keywords(
        self,
        index: RepositoryIndex,
        query: str,
        max_matches: int = 10,
    ) -> List[Tuple[str, int, str]]:
        matches = []
        q_low = query.lower()
        for rel_path, node in index.file_tree.items():
            content = index.get_file_content(rel_path)
            if content and q_low in content.lower():
                for i, line in enumerate(content.splitlines(), start=1):
                    if q_low in line.lower():
                        matches.append((rel_path, i, line.strip()))
                        if len(matches) >= max_matches:
                            return matches
                        break
        return matches
