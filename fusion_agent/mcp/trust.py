"""User-owned Process Trust Store for MCP Server Executables.

CRITICAL SECURITY PRINCIPLE:
A cloned repository must NEVER be able to silently or automatically execute code
via repository-controlled configuration (such as .fusion/mcp_servers.json).
Repository-discovered server definitions are treated strictly as UNTRUSTED suggestions.
Before any local server process is spawned, it must be explicitly authorized by the user.
Trust binds cryptographically to the server's definition fingerprint (command, args, env, cwd).
If the executable or arguments materially change, trust is invalidated immediately.
"""

import json
import os
from pathlib import Path
from typing import Dict, Optional

from fusion_agent.mcp.models import MCPServerConfig, ProcessTrustLevel


def get_default_user_trust_file() -> str:
    """Return the authoritative user-owned trust file location outside repository trees.
    
    Defaults to ~/.fusion/mcp_trust_store.json unless overridden by the
    FUSION_USER_TRUST_STORE environment variable.
    """
    override = os.environ.get("FUSION_USER_TRUST_STORE")
    if override:
        return override
    return str(Path.home() / ".fusion" / "mcp_trust_store.json")


class MCPServerTrustStore:
    """Manages explicit user trust for MCP server host process executables."""

    def __init__(self, trust_file_path: Optional[str] = None, in_memory: bool = False):
        if in_memory:
            self.trust_file_path = None
        else:
            self.trust_file_path = trust_file_path or get_default_user_trust_file()
        self._trusted_fingerprints: Dict[str, str] = {}  # server_id -> fingerprint
        self._load()

    def _load(self) -> None:
        """Load trusted fingerprints from storage file if available."""
        if not self.trust_file_path:
            return
        p = Path(self.trust_file_path)
        if p.is_file():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self._trusted_fingerprints = data
            except Exception:
                self._trusted_fingerprints = {}

    def _save(self) -> None:
        """Persist trusted fingerprints to storage file if configured."""
        if not self.trust_file_path:
            return
        p = Path(self.trust_file_path)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(self._trusted_fingerprints, f, indent=2)
        except Exception:
            pass

    def trust_server(self, config: MCPServerConfig) -> str:
        """Explicitly authorize an MCP server definition.
        
        Computes and records the SHA-256 fingerprint binding server ID, command, args, and env.
        Returns the computed fingerprint.
        """
        fingerprint = config.compute_definition_fingerprint()
        self._trusted_fingerprints[config.server_id] = fingerprint
        config.process_trust = ProcessTrustLevel.USER_EXPLICITLY_TRUSTED
        self._save()
        return fingerprint

    def is_server_trusted(self, config: MCPServerConfig) -> bool:
        """Check if an MCP server's current definition matches an authorized fingerprint."""
        trusted_fp = self._trusted_fingerprints.get(config.server_id)
        if not trusted_fp:
            return False
        current_fp = config.compute_definition_fingerprint()
        return current_fp == trusted_fp

    def revoke_trust(self, server_id: str) -> bool:
        """Revoke user authorization for a server."""
        if server_id in self._trusted_fingerprints:
            del self._trusted_fingerprints[server_id]
            self._save()
            return True
        return False

    def list_trusted_fingerprints(self) -> Dict[str, str]:
        """Return a copy of all authorized server fingerprints."""
        return dict(self._trusted_fingerprints)

    def verify_and_apply_trust(self, config: MCPServerConfig) -> None:
        """Update a server's process_trust state based on the authoritative trust store."""
        if self.is_server_trusted(config):
            config.process_trust = ProcessTrustLevel.USER_EXPLICITLY_TRUSTED
        else:
            config.process_trust = ProcessTrustLevel.UNTRUSTED
