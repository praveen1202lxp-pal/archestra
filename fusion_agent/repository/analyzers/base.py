"""Abstract base interface for language-extensible repository analyzers."""

from abc import ABC, abstractmethod
from typing import List, Set

from fusion_agent.repository.indexer import SymbolDefinition


class LanguageAnalyzer(ABC):
    """Abstract interface defining language-specific symbol extraction and dependency discovery."""

    @abstractmethod
    def supports(self, path: str) -> bool:
        """Return True if this analyzer handles the given file path/extension."""
        pass

    @abstractmethod
    def extract_symbols(self, rel_path: str, content: str) -> List[SymbolDefinition]:
        """Extract classes, functions, and method signatures from file content."""
        pass

    @abstractmethod
    def extract_dependencies(self, rel_path: str, content: str, all_files: Set[str]) -> Set[str]:
        """Discover internal repository files imported or referenced by this file."""
        pass

    @abstractmethod
    def detect_tests(self, rel_path: str, all_files: Set[str]) -> Set[str]:
        """Identify associated test files for this source file, or source files for this test file."""
        pass
