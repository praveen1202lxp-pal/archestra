"""Execution Broker enforcing path containment and process sandboxing."""

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Union

from fusion_agent.workspace.session import WorkspaceSession, WorkspaceState


class ExecutionBroker:
    """Security boundary and tool execution gateway jailed strictly to a WorkspaceSession."""

    # Windows reserved DOS device names (CON, PRN, AUX, NUL, COM1-9, LPT1-9)
    DOS_DEVICE_NAMES = {
        "CON", "PRN", "AUX", "NUL",
        "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
        "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
    }

    # Strict allowlist of environment variables permitted in sandboxed subprocesses
    ENV_ALLOWLIST: Set[str] = {
        # Core Windows OS paths and executables
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        # User home directories required by compilers / git / python
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "LOCALAPPDATA",
        "APPDATA",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "COMMONPROGRAMFILES",
        # Architecture / runtime
        "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE",
        "PROCESSOR_IDENTIFIER",
        "OS",
        # Python / encoding
        "LANG",
        "LC_ALL",
        "PYTHONIOENCODING",
        "PYTHONUTF8",
        "PYTHONPATH",
        "VIRTUAL_ENV",
    }

    def __init__(self, session: WorkspaceSession):
        self.session = session

    def resolve_path(self, path: Union[str, Path]) -> Path:
        """Resolve and canonicalize path, enforcing strict worktree containment on Windows and POSIX."""
        raw_str = str(path).strip()
        if not raw_str:
            return Path(os.path.realpath(str(self.session.worktree_dir)))

        # 1. Prohibit Windows device namespaces (e.g. \\.\ or \\?\)
        if re.match(r"^[\\/]{2}[\.\?][\\/]", raw_str):
            raise PermissionError(f"Access denied: Windows device namespace paths are prohibited: {path}")

        # 2. Prohibit UNC paths (e.g. \\server\share or //server/share)
        if raw_str.startswith(("\\\\", "//")):
            raise PermissionError(f"Access denied: UNC paths are prohibited: {path}")

        raw_path = Path(path)

        # 3. Prohibit Windows DOS reserved device names anywhere in path components
        for part in raw_path.parts:
            stem = part.split(".")[0].upper()
            if stem in self.DOS_DEVICE_NAMES:
                raise PermissionError(f"Access denied: Windows reserved device name '{part}' is prohibited.")

        # 4. Prohibit .git repository metadata / linked-worktree references
        for part in raw_path.parts:
            if part.lower() == ".git":
                raise PermissionError("Access denied: Access to .git repository metadata or linked worktree files is prohibited.")

        worktree_real = Path(os.path.realpath(str(self.session.worktree_dir)))

        # 5. Drive letter checks on Windows
        if raw_path.is_absolute():
            # Ensure drive letters match if specified
            if raw_path.drive and worktree_real.drive:
                if raw_path.drive.upper() != worktree_real.drive.upper():
                    raise PermissionError(
                        f"Access denied: Cross-drive path '{path}' escapes worktree drive '{worktree_real.drive}'."
                    )
            candidate = raw_path
        else:
            candidate = self.session.worktree_dir / raw_path

        # 6. Canonicalize symlinks, junctions, reparse points, and relative segments
        try:
            resolved_real = Path(os.path.realpath(str(candidate)))
        except Exception as exc:
            raise PermissionError(f"Access denied: Unable to resolve realpath for '{path}': {exc}")

        # 7. Case-insensitive commonpath containment check
        worktree_str = str(worktree_real)
        resolved_str = str(resolved_real)

        # Compare canonical lowercase paths
        try:
            common = os.path.commonpath([worktree_str.lower(), resolved_str.lower()])
            if common != worktree_str.lower():
                raise PermissionError(
                    f"Access denied: Path '{path}' resolves to '{resolved_real}', which escapes the isolated worktree '{worktree_real}'."
                )
        except ValueError:
            # Different drives on Windows will raise ValueError in commonpath
            raise PermissionError(
                f"Access denied: Path '{path}' resolves to '{resolved_real}', which is on a different root/drive from '{worktree_real}'."
            )

        # 8. Final check: Ensure target does not point to or inside .git
        rel = resolved_real.relative_to(worktree_real)
        for part in rel.parts:
            if part.lower() == ".git":
                raise PermissionError("Access denied: Direct modification of .git repository metadata is prohibited.")

        return resolved_real

    def file_exists(self, path: Union[str, Path]) -> bool:
        """Check if file exists within contained worktree."""
        try:
            target = self.resolve_path(path)
            return target.is_file()
        except Exception:
            return False

    def read_file(self, path: Union[str, Path]) -> str:
        """Read content from a contained file."""
        target = self.resolve_path(path)
        if not target.is_file():
            raise FileNotFoundError(f"File not found in worktree: {path}")
        return target.read_text(encoding="utf-8", errors="replace")

    def write_file(self, path: Union[str, Path], content: str) -> None:
        """Write content to a contained file, creating parent directories if needed."""
        target = self.resolve_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        if self.session.state == WorkspaceState.PREPARED:
            self.session.state = WorkspaceState.ACTIVE

    def edit_file(
        self,
        path: Union[str, Path],
        target_snippet: str,
        replacement_snippet: str,
    ) -> None:
        """Replace target snippet with replacement snippet in a contained file."""
        target = self.resolve_path(path)
        if not target.is_file():
            raise FileNotFoundError(f"File not found in worktree: {path}")

        content = target.read_text(encoding="utf-8", errors="replace")
        if target_snippet not in content:
            raise ValueError(f"Target snippet not found in {path}")

        updated = content.replace(target_snippet, replacement_snippet, 1)
        target.write_text(updated, encoding="utf-8")
        if self.session.state == WorkspaceState.PREPARED:
            self.session.state = WorkspaceState.ACTIVE

    def list_dir(self, path: Union[str, Path] = "") -> List[str]:
        """List directory contents relative to the worktree."""
        target = self.resolve_path(path)
        if not target.is_dir():
            raise NotADirectoryError(f"Directory not found in worktree: {path}")

        worktree_root = Path(os.path.realpath(str(self.session.worktree_dir)))
        entries = []
        for item in target.iterdir():
            item_real = Path(os.path.realpath(str(item)))
            rel = item_real.relative_to(worktree_root)
            entries.append(str(rel).replace("\\", "/"))
        return sorted(entries)

    def file_exists(self, path: Union[str, Path]) -> bool:
        """Check if a file or directory exists within the worktree."""
        try:
            target = self.resolve_path(path)
            return target.exists()
        except PermissionError:
            return False

    def build_sanitized_env(self, extra_env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """Construct an allowlist-based minimal environment for sandboxed subprocesses."""
        env: Dict[str, str] = {}
        for key in self.ENV_ALLOWLIST:
            val = os.environ.get(key)
            if val is not None:
                env[key] = val

        # Merge caller-provided extra_env if not containing sensitive tokens
        if extra_env:
            for k, v in extra_env.items():
                k_upper = k.upper()
                if any(term in k_upper for term in ("KEY", "TOKEN", "SECRET", "PASS", "CRED", "AUTH")):
                    raise PermissionError(f"Security error: Environment variable '{k}' rejected as potentially sensitive.")
                env[k] = v

        return env

    def run_command(
        self,
        cmd: Union[str, List[str]],
        timeout: float = 60.0,
        extra_env: Optional[Dict[str, str]] = None,
    ) -> Tuple[int, str, str]:
        """Execute a command strictly jailed to the worktree with an allowlist-based environment."""
        env = self.build_sanitized_env(extra_env)

        is_shell = isinstance(cmd, str)
        proc = subprocess.run(
            cmd,
            cwd=str(self.session.worktree_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=is_shell,
            env=env,
            encoding="utf-8",
            errors="replace",
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
