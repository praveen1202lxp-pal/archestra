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
