"""Language analyzer registry and exports for Milestone 7."""

from typing import List, Optional

from fusion_agent.repository.analyzers.base import LanguageAnalyzer
from fusion_agent.repository.analyzers.generic import GenericLanguageAnalyzer
from fusion_agent.repository.analyzers.python import PythonAnalyzer


class AnalyzerRegistry:
    """Registry coordinating language-specific analyzers."""

    def __init__(self, custom_analyzers: Optional[List[LanguageAnalyzer]] = None):
        self.analyzers: List[LanguageAnalyzer] = custom_analyzers or [
            PythonAnalyzer(),
            GenericLanguageAnalyzer(),
        ]

    def get_analyzer(self, path: str) -> LanguageAnalyzer:
        """Return the first analyzer that supports the given file path."""
        for analyzer in self.analyzers:
            if analyzer.supports(path):
                return analyzer
        return self.analyzers[-1]  # Generic fallback


__all__ = [
    "LanguageAnalyzer",
    "PythonAnalyzer",
    "GenericLanguageAnalyzer",
    "AnalyzerRegistry",
]
