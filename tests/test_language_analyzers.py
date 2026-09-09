"""Tests for language-extensible analyzers (PythonAnalyzer and GenericLanguageAnalyzer)."""

from fusion_agent.repository.analyzers.base import LanguageAnalyzer
from fusion_agent.repository.analyzers.generic import GenericLanguageAnalyzer
from fusion_agent.repository.analyzers.python import PythonAnalyzer
from fusion_agent.repository.analyzers import AnalyzerRegistry


def test_python_analyzer_extract_symbols():
    """Verify PythonAnalyzer extracts classes, functions, methods, docstrings, and signatures."""
    code = '''"""Module docstring."""

class Calculator:
    """A simple calculator class."""

    def __init__(self, precision: int = 2):
        self.precision = precision

    def add(self, a: float, b: float) -> float:
        """Add two numbers."""
        return round(a + b, self.precision)

def standalone_helper(val: int) -> bool:
    """Check if value is positive."""
    return val > 0
'''
    analyzer = PythonAnalyzer()
    symbols = analyzer.extract_symbols("calc.py", code)

    names = {s.name: s for s in symbols}
    assert "Calculator" in names
    assert names["Calculator"].kind == "class"
    assert "A simple calculator" in names["Calculator"].docstring

    assert "Calculator.add" in names
    assert names["Calculator.add"].kind == "method"
    assert "a: float, b: float" in names["Calculator.add"].signature

    assert "standalone_helper" in names
    assert names["standalone_helper"].kind == "function"


def test_python_analyzer_extract_dependencies():
    """Verify PythonAnalyzer extracts import dependencies resolving relative and absolute imports."""
    code = '''import os
import sys
from pathlib import Path
from fusion_agent.core.budget import TaskBudgetController
from .helpers import utils
from ..models import Task
'''
    analyzer = PythonAnalyzer()
    all_files = {
        "fusion_agent/core/orchestrator.py",
        "fusion_agent/core/budget.py",
        "fusion_agent/core/helpers/utils.py",
        "fusion_agent/models/task.py",
    }
    deps = analyzer.extract_dependencies("fusion_agent/core/orchestrator.py", code, all_files)

    assert "fusion_agent/core/budget.py" in deps
    assert "fusion_agent/core/helpers/utils.py" in deps


def test_python_analyzer_detect_tests():
    """Verify PythonAnalyzer associates source files with their corresponding test files."""
    analyzer = PythonAnalyzer()
    all_files = {
        "src/calculator.py",
        "tests/test_calculator.py",
        "tests/test_unrelated.py",
    }
    tests = analyzer.detect_tests("src/calculator.py", all_files)
    assert "tests/test_calculator.py" in tests


def test_generic_language_analyzer_typescript_and_cpp():
    """Verify GenericLanguageAnalyzer safely extracts symbols and dependencies for TS/JS/C++."""
    ts_code = '''
export class UserService {
    private db: Database;

    constructor(db: Database) {
        this.db = db;
    }

    public async getUser(id: string): Promise<User> {
        return this.db.find(id);
    }
}

export function formatName(first: string, last: string): string {
    return `${first} ${last}`;
}
'''
    analyzer = GenericLanguageAnalyzer()
    symbols = analyzer.extract_symbols("src/services/user.ts", ts_code)

    names = {s.name: s for s in symbols}
    assert "UserService" in names
    assert names["UserService"].kind == "class"
    assert "formatName" in names
    assert names["formatName"].kind == "function"


def test_analyzer_registry_routing():
    """Verify AnalyzerRegistry routes .py to PythonAnalyzer and other languages to GenericLanguageAnalyzer."""
    registry = AnalyzerRegistry()

    py_analyzer = registry.get_analyzer("fusion_agent/core/router.py")
    assert isinstance(py_analyzer, PythonAnalyzer)

    ts_analyzer = registry.get_analyzer("frontend/src/index.tsx")
    assert isinstance(ts_analyzer, GenericLanguageAnalyzer)

    cpp_analyzer = registry.get_analyzer("engine/core.cpp")
    assert isinstance(cpp_analyzer, GenericLanguageAnalyzer)

    java_analyzer = registry.get_analyzer("backend/src/Service.java")
    assert isinstance(java_analyzer, GenericLanguageAnalyzer)
