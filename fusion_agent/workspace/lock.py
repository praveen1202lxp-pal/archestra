"""Cross-platform Task Execution Lock for atomic concurrency control."""

import json
import os
import platform
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


class TaskLockError(RuntimeError):
    """Raised when task execution lock cannot be acquired or concurrency conflict occurs."""
    pass


class TaskExecutionLock:
    """Provides atomic mutual exclusion for task execution across processes.
    
    Uses OS kernel file locking so locks are automatically released if a process crashes.
    Also records diagnostic metadata in SQLite for observability.
    """

    def __init__(
        self,
        task_id: str,
        locks_dir: Optional[Path] = None,
        state_manager: Optional[Any] = None,
        owner_id: Optional[str] = None,
    ):
        self.task_id = task_id
        self.owner_id = owner_id or str(uuid.uuid4())[:8]
        if locks_dir:
            p = Path(locks_dir).resolve()
            if p.name == "locks":
                self.locks_dir = p
            elif p.name == ".fusion":
                self.locks_dir = p / "locks"
            else:
                self.locks_dir = p / ".fusion" / "locks"
        else:
            self.locks_dir = (Path(".fusion") / "locks").resolve()
        self.locks_dir.mkdir(parents=True, exist_ok=True)
        self.lock_file_path = self.locks_dir / f"task-{task_id}.lock"
        self.state_manager = state_manager
        self._file_handle = None
        self._is_acquired = False

    @staticmethod
    def _is_pid_running(pid: int) -> bool:
        """Check if a process with given PID is actively running on the system."""
        if pid <= 0:
            return False
        if sys.platform == "win32":
            import ctypes
            kernel32 = ctypes.windll.kernel32
            # PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        else:
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False

    @classmethod
    def is_locked(cls, task_id: str, locks_dir: Optional[Path] = None) -> bool:
        """Check if a task is currently locked by an active process holding an OS kernel file lock."""
        path = (locks_dir or (Path(".fusion") / "locks")).resolve() / f"task-{task_id}.lock"
        if not path.exists():
            return False
        try:
            with open(path, "a+b") as fh:
                fh.seek(0)
                if sys.platform == "win32":
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            return False
        except (OSError, IOError):
            return True

    @classmethod
    def get_lock_info(cls, task_id: str, locks_dir: Optional[Path] = None) -> Optional[Dict[str, Any]]:
        """Read diagnostic ownership metadata from lock file if present."""
        path = (locks_dir or (Path(".fusion") / "locks")).resolve() / f"task-{task_id}.lock"
        if not path.exists():
            return None
        try:
            content = path.read_text(encoding="utf-8", errors="replace").strip()
            return json.loads(content) if content else None
        except Exception:
            return None

    def acquire(self, timeout_seconds: float = 0.0) -> bool:
        """Attempt to acquire exclusive lock on the task.
        
        Raises TaskLockError if another live process holds the lock.
        """
        if self._is_acquired:
            return True

        # Open file in read/write/create mode
        try:
            self._file_handle = open(self.lock_file_path, "a+b")
        except OSError as e:
            raise TaskLockError(f"Failed to open lock file {self.lock_file_path}: {e}")

        # Try to acquire non-blocking OS lock
        try:
            self._file_handle.seek(0)
            if sys.platform == "win32":
                # Lock byte 0 with LK_NBLCK (non-blocking)
                msvcrt.locking(self._file_handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(self._file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError) as lock_exc:
            # Lock is held by another process! Read diagnostic info from lock file
            self._file_handle.seek(0)
            content = self._file_handle.read().decode("utf-8", errors="replace").strip()
            self._file_handle.close()
            self._file_handle = None

            owner_pid = "unknown"
            acquired_at = "unknown"
            if content:
                try:
                    data = json.loads(content)
                    owner_pid = data.get("pid", "unknown")
                    acquired_at = data.get("acquired_at", "unknown")
                except Exception:
                    pass

            raise TaskLockError(
                f"Task '{self.task_id}' is currently locked by another active Fusion process "
                f"(PID: {owner_pid}, acquired: {acquired_at}). Concurrent execution refused."
            ) from lock_exc

        # We acquired the OS lock! Write our ownership payload
        self._is_acquired = True
        payload = {
            "task_id": self.task_id,
            "owner_id": self.owner_id,
            "pid": os.getpid(),
            "hostname": platform.node(),
            "acquired_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            self._file_handle.seek(0)
            self._file_handle.truncate(0)
            self._file_handle.write(json.dumps(payload, indent=2).encode("utf-8"))
            self._file_handle.flush()
        except Exception:
            pass

        # Also register in SQLite if state_manager provided
        if self.state_manager and hasattr(self.state_manager, "record_task_lock"):
            try:
                self.state_manager.record_task_lock(
                    task_id=self.task_id,
                    owner_id=self.owner_id,
                    pid=os.getpid(),
                    hostname=platform.node(),
                )
            except Exception:
                pass

        return True

    def release(self) -> None:
        """Release the lock."""
        if not self._is_acquired:
            return

        # Remove from SQLite
        if self.state_manager and hasattr(self.state_manager, "release_task_lock"):
            try:
                self.state_manager.release_task_lock(self.task_id, self.owner_id)
            except Exception:
                pass

        if self._file_handle:
            try:
                self._file_handle.seek(0)
                if sys.platform == "win32":
                    msvcrt.locking(self._file_handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(self._file_handle.fileno(), fcntl.LOCK_UN)
                self._file_handle.close()
            except Exception:
                pass
            self._file_handle = None

        self._is_acquired = False

    def __enter__(self) -> "TaskExecutionLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()
