"""Tests for secret-aware repository filtering, ignore rules, and binary file exclusion."""

import tempfile
from pathlib import Path

from fusion_agent.repository.ignore import IgnoreManager, SecretFilter


def test_secret_filter_sensitive_filenames():
    """Verify that common secret and credential files are flagged as sensitive."""
    sensitive_paths = [
        ".env",
        ".env.local",
        ".env.production",
        "subfolder/.env.dev",
        "id_rsa",
        "id_ed25519",
        "keys/id_ecdsa",
        "credentials.json",
        "client_secret.json",
        "service_account.json",
        ".npmrc",
        ".pypirc",
        ".netrc",
        "auth.json",
        "token.txt",
    ]
    for p in sensitive_paths:
        is_secret, reason = SecretFilter.is_secret_or_sensitive(p)
        assert is_secret, f"Expected {p} to be identified as secret (reason: {reason})"


def test_secret_filter_sensitive_extensions():
    """Verify that cryptographic key and certificate extensions are flagged."""
    key_files = [
        "cert.pem",
        "server.key",
        "keystore.p12",
        "certificate.pfx",
        "bundle.crt",
        "private.asc",
        "db.kdbx",
    ]
    for k in key_files:
        is_secret, reason = SecretFilter.is_secret_or_sensitive(k)
        assert is_secret, f"Expected {k} to be identified as secret (reason: {reason})"


def test_secret_filter_internal_directories():
    """Verify that .git and .fusion internal directories are flagged."""
    internal_paths = [
        ".git/config",
        ".git/HEAD",
        ".fusion/fusion.db",
        ".fusion/worktrees/task-1/file.py",
    ]
    for ip in internal_paths:
        is_secret, reason = SecretFilter.is_secret_or_sensitive(ip)
        assert is_secret, f"Expected {ip} to be identified as internal/secret"


def test_secret_filter_normal_code_files():
    """Verify that standard source and documentation files are not flagged."""
    normal_files = [
        "fusion_agent/core/orchestrator.py",
        "README.md",
        "pyproject.toml",
        "src/utils/math_utils.py",
        "tests/test_calculator.py",
        "frontend/src/App.tsx",
    ]
    for nf in normal_files:
        is_secret, reason = SecretFilter.is_secret_or_sensitive(nf)
        assert not is_secret, f"Did not expect {nf} to be flagged as secret ({reason})"


def test_binary_file_detection(tmp_path):
    """Verify that binary files (images, archives, compiled binaries) are detected and excluded."""
    # Test extension detection
    assert SecretFilter.is_binary_file("assets/logo.png")
    assert SecretFilter.is_binary_file("dist/app.exe")
    assert SecretFilter.is_binary_file("archive.zip")
    assert SecretFilter.is_binary_file("lib.so")
    assert not SecretFilter.is_binary_file("main.py")
    assert not SecretFilter.is_binary_file("README.md")

    # Test content null byte detection
    bin_file = tmp_path / "data.dat"
    bin_file.write_bytes(b"hello\x00world")
    assert SecretFilter.is_binary_file(str(bin_file), content=b"hello\x00world")


def test_ignore_manager_gitignore_and_fusionignore(tmp_path):
    """Verify IgnoreManager respects both .gitignore and .fusionignore rules."""
    # Create .gitignore
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("*.log\nbuild/\nlocal_cache/", encoding="utf-8")

    # Create .fusionignore
    fusionignore = tmp_path / ".fusionignore"
    fusionignore.write_text("secret_docs/\n*.internal", encoding="utf-8")

    mgr = IgnoreManager(tmp_path)

    # Gitignore patterns
    assert mgr.should_ignore("app.log")
    assert mgr.should_ignore("logs/debug.log")
    assert mgr.should_ignore("build/output.js")
    assert mgr.should_ignore("local_cache/item.json")

    # Fusionignore patterns
    assert mgr.should_ignore("secret_docs/notes.md")
    assert mgr.should_ignore("config.internal")

    # Default secret filter integration
    assert mgr.should_ignore(".env")
    assert mgr.should_ignore("sub/.env.prod")
    assert mgr.should_ignore("server.key")

    # Non-ignored file
    assert not mgr.should_ignore("fusion_agent/core/orchestrator.py")
    assert not mgr.should_ignore("tests/test_editor.py")


def test_ignore_manager_explorer_filtering(tmp_path):
    """Verify should_ignore_explorer hides Fusion internals, tool dirs, and secrets while preserving user code."""
    mgr = IgnoreManager(tmp_path)

    # Fusion runtime internals must be ignored
    assert mgr.should_ignore_explorer(".fusion")
    assert mgr.should_ignore_explorer(".fusion/locks")
    assert mgr.should_ignore_explorer(".fusion/worktrees")
    assert mgr.should_ignore_explorer(".fusion/fusion.db")
    assert mgr.should_ignore_explorer(".fusion/fusion.db-wal")
    assert mgr.should_ignore_explorer(".fusion/fusion.db-shm")
    assert mgr.should_ignore_explorer(".fusion/config.json")
    assert mgr.should_ignore_explorer("task-99.lock")
    assert mgr.should_ignore_explorer(".fusion/locks/task-99.lock")

    # Tool and cache directories must be ignored
    assert mgr.should_ignore_explorer(".git")
    assert mgr.should_ignore_explorer(".git/config")
    assert mgr.should_ignore_explorer("node_modules")
    assert mgr.should_ignore_explorer("node_modules/express/index.js")
    assert mgr.should_ignore_explorer(".angular")
    assert mgr.should_ignore_explorer(".angular/cache")
    assert mgr.should_ignore_explorer("__pycache__")
    assert mgr.should_ignore_explorer("src/__pycache__/mod.cpython-312.pyc")
    assert mgr.should_ignore_explorer(".cache")
    assert mgr.should_ignore_explorer(".temp")
    assert mgr.should_ignore_explorer(".tmp")
    assert mgr.should_ignore_explorer(".pytest_cache")
    assert mgr.should_ignore_explorer("data.sqlite3")
    assert mgr.should_ignore_explorer("app.db")

    # Sensitive files must be ignored
    assert mgr.should_ignore_explorer(".env")
    assert mgr.should_ignore_explorer("keys/server.key")

    # Legitimate user source directories sharing generic names MUST NOT be ignored
    assert not mgr.should_ignore_explorer(".fusion-notes")
    assert not mgr.should_ignore_explorer(".fusion-notes/notes.md")
    assert not mgr.should_ignore_explorer("src/locks")
    assert not mgr.should_ignore_explorer("src/locks/mutex.py")
    assert not mgr.should_ignore_explorer("src/cache")
    assert not mgr.should_ignore_explorer("src/cache/lru.py")
    assert not mgr.should_ignore_explorer("src/temp")
    assert not mgr.should_ignore_explorer("src/temp/helper.py")
    assert not mgr.should_ignore_explorer("src/app.py")
    assert not mgr.should_ignore_explorer("README.md")
    assert not mgr.should_ignore_explorer("package.json")


def test_repository_indexer_pre_m14_ignore_behavior(tmp_path):
    """Verify that general repository indexing outside Studio is unchanged from the pre-M14 policy."""
    from fusion_agent.repository.indexer import RepositoryIndexer

    # DEFAULT_IGNORE_DIRS must match the exact 13 pre-M14 entries
    expected_pre_m14_dirs = {
        ".git",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".pytest_cache",
        ".fusion",
        "node_modules",
        "dist",
        "build",
        ".mypy_cache",
        ".ruff_cache",
        ".angular",
    }
    assert IgnoreManager.DEFAULT_IGNORE_DIRS == expected_pre_m14_dirs

    mgr = IgnoreManager(tmp_path)
    # Generic cache/temp dirs are NOT in DEFAULT_IGNORE_DIRS and not ignored unless specified in .gitignore
    assert not mgr.should_ignore(".cache/item.json")
    assert not mgr.should_ignore(".temp/test.txt")
    assert not mgr.should_ignore(".tmp/scratch.txt")
    assert not mgr.should_ignore(".fusion-notes/notes.md")

    # While Studio explorer DOES ignore obvious runtime cache/temp dirs:
    assert mgr.should_ignore_explorer(".cache/item.json")
    assert mgr.should_ignore_explorer(".temp/test.txt")
    assert mgr.should_ignore_explorer(".tmp/scratch.txt")
    assert not mgr.should_ignore_explorer(".fusion-notes/notes.md")

    # Built-in indexing ignores .fusion and .git
    assert mgr.should_ignore(".fusion/fusion.db")
    assert mgr.should_ignore(".git/config")

    # Test with RepositoryIndexer directly
    fusion_dir = tmp_path / ".fusion"
    fusion_dir.mkdir(parents=True, exist_ok=True)
    (fusion_dir / "fusion.db").write_bytes(b"dummy db")

    fusion_notes_dir = tmp_path / ".fusion-notes"
    fusion_notes_dir.mkdir(parents=True, exist_ok=True)
    (fusion_notes_dir / "notes.md").write_text("# Notes", encoding="utf-8")

    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "app.py").write_text("print('hello')", encoding="utf-8")

    cache_dir = tmp_path / ".cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "data.txt").write_text("cached content", encoding="utf-8")

    indexer = RepositoryIndexer()
    proj_index = indexer.index_project(tmp_path)

    # Indexer discovers .fusion-notes/notes.md, src/app.py, and .cache/data.txt, but not .fusion/fusion.db
    assert ".fusion-notes/notes.md" in proj_index.file_tree
    assert "src/app.py" in proj_index.file_tree
    assert ".cache/data.txt" in proj_index.file_tree
    assert ".fusion/fusion.db" not in proj_index.file_tree
    assert not any(p.startswith(".fusion/") or p == ".fusion" for p in proj_index.file_tree)

    # And Studio explorer hides .cache/data.txt while keeping .fusion-notes/notes.md and src/app.py visible
    assert mgr.should_ignore_explorer(".cache/data.txt")
    assert not mgr.should_ignore_explorer(".fusion-notes/notes.md")
    assert not mgr.should_ignore_explorer("src/app.py")
