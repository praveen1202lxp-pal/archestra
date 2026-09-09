"""Tests for RelevantFileSelector with priority ordering and non-existent file convention discovery."""

from pathlib import Path

from fusion_agent.repository.indexer import RepositoryIndexer
from fusion_agent.repository.selector import RelevantFileSelector


def test_relevant_file_selector_explicit_path_priority(tmp_path):
    """Verify that explicitly named paths receive the highest priority score."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "target.py").write_text("def run(): pass\n", encoding="utf-8")
    (tmp_path / "pkg" / "other.py").write_text("def helper(): pass\n", encoding="utf-8")

    indexer = RepositoryIndexer()
    index = indexer.index_project(tmp_path)
    selector = RelevantFileSelector(index)

    prompt = "Please update pkg/target.py to implement logging."
    candidates, _ = selector.select_relevant_files(prompt)

    assert len(candidates) > 0
    assert candidates[0].rel_path == "pkg/target.py"
    assert candidates[0].score >= 100.0


def test_relevant_file_selector_non_existent_file_convention_discovery(tmp_path):
    """Verify that if target file does not yet exist, selector discovers package siblings, __init__.py, and test files."""
    (tmp_path / "fusion_agent" / "utils").mkdir(parents=True)
    (tmp_path / "fusion_agent" / "utils" / "__init__.py").write_text('"""Utils package."""\n', encoding="utf-8")
    (tmp_path / "fusion_agent" / "utils" / "string_utils.py").write_text("def slugify(s): return s.lower()\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_math_utils.py").write_text("def test_math(): pass\n", encoding="utf-8")

    indexer = RepositoryIndexer()
    index = indexer.index_project(tmp_path)
    selector = RelevantFileSelector(index)

    # Prompt asks to create a file that does NOT yet exist: fusion_agent/utils/math_utils.py
    prompt = "Create fusion_agent/utils/math_utils.py with clamp function and make tests/test_math_utils.py pass"
    candidates, _ = selector.select_relevant_files(prompt)

    candidate_paths = {c.rel_path for c in candidates}
    # Should discover sibling files in the intended directory to learn conventions
    assert "fusion_agent/utils/__init__.py" in candidate_paths
    assert "fusion_agent/utils/string_utils.py" in candidate_paths
    # Should discover corresponding test file
    assert "tests/test_math_utils.py" in candidate_paths


def test_relevant_file_selector_symbol_matching(tmp_path):
    """Verify that identifier tokens match symbol index and pull in defining files."""
    (tmp_path / "auth.py").write_text("class Authenticator:\n    def verify_token(self):\n        pass\n", encoding="utf-8")

    indexer = RepositoryIndexer()
    index = indexer.index_project(tmp_path)
    selector = RelevantFileSelector(index)

    prompt = "Refactor verify_token to support JWT expiration"
    candidates, symbols = selector.select_relevant_files(prompt)

    assert any(c.rel_path == "auth.py" for c in candidates)
    assert any(s.name == "Authenticator.verify_token" for s in symbols)
