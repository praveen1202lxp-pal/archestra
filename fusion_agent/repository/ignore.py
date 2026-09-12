"""Secret-aware filtering and ignore management for Milestone 7."""

import fnmatch
import os
import re
from pathlib import Path
from typing import List, Optional, Set, Tuple, Union


class SecretFilter:
    """Detects credentials, keys, tokens, and sensitive files to prevent leakage to model providers."""

    SENSITIVE_FILENAMES = {
        ".env",
        ".env.local",
        ".env.production",
        ".env.development",
        ".env.test",
        ".env.staging",
        "credentials.json",
        "token.json",
        "tokens.json",
        "token.txt",
        "auth.json",
        "id_rsa",
        "id_ed25519",
        "id_ecdsa",
        "id_dsa",
        "known_hosts",
        "authorized_keys",
        "client_secret.json",
        ".npmrc",
        ".pypirc",
        ".netrc",
    }

    SENSITIVE_EXTENSIONS = {
        ".pem",
        ".key",
        ".p12",
        ".pfx",
        ".keystore",
        ".jks",
        ".pkcs12",
        ".der",
        ".crt",
        ".asc",
        ".kdbx",
    }

    INTERNAL_DIRS = {
        ".git",
        ".fusion",
    }

    SENSITIVE_REGEXES = [
        re.compile(r"^\.env(\..+)?$", re.IGNORECASE),
        re.compile(r".*secret.*", re.IGNORECASE),
        re.compile(r".*credential.*", re.IGNORECASE),
        re.compile(r".*service_account.*\.json$", re.IGNORECASE),
        re.compile(r".*private.*key.*", re.IGNORECASE),
    ]

    BINARY_EXTENSIONS = {
        ".pyc", ".pyo", ".pyd", ".db", ".sqlite", ".sqlite3",
        ".log", ".png", ".jpg", ".jpeg", ".ico", ".svg", ".gif",
        ".bin", ".exe", ".dll", ".so", ".dylib", ".tar", ".gz",
        ".zip", ".7z", ".lock", ".wasm", ".pdf",
    }

    @classmethod
    def is_secret_or_sensitive(cls, file_path: Union[str, Path]) -> Tuple[bool, Optional[str]]:
        """Return True and a description if the file appears to contain credentials or secrets."""
        norm_str = str(file_path).replace("\\", "/").lstrip("/")
        parts = norm_str.split("/")

        # Check internal directories (.git, .fusion)
        for part in parts:
            if part in cls.INTERNAL_DIRS:
                return True, f"Internal directory component: {part}"

        p = Path(file_path)
        name = p.name
        ext = p.suffix.lower()

        if name in cls.SENSITIVE_FILENAMES:
            return True, f"Explicit sensitive filename: {name}"

        if ext in cls.SENSITIVE_EXTENSIONS:
            return True, f"Private key/certificate extension: {ext}"

        for regex in cls.SENSITIVE_REGEXES:
            if regex.search(name):
                return True, f"Filename matched sensitive pattern: {name}"

        return False, None

    @classmethod
    def is_binary(cls, file_path: Union[str, Path], content: Optional[bytes] = None) -> bool:
        """Check if file is binary by extension or content inspection."""
        p = Path(file_path)
        if p.suffix.lower() in cls.BINARY_EXTENSIONS:
            return True

        if content is not None:
            return b"\x00" in content

        if not p.is_file():
            return False

        try:
            with open(p, "rb") as f:
                chunk = f.read(1024)
                if b"\x00" in chunk:
                    return True
        except Exception:
            return True
        return False

    is_binary_file = is_binary

    SECRET_VALUE_PATTERNS = [
        re.compile(r"(?i)(bearer\s+)[a-zA-Z0-9_\-\.]{16,}"),
        re.compile(r"(?i)(api[_-]?key|secret|token|password|auth|credential)\s*[:=]\s*['\"]?[a-zA-Z0-9_\-\.]{12,}['\"]?"),
    ]

    @classmethod
    def filter_text(cls, text: str) -> str:
        """Mask potential credential and secret values from logs, prompts, and audit records."""
        if not text:
            return text
        filtered = text
        for pat in cls.SECRET_VALUE_PATTERNS:
            filtered = pat.sub(r"\1: [REDACTED_SECRET]", filtered)
        return filtered


class IgnoreManager:
    """Aggregates built-in ignore rules, .gitignore, and .fusionignore."""

    DEFAULT_IGNORE_DIRS = {
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

    # Dedicated Studio Explorer ignore directories
    EXPLORER_IGNORE_DIRS = {
        ".git",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".angular",
        "node_modules",
        ".cache",
        ".temp",
        ".tmp",
    }

    EXPLORER_RUNTIME_EXTENSIONS = {
        ".db",
        ".db-wal",
        ".db-shm",
        ".sqlite",
        ".sqlite3",
        ".pyc",
        ".pyo",
        ".pyd",
    }

    def __init__(self, repo_root: Union[str, Path]):
        self.root = Path(repo_root).resolve()
        self.custom_patterns: List[str] = []
        self._load_ignore_files()

    def _load_ignore_files(self) -> None:
        """Load patterns from .gitignore and .fusionignore."""
        for filename in [".gitignore", ".fusionignore"]:
            p = self.root / filename
            if p.is_file():
                try:
                    with open(p, "r", encoding="utf-8", errors="replace") as f:
                        for line in f:
                            line = line.strip()
                            if line and not line.startswith("#"):
                                self.custom_patterns.append(line.replace("\\", "/"))
                except Exception:
                    pass

    def should_ignore(self, rel_path: str) -> bool:
        """Check whether a relative path should be excluded from indexing."""
        norm = rel_path.replace("\\", "/").lstrip("/")
        parts = norm.split("/")

        # Check directory names
        for part in parts[:-1]:
            if part in self.DEFAULT_IGNORE_DIRS or part.endswith(".egg-info"):
                return True

        # Check final component if it's a dir
        if parts[-1] in self.DEFAULT_IGNORE_DIRS or parts[-1].endswith(".egg-info"):
            return True

        # Check SecretFilter
        is_secret, _ = SecretFilter.is_secret_or_sensitive(norm)
        if is_secret:
            return True

        # Check Binary
        full_path = self.root / norm
        if full_path.is_file() and SecretFilter.is_binary(full_path):
            return True

        # Check .gitignore / .fusionignore pattern matches
        for pat in self.custom_patterns:
            clean_pat = pat.strip("/")
            if fnmatch.fnmatch(norm, pat) or fnmatch.fnmatch(norm, clean_pat):
                return True
            if any(fnmatch.fnmatch(part, clean_pat) for part in parts):
                return True

        return False

    def should_ignore_explorer(
        self,
        rel_path: Union[str, Path],
        storage_dir: Optional[str] = None,
    ) -> bool:
        """Check whether a relative path should be excluded from Project Explorer and file APIs.

        Studio file explorer hides:
        - exact .fusion/ directory and its contents
        - configured Fusion storage directory and its contents
        - exact .git/ directory and its contents
        - Fusion runtime worktrees/locks
        - runtime SQLite DB / WAL / SHM files (.db, .db-wal, .db-shm, .sqlite, .sqlite3)
        - __pycache__, .pytest_cache, .mypy_cache, .ruff_cache, .angular, node_modules
        - obvious runtime cache/temp directories (.cache, .temp, .tmp)
        - compiled bytecode (.pyc, .pyo, .pyd)
        - secret and sensitive credential files (via SecretFilter)
        - custom patterns from .gitignore / .fusionignore

        Preserves legitimate user directories and files (e.g. .fusion-notes/, src/locks/mutex.py, src/cache/).
        """
        norm = str(rel_path).replace("\\", "/").strip("/")
        if not norm:
            return False

        parts = norm.split("/")
        filename = parts[-1]
        name_lower = filename.lower()

        # 1. Exact match on Fusion runtime directory or configured storage directory
        # Exact matching ensures legitimate directories like '.fusion-notes/' are NOT hidden.
        effective_storage = (storage_dir or ".fusion").replace("\\", "/").strip("/")
        for part in parts:
            if part == ".fusion" or (effective_storage and part == effective_storage):
                return True
            if part == ".git":
                return True
            if part.lower() in self.EXPLORER_IGNORE_DIRS:
                return True

        # 2. Check Fusion runtime worktrees and locks
        # If at root level or under storage directory:
        if len(parts) == 1 and parts[0].lower() in ("worktrees", "locks"):
            return True
        if any(p == ".fusion" or (effective_storage and p == effective_storage) for p in parts[:-1]):
            if name_lower in ("worktrees", "locks"):
                return True

        # 3. Check task locks and lock files associated with Fusion
        if name_lower.endswith(".lock"):
            if name_lower.startswith("task-") or name_lower == ".fusion.lock" or any(p.lower() == "locks" for p in parts):
                return True

        # 4. Check runtime database, WAL, SHM, and bytecode extensions
        for ext in self.EXPLORER_RUNTIME_EXTENSIONS:
            if name_lower.endswith(ext):
                return True

        # 5. Check SecretFilter (credentials, private keys, .env, etc.)
        is_secret, _ = SecretFilter.is_secret_or_sensitive(norm)
        if is_secret:
            return True

        # 7. Check custom ignore patterns from .gitignore / .fusionignore
        for pat in self.custom_patterns:
            clean_pat = pat.strip("/")
            if fnmatch.fnmatch(norm, pat) or fnmatch.fnmatch(norm, clean_pat):
                return True
            if any(fnmatch.fnmatch(part, clean_pat) for part in parts):
                return True
        return False
