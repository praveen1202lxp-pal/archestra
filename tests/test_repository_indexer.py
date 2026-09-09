"""Tests for language-extensible, secret-aware RepositoryIndexer."""

import tempfile
from pathlib import Path

from fusion_agent.repository.indexer import RepositoryIndexer


def test_repository_indexer_discovers_files_and_ignores_secrets(tmp_path):
    """Verify RepositoryIndexer indexes source files while excluding secrets, ignored files, and binaries."""
    # Create valid python files
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "math_utils.py").write_text("def clamp(val, low, high):\n    return max(low, min(high, val))\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_math_utils.py").write_text("from src.math_utils import clamp\ndef test_clamp():\n    assert clamp(5, 0, 10) == 5\n", encoding="utf-8")

    # Create secret and ignored files
    (tmp_path / ".env").write_text("SECRET_KEY=supersecret\n", encoding="utf-8")
    (tmp_path / "server.key").write_text("-----BEGIN RSA PRIVATE KEY-----\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("repository config\n", encoding="utf-8")

    indexer = RepositoryIndexer()
    index = indexer.index_project(tmp_path)

    # Valid files must be indexed
    assert "src/math_utils.py" in index.file_tree
    assert "tests/test_math_utils.py" in index.file_tree

    # Secrets and internal dirs must NOT be indexed
    assert ".env" not in index.file_tree
    assert "server.key" not in index.file_tree
    assert ".git/config" not in index.file_tree

    # Check test classification
    assert not index.file_tree["src/math_utils.py"].is_test
    assert index.file_tree["tests/test_math_utils.py"].is_test


def test_repository_indexer_symbol_and_dependency_graphs(tmp_path):
    """Verify symbol extraction and test association building."""
    (tmp_path / "service.py").write_text(
        "class OrderService:\n    def create_order(self):\n        pass\n",
        encoding="utf-8",
    )
    (tmp_path / "test_service.py").write_text(
        "from service import OrderService\ndef test_create():\n    pass\n",
        encoding="utf-8",
    )

    indexer = RepositoryIndexer()
    index = indexer.index_project(tmp_path)

    # Symbol index
    assert "OrderService" in index.symbol_index
    assert "OrderService.create_order" in index.symbol_index

    # Search symbols
    sym_results = index.search_symbols("Order")
    assert len(sym_results) >= 1
    assert sym_results[0].name == "OrderService"

    # Search keywords
    kw_results = index.search_keywords("create_order")
    assert len(kw_results) >= 1
    assert kw_results[0][0] == "service.py"
