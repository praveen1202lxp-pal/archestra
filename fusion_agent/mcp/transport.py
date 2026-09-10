"""MCP Transport layer with isolated Stdio implementation.

NOTE: An MCP server process running locally is a third-party executable.
Process isolation implemented here provides hygiene, timeouts, and sanitized
environment boundaries, but is NOT an OS-level security sandbox.
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Set

from fusion_agent.mcp.models import MCPServerConfig, ProcessTrustLevel

# Safe system environment variables allowlisted for child process execution
SAFE_ENV_ALLOWLIST = {
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "HOME",
    "LANG",
    "LC_ALL",
    "PYTHONPATH",
    "VIRTUAL_ENV",
}


class MCPTransportError(Exception):
    """Base error for MCP transport operations."""
    pass


class MCPUntrustedProcessError(MCPTransportError):
    """Raised when attempting to spawn an unapproved/untrusted MCP server executable."""
    pass


class MCPTimeoutError(MCPTransportError):
    """Raised when an MCP operation exceeds its configured timeout."""
    pass


class MCPProtocolError(MCPTransportError):
    """Raised when an MCP message violates protocol framing."""
    pass


class MCPTransport(ABC):
    """Abstract base class for MCP communication channels."""

    @abstractmethod
    def start(self) -> None:
        """Start the transport channel or process."""
        pass

    @abstractmethod
    def send_message(self, message: Dict[str, Any]) -> None:
        """Send a JSON-RPC message over the transport."""
        pass

    @abstractmethod
    def read_message(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Read the next JSON-RPC message from the transport."""
        pass

    @abstractmethod
    def send_receive(self, message: Dict[str, Any], timeout: Optional[float] = None) -> Dict[str, Any]:
        """Send a JSON-RPC message and await the corresponding response."""
        pass

    @abstractmethod
    def send_notification(self, message: Dict[str, Any]) -> None:
        """Send a one-way notification without expecting a response."""
        pass

    @abstractmethod
    def close(self) -> None:
        """Terminate the transport and clean up resources."""
        pass

    @abstractmethod
    def is_alive(self) -> bool:
        """Check whether the transport is active and responsive."""
        pass


class StdioMCPTransport(MCPTransport):
    """Stdio-based transport executing local MCP servers via subprocess pipes.
    
    Adheres to newline-delimited JSON-RPC 2.0 serialization over stdin and stdout.
    Stdout is strictly reserved for protocol messages; stderr is captured separately
    as bounded diagnostics and never parsed as JSON-RPC data.
    """

    def __init__(self, config: MCPServerConfig):
        self.config = config
        self.process: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._is_closed = False
        self._stderr_buffer: str = ""
        self._stderr_thread: Optional[threading.Thread] = None
        self.ephemeral_secrets: Set[str] = set()

    def _build_sanitized_env(self) -> Dict[str, str]:
        """Construct a sanitized environment containing allowlisted system variables and resolved secret references.
        
        CRITICAL: Credential values are resolved dynamically from host os.environ into the child environment.
        Raw credentials are never stored in MCPServerConfig or serialized to disk.
        Resolved credential values are kept in-memory in self.ephemeral_secrets for redacting from results.
        """
        sanitized = {}
        for k in SAFE_ENV_ALLOWLIST:
            if k in os.environ:
                sanitized[k] = os.environ[k]

        # 1. Resolve host env vars explicitly passed by reference name
        if self.config.env_vars:
            for var_name in self.config.env_vars:
                if var_name in os.environ:
                    val = os.environ[var_name]
                    sanitized[var_name] = val
                    if val and len(val.strip()) >= 3:
                        self.ephemeral_secrets.add(val.strip())

        # 2. Resolve mapped env vars (child_var_name -> host_var_name)
        if self.config.env_mapping:
            for child_var, host_var in self.config.env_mapping.items():
                if host_var in os.environ:
                    val = os.environ[host_var]
                    sanitized[child_var] = val
                    if val and len(val.strip()) >= 3:
                        self.ephemeral_secrets.add(val.strip())

        return sanitized

    def _read_stderr(self) -> None:
        """Background worker accumulating stderr into a bounded diagnostic buffer."""
        try:
            if self.process and self.process.stderr:
                for line in self.process.stderr:
                    if len(self._stderr_buffer) < 100_000:
                        self._stderr_buffer += line
        except Exception:
            pass

    def start(self) -> None:
        """Spawn the child process with piped stdin/stdout/stderr after verifying process trust.
        
        CRITICAL SECURITY INVARIANT:
        An MCP server executable runs with host-level user permissions unless an OS-level
        sandbox exists. Fusion V1 DOES NOT provide an OS kernel sandbox.
        Therefore:
        1. Never launch an UNTRUSTED executable.
        2. cwd confinement is NOT an OS security sandbox.
        """
        if self.process is not None and self.is_alive():
            return

        if self.config.process_trust != ProcessTrustLevel.USER_EXPLICITLY_TRUSTED:
            raise MCPUntrustedProcessError(
                f"MCP server '{self.config.server_id}' executable '{self.config.command}' is UNTRUSTED. "
                f"Fusion will not launch unapproved local executables. Explicit user trust is required."
            )

        if not self.config.command:
            raise MCPTransportError(f"Server '{self.config.server_id}' has no executable command configured.")

        cmd = [self.config.command] + (self.config.args or [])
        env = self._build_sanitized_env()

        # Safer cwd resolution (cwd isolation is hygiene only, not an OS sandbox)
        if self.config.cwd:
            cwd = self.config.cwd
        elif self.config.allow_repo_cwd:
            cwd = os.getcwd()
        else:
            # Default to dedicated Fusion-controlled runtime directory outside repo root
            cwd = os.path.join(tempfile.gettempdir(), "fusion_mcp_runtime", self.config.server_id)
            os.makedirs(cwd, exist_ok=True)

        try:
            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                env=env,
                cwd=cwd,
                bufsize=1,  # Line-buffered
            )
            self._is_closed = False
            self._stderr_buffer = ""
            self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
            self._stderr_thread.start()
        except Exception as exc:
            raise MCPTransportError(
                f"Failed to start MCP server '{self.config.server_id}' with command {cmd}: {exc}"
            ) from exc

    def is_alive(self) -> bool:
        """Check if child process is running."""
        if self.process is None:
            return False
        return self.process.poll() is None

    def send_message(self, message: Dict[str, Any]) -> None:
        """Serialize and write a JSON-RPC message to child stdin."""
        if not self.is_alive():
            self.start()

        if self.process is None or self.process.stdin is None:
            raise MCPTransportError(f"Server '{self.config.server_id}' process stdin unavailable.")

        payload = json.dumps(message) + "\n"
        try:
            self.process.stdin.write(payload)
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self.close()
            raise MCPTransportError(f"Failed to write message to '{self.config.server_id}': {exc}") from exc

    def send_notification(self, message: Dict[str, Any]) -> None:
        """Write a notification message to stdin."""
        with self._lock:
            self.send_message(message)

    def read_message(self, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Read a single newline-delimited JSON-RPC object from stdout with timeout and size bounds."""
        if not self.is_alive():
            self.start()

        if self.process is None or self.process.stdout is None:
            raise MCPTransportError(f"Server '{self.config.server_id}' pipes unavailable.")

        op_timeout = timeout or self.config.timeout_seconds
        line_data: Optional[str] = None
        read_error: Optional[Exception] = None

        def _reader():
            nonlocal line_data, read_error
            try:
                line = self.process.stdout.readline()
                if line:
                    line_data = line
            except Exception as ex:
                read_error = ex

        t = threading.Thread(target=_reader, daemon=True)
        t.start()
        t.join(timeout=op_timeout)

        if t.is_alive():
            self.close()
            raise MCPTimeoutError(
                f"MCP server '{self.config.server_id}' timed out after {op_timeout:.1f}s waiting for stdout response."
            )

        if read_error:
            raise MCPTransportError(f"Error reading from '{self.config.server_id}': {read_error}") from read_error

        if not line_data:
            exit_code = self.process.poll() if self.process else -1
            diag = self._stderr_buffer[-500:] if self._stderr_buffer else "No stderr."
            self.close()
            raise MCPTransportError(
                f"MCP server '{self.config.server_id}' terminated unexpectedly (exit code {exit_code}). "
                f"Diagnostics: {diag}"
            )

        # Enforce per-message byte ceiling (1 MB max)
        msg_bytes = len(line_data.encode("utf-8"))
        if msg_bytes > min(self.config.max_output_bytes, 1_000_000):
            self.close()
            raise MCPProtocolError(
                f"MCP message from '{self.config.server_id}' exceeded per-message byte ceiling "
                f"({msg_bytes} > {min(self.config.max_output_bytes, 1_000_000)} bytes)."
            )

        try:
            return json.loads(line_data.strip())
        except json.JSONDecodeError as exc:
            raise MCPProtocolError(
                f"Malformed JSON-RPC message from '{self.config.server_id}': {line_data[:200]}"
            ) from exc

    def send_receive(self, message: Dict[str, Any], timeout: Optional[float] = None) -> Dict[str, Any]:
        """Send message and read responses until a message with matching request ID arrives.
        
        CRITICAL: Ignores unsolicited notifications and rejects server requests.
        """
        with self._lock:
            self.send_message(message)
            req_id = message.get("id")
            op_timeout = timeout or self.config.timeout_seconds
            deadline = time.time() + op_timeout

            while True:
                remaining = max(0.1, deadline - time.time())
                resp = self.read_message(timeout=remaining)

                # 1. Handle server-initiated requests (reject with -32601)
                if "method" in resp and "id" in resp:
                    # Server requesting client action (e.g. sampling/createMessage, roots/list)
                    server_req_id = resp["id"]
                    method = resp.get("method", "")
                    # Reject unsupported server-initiated capabilities
                    self.send_message({
                        "jsonrpc": "2.0",
                        "id": server_req_id,
                        "error": {
                            "code": -32601,
                            "message": f"Server-initiated capability '{method}' is unsupported by Fusion client.",
                        },
                    })
                    continue

                # 2. Handle unsolicited notifications (discard without error)
                if "method" in resp and "id" not in resp:
                    continue

                # 3. Check response ID matching
                if req_id is not None:
                    if resp.get("id") == req_id:
                        return resp
                    # Mismatched ID from an out-of-order or stale message: discard and continue waiting
                    continue

                return resp

    def close(self) -> None:
        """Gracefully terminate or kill child process and cleanup pipes."""
        self._is_closed = True
        if self.process is not None:
            try:
                if self.process.stdin:
                    try:
                        self.process.stdin.close()
                    except Exception:
                        pass
                if self.is_alive():
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=2.0)
            except Exception:
                pass
            finally:
                self.process = None
