"""Policy engine enforcing security and execution boundaries over MCP tool calls."""

from typing import Any, Dict, Optional, Tuple

from fusion_agent.mcp.models import (
    CapabilityType,
    DataSensitivityClassification,
    MCPServerConfig,
    PolicyDecision,
    SideEffectClassification,
    ToolCapability,
    ToolRequest,
    TrustLevel,
)


class MCPPolicyEngine:
    """Evaluates whether an MCP ToolRequest is permitted, denied, or requires human confirmation.
    
    CRITICAL: Providers never receive unrestricted MCP authority. Provider prompt text
    or reasoning cannot override or loosen policy decisions.
    
    Evaluates multiple capability dimensions. The most restrictive rule always wins.
    """

    def __init__(
        self,
        allow_external_mutations: bool = False,
        allow_secret_access: bool = False,
    ):
        self.allow_external_mutations = allow_external_mutations
        self.allow_secret_access = allow_secret_access

    def evaluate(
        self,
        request: ToolRequest,
        capability: ToolCapability,
        server_config: Optional[MCPServerConfig] = None,
    ) -> Tuple[PolicyDecision, str]:
        """Evaluate a tool request against all capability classifications and security policies."""
        server_id = request.server_id
        tool_name = request.tool_name
        trust = server_config.trust_level if server_config else TrustLevel.SANDBOXED
        caps = capability.capabilities

        # 1. HARD DENY: File writes directly from MCP
        if CapabilityType.FILE_WRITE in caps:
            return (
                PolicyDecision.DENY,
                f"Policy DENIED: MCP tool '{server_id}.{tool_name}' requests repository file write ({CapabilityType.FILE_WRITE.value}). "
                f"Repository modifications are strictly restricted to Fusion's WorkspaceEditor and ExecutionBroker.",
            )

        # 2. HARD DENY: Arbitrary Shell Execution
        if CapabilityType.SHELL_EXECUTION in caps:
            return (
                PolicyDecision.DENY,
                f"Policy DENIED: MCP tool '{server_id}.{tool_name}' requests shell execution ({CapabilityType.SHELL_EXECUTION.value}). "
                f"Arbitrary shell commands cannot execute through MCP.",
            )

        # 3. HARD DENY: Unknown / Unclassified Tools
        if CapabilityType.UNKNOWN in caps or not caps:
            return (
                PolicyDecision.DENY,
                f"Policy DENIED: Tool '{server_id}.{tool_name}' is unclassified or unknown. "
                f"Fusion defaults to fail-closed DENY for unconfigured tools.",
            )

        # 4. SECRET_ACCESS or SECRET_BEARING: Denied by default
        if (CapabilityType.SECRET_ACCESS in caps or capability.sensitivity == DataSensitivityClassification.SECRET_BEARING) and not self.allow_secret_access:
            return (
                PolicyDecision.DENY,
                f"Policy DENIED: Tool '{server_id}.{tool_name}' involves secret access or secret-bearing data. "
                f"Secret-bearing operations are denied by default policy.",
            )

        # 5. UNTRUSTED Server Trust: All operations require explicit human confirmation
        if trust == TrustLevel.UNTRUSTED:
            return (
                PolicyDecision.REQUIRE_HUMAN_APPROVAL,
                f"Server '{server_id}' is UNTRUSTED. All tool invocations require explicit human confirmation.",
            )

        # 6. External Mutations & Network Writes: Require interactive human approval
        if (
            CapabilityType.EXTERNAL_MUTATION in caps
            or CapabilityType.NETWORK_WRITE in caps
            or capability.side_effect != SideEffectClassification.NONE
            or capability.requires_human_approval
        ):
            return (
                PolicyDecision.REQUIRE_HUMAN_APPROVAL,
                f"MCP tool '{server_id}.{tool_name}' performs external side effects. "
                f"Explicit human confirmation is required.",
            )

        # 7. Safe Read-Only Operations
        safe_read_set = {CapabilityType.READ_ONLY, CapabilityType.REPOSITORY_READ, CapabilityType.NETWORK_READ}
        if caps.issubset(safe_read_set):
            return (
                PolicyDecision.ALLOW,
                f"Read-only tool '{server_id}.{tool_name}' permitted by policy.",
            )

        # Default fallback: Deny
        return (
            PolicyDecision.DENY,
            f"Policy DENY: Tool '{server_id}.{tool_name}' cannot be verified as safe.",
        )
