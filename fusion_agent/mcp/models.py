"""Model Context Protocol (MCP) domain models and contracts for Fusion."""

from dataclasses import asdict, dataclass, field
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Any, Dict, List, Optional, Set


def _hash_file_sha256(filepath: str) -> Optional[str]:
    """Compute SHA-256 hash of a local file safely in 64KB blocks."""
    try:
        p = Path(filepath)
        if p.is_file():
            hasher = hashlib.sha256()
            with open(p, "rb") as f:
                while chunk := f.read(65536):
                    hasher.update(chunk)
            return hasher.hexdigest()
    except Exception:
        pass
    return None


class CapabilityType(str, Enum):
    """Fine-grained capability flags for external tools."""
    READ_ONLY = "READ_ONLY"
    REPOSITORY_READ = "REPOSITORY_READ"
    FILE_WRITE = "FILE_WRITE"
    SHELL_EXECUTION = "SHELL_EXECUTION"
    NETWORK_READ = "NETWORK_READ"
    NETWORK_WRITE = "NETWORK_WRITE"
    EXTERNAL_MUTATION = "EXTERNAL_MUTATION"
    SECRET_ACCESS = "SECRET_ACCESS"
    UNKNOWN = "UNKNOWN"


# Backward compatibility alias
ToolCategory = CapabilityType


class SideEffectClassification(str, Enum):
    """Classification of tool side effects."""
    NONE = "NONE"
    IDEMPOTENT_EXTERNAL = "IDEMPOTENT_EXTERNAL"
    STATE_CHANGE_EXTERNAL = "STATE_CHANGE_EXTERNAL"
    LOCAL_DISK_MUTATION = "LOCAL_DISK_MUTATION"
    DESTRUCTIVE_EXTERNAL = "DESTRUCTIVE_EXTERNAL"
    IRREVERSIBLE = "IRREVERSIBLE"


class DataSensitivityClassification(str, Enum):
    """Classification of data accessed or returned by a tool."""
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    SECRET_BEARING = "SECRET_BEARING"


class ProcessTrustLevel(str, Enum):
    """Host execution trust classification for an MCP server process executable.

    CRITICAL DISTINCTION:
    1. Process Trust: Controls whether Fusion is permitted to spawn the host executable.
       A local MCP server process runs with host-level user permissions unless an OS-level
       sandbox exists. Fusion V1 DOES NOT provide an OS kernel sandbox.
       Therefore, no process is labeled 'SANDBOXED'.
    2. Data/RPC Trust: Even when a process is explicitly user-trusted to launch, all tool descriptions,
       schemas, and returned content remain UNTRUSTED DATA that must be filtered and demarcated.
    """
    UNTRUSTED = "UNTRUSTED"
    USER_EXPLICITLY_TRUSTED = "USER_EXPLICITLY_TRUSTED"


class TrustLevel(str, Enum):
    """Backward-compatibility wrapper for ProcessTrustLevel."""
    UNTRUSTED = "UNTRUSTED"
    LOCAL_TRUSTED = "LOCAL_TRUSTED"
    USER_EXPLICITLY_TRUSTED = "USER_EXPLICITLY_TRUSTED"
    SANDBOXED = "SANDBOXED"  # Deprecated alias


class PolicyDecision(str, Enum):
    """Outcome of MCP policy evaluation."""
    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_HUMAN_APPROVAL = "REQUIRE_HUMAN_APPROVAL"


class ServerHealthState(str, Enum):
    """Liveness status of an MCP server."""
    UNKNOWN = "UNKNOWN"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNREACHABLE = "UNREACHABLE"
    DISABLED = "DISABLED"


class ProviderOutputType(str, Enum):
    """Classification of a provider stage output."""
    FINAL_RESPONSE = "FINAL_RESPONSE"
    TOOL_REQUEST = "TOOL_REQUEST"
    CONTEXT_INSUFFICIENT = "CONTEXT_INSUFFICIENT"


@dataclass
class MCPServerConfig:
    """Configuration for an external MCP server.
    
    CRITICAL: Does NOT contain raw API keys or tokens.
    Stores environment variable references only (env_vars / env_mapping),
    which are resolved directly from host environment into the child process at launch.
    """
    server_id: str
    display_name: str = ""
    transport: str = "stdio"
    command: Optional[str] = None
    args: List[str] = field(default_factory=list)
    cwd: Optional[str] = None
    allow_repo_cwd: bool = False
    env_vars: List[str] = field(default_factory=list)               # Host env var names to pass through
    env_mapping: Dict[str, str] = field(default_factory=dict)       # child_var_name -> host_var_name
    tools: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # Explicit Fusion-controlled tool configs
    enabled: bool = True
    process_trust: ProcessTrustLevel = ProcessTrustLevel.UNTRUSTED
    timeout_seconds: float = 30.0
    max_output_bytes: int = 2_000_000

    @property
    def trust_level(self) -> ProcessTrustLevel:
        return self.process_trust

    @trust_level.setter
    def trust_level(self, value: Any) -> None:
        if isinstance(value, str):
            if value == "USER_EXPLICITLY_TRUSTED" or value == "LOCAL_TRUSTED":
                self.process_trust = ProcessTrustLevel.USER_EXPLICITLY_TRUSTED
            else:
                self.process_trust = ProcessTrustLevel.UNTRUSTED
        elif isinstance(value, ProcessTrustLevel):
            self.process_trust = value
        else:
            self.process_trust = ProcessTrustLevel.UNTRUSTED

    def compute_definition_fingerprint(self) -> str:
        """Compute authoritative SHA-256 launch fingerprint.
        
        Cryptographically binds:
        - Server ID
        - Canonical executable path and executable binary SHA-256 hash
        - Launch arguments
        - SHA-256 hash of any local entrypoint script or file referenced in arguments
        - Environment variable references (names/mappings only, NEVER secret values)
        - Working directory policy (cwd and allow_repo_cwd)
        """
        canonical_cmd = ""
        cmd_sha256 = "UNRESOLVED"
        if self.command:
            resolved = shutil.which(self.command)
            if not resolved and os.path.isfile(self.command):
                resolved = self.command
            if resolved and os.path.isfile(resolved):
                canonical_cmd = os.path.realpath(resolved)
                cmd_sha256 = _hash_file_sha256(canonical_cmd) or "UNREADABLE"
            else:
                canonical_cmd = self.command

        script_hashes: Dict[str, Dict[str, str]] = {}
        for idx, arg in enumerate(self.args):
            if os.path.isfile(arg):
                can_arg = os.path.realpath(arg)
                h = _hash_file_sha256(can_arg)
                if h:
                    script_hashes[str(idx)] = {
                        "path": can_arg,
                        "sha256": h,
                    }

        payload = {
            "server_id": self.server_id,
            "canonical_command": canonical_cmd,
            "command_sha256": cmd_sha256,
            "args": self.args,
            "script_hashes": script_hashes,
            "env_vars": sorted(self.env_vars),
            "env_mapping": sorted(self.env_mapping.items()),
            "cwd": self.cwd,
            "allow_repo_cwd": self.allow_repo_cwd,
        }
        dumped = json.dumps(payload, sort_keys=True)
        return hashlib.sha256(dumped.encode("utf-8")).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        # Exclude executable trust declarations from configuration serialization
        data.pop("process_trust", None)
        data.pop("trust_level", None)
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MCPServerConfig":
        d = dict(data)
        # Repository-controlled configuration must NEVER self-declare process trust
        d.pop("process_trust", None)
        d.pop("trust_level", None)
        inst = cls(**d)
        inst.process_trust = ProcessTrustLevel.UNTRUSTED
        return inst


@dataclass
class ToolCapability:
    """Normalized representation of a discovered MCP tool.
    
    Authoritative classification comes strictly from explicit Fusion configuration
    or safe built-in policy mappings, never from untrusted server descriptions.
    """
    server_id: str
    tool_name: str
    description: str
    input_schema: Dict[str, Any] = field(default_factory=dict)
    capabilities: Set[CapabilityType] = field(default_factory=lambda: {CapabilityType.UNKNOWN})
    side_effect: SideEffectClassification = SideEffectClassification.NONE
    sensitivity: DataSensitivityClassification = DataSensitivityClassification.INTERNAL
    requires_human_approval: bool = False
    supports_idempotency: bool = False
    timeout_seconds: float = 30.0
    classification_fingerprint: str = ""

    @property
    def category(self) -> CapabilityType:
        """Backward-compatibility property returning the most restrictive capability."""
        if CapabilityType.FILE_WRITE in self.capabilities:
            return CapabilityType.FILE_WRITE
        if CapabilityType.SHELL_EXECUTION in self.capabilities:
            return CapabilityType.SHELL_EXECUTION
        if CapabilityType.SECRET_ACCESS in self.capabilities:
            return CapabilityType.SECRET_ACCESS
        if CapabilityType.EXTERNAL_MUTATION in self.capabilities:
            return CapabilityType.EXTERNAL_MUTATION
        if CapabilityType.NETWORK_WRITE in self.capabilities:
            return CapabilityType.NETWORK_WRITE
        if CapabilityType.REPOSITORY_READ in self.capabilities:
            return CapabilityType.REPOSITORY_READ
        if CapabilityType.NETWORK_READ in self.capabilities:
            return CapabilityType.NETWORK_READ
        if CapabilityType.READ_ONLY in self.capabilities:
            return CapabilityType.READ_ONLY
        return CapabilityType.UNKNOWN

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["capabilities"] = [c.value for c in self.capabilities]
        data["side_effect"] = self.side_effect.value
        data["sensitivity"] = self.sensitivity.value
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ToolCapability":
        d = dict(data)
        if "capabilities" in d and isinstance(d["capabilities"], list):
            d["capabilities"] = {CapabilityType(c) for c in d["capabilities"]}
        if "side_effect" in d and isinstance(d["side_effect"], str):
            d["side_effect"] = SideEffectClassification(d["side_effect"])
        if "sensitivity" in d and isinstance(d["sensitivity"], str):
            d["sensitivity"] = DataSensitivityClassification(d["sensitivity"])
        return cls(**d)


@dataclass
class ToolRequest:
    """Structured tool invocation request emitted by a model or agent."""
    server_id: str
    tool_name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    expected_use: str = ""
    logical_invocation_id: Optional[str] = None
    idempotency_key: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ToolRequest":
        return cls(**data)


@dataclass
class ToolResult:
    """Normalized outcome of an MCP tool invocation returned to Fusion.
    
    CRITICAL: side_effects_occurred is derived strictly from Fusion's authoritative
    pre-execution capability and policy classification. Server-reported metadata is
    treated strictly as untrusted telemetry (server_reported_telemetry) and is never
    used as authoritative security evidence.
    """
    success: bool
    server_id: str
    tool_name: str
    content: str = ""
    structured_data: Optional[Any] = None
    error: Optional[str] = None
    duration_ms: float = 0.0
    size_chars: int = 0
    tokens_consumed: int = 0
    side_effects_occurred: bool = False
    server_reported_telemetry: Optional[Dict[str, Any]] = None  # Untrusted telemetry only
    is_truncated: bool = False
    omission_reason: Optional[str] = None
    is_accepted: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ToolResult":
        return cls(**data)


@dataclass
class NormalizedProviderOutput:
    """Discriminator for provider stage output in the tool-assisted loop."""
    output_type: ProviderOutputType
    response_content: str = ""
    final_content: str = ""
    tool_request: Optional[ToolRequest] = None
    context_expansion_request: Optional[Any] = None
    context_request: Optional[Any] = None

    def __post_init__(self):
        if self.final_content and not self.response_content:
            self.response_content = self.final_content
        elif self.response_content and not self.final_content:
            self.final_content = self.response_content
        if self.context_request is not None and self.context_expansion_request is None:
            self.context_expansion_request = self.context_request
        elif self.context_expansion_request is not None and self.context_request is None:
            self.context_request = self.context_expansion_request


@dataclass
class MCPEvidence:
    """Bounded external evidence retrieved via MCP for inclusion in CodeContext."""
    server_id: str
    tool_name: str
    content: str
    arguments_summary: str = ""
    duration_ms: float = 0.0
    error: Optional[str] = None
    timestamp: str = ""
