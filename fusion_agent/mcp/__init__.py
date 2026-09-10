"""Model Context Protocol (MCP) support for Fusion Agent."""

from fusion_agent.mcp.approval import MCPApprovalHandler, hash_tool_request
from fusion_agent.mcp.budgeter import ToolResultBudgeter
from fusion_agent.mcp.client import MCPClient, MCPClientError, MCPRemoteError
from fusion_agent.mcp.gateway import MCPGateway
from fusion_agent.mcp.models import (
    DataSensitivityClassification,
    MCPEvidence,
    MCPServerConfig,
    PolicyDecision,
    ProcessTrustLevel,
    ServerHealthState,
    SideEffectClassification,
    ToolCapability,
    ToolCategory,
    ToolRequest,
    ToolResult,
    TrustLevel,
)
from fusion_agent.mcp.policy import MCPPolicyEngine
from fusion_agent.mcp.redaction import recursive_redact_secrets
from fusion_agent.mcp.registry import MCPServerRegistry
from fusion_agent.mcp.transport import (
    MCPTimeoutError,
    MCPTransport,
    MCPTransportError,
    MCPUntrustedProcessError,
    StdioMCPTransport,
)
from fusion_agent.mcp.trust import MCPServerTrustStore

__all__ = [
    "ToolCategory",
    "SideEffectClassification",
    "DataSensitivityClassification",
    "ProcessTrustLevel",
    "TrustLevel",
    "PolicyDecision",
    "ServerHealthState",
    "MCPServerConfig",
    "ToolCapability",
    "ToolRequest",
    "ToolResult",
    "MCPEvidence",
    "MCPTransport",
    "StdioMCPTransport",
    "MCPTransportError",
    "MCPTimeoutError",
    "MCPUntrustedProcessError",
    "MCPClient",
    "MCPClientError",
    "MCPRemoteError",
    "MCPServerRegistry",
    "MCPServerTrustStore",
    "MCPPolicyEngine",
    "ToolResultBudgeter",
    "MCPApprovalHandler",
    "hash_tool_request",
    "MCPGateway",
    "recursive_redact_secrets",
]
