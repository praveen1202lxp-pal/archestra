import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from fusion_agent.mcp.client import MCPClient
from fusion_agent.mcp.models import (
    CapabilityType,
    DataSensitivityClassification,
    MCPServerConfig,
    ProcessTrustLevel,
    ServerHealthState,
    SideEffectClassification,
    ToolCapability,
    TrustLevel,
)
from fusion_agent.mcp.transport import MCPUntrustedProcessError, StdioMCPTransport
from fusion_agent.mcp.trust import MCPServerTrustStore

# Built-in authoritative safe read-only catalog for common standard tools
KNOWN_SAFE_TOOL_CATALOG: Dict[str, Dict[str, Any]] = {
    "issues.get": {
        "capabilities": {CapabilityType.READ_ONLY, CapabilityType.NETWORK_READ},
        "side_effect": SideEffectClassification.NONE,
        "sensitivity": DataSensitivityClassification.PUBLIC,
        "requires_human_approval": False,
        "supports_idempotency": True,
    },
    "docs.search": {
        "capabilities": {CapabilityType.READ_ONLY, CapabilityType.NETWORK_READ},
        "side_effect": SideEffectClassification.NONE,
        "sensitivity": DataSensitivityClassification.PUBLIC,
        "requires_human_approval": False,
        "supports_idempotency": True,
    },
    "github.get_issue": {
        "capabilities": {CapabilityType.READ_ONLY, CapabilityType.NETWORK_READ},
        "side_effect": SideEffectClassification.NONE,
        "sensitivity": DataSensitivityClassification.PUBLIC,
        "requires_human_approval": False,
        "supports_idempotency": True,
    },
}


class MCPServerRegistry:
    """Manages configured MCP servers, transport lifecycles, and tool capability caches.
    
    CRITICAL: Security classification is strictly Fusion-controlled. Server descriptions,
    names, or schema texts are treated as UNTRUSTED DATA and are never used to grant permissions.
    """

    def __init__(
        self,
        configs: Optional[List[MCPServerConfig]] = None,
        trust_store: Optional[MCPServerTrustStore] = None,
    ):
        self.trust_store = trust_store
        self.configs: Dict[str, MCPServerConfig] = {}
        self.clients: Dict[str, MCPClient] = {}
        self.capabilities: Dict[Tuple[str, str], ToolCapability] = {}  # (server_id, tool_name) -> ToolCapability
        self.health_states: Dict[str, ServerHealthState] = {}
        self.last_health_checks: Dict[str, str] = {}
        self.health_latencies: Dict[str, float] = {}

        if configs:
            for cfg in configs:
                self.register_server(cfg)

    def register_server(self, config: MCPServerConfig) -> None:
        """Register a server configuration, deriving effective trust strictly from trust store."""
        config.process_trust = ProcessTrustLevel.UNTRUSTED
        if self.trust_store:
            self.trust_store.verify_and_apply_trust(config)
        self.configs[config.server_id] = config
        self.health_states[config.server_id] = ServerHealthState.HEALTHY if config.enabled else ServerHealthState.DISABLED

    def get_server_config(self, server_id: str) -> Optional[MCPServerConfig]:
        """Retrieve configuration for a server."""
        cfg = self.configs.get(server_id)
        if cfg:
            if self.trust_store:
                self.trust_store.verify_and_apply_trust(cfg)
            else:
                cfg.process_trust = ProcessTrustLevel.UNTRUSTED
        return cfg

    def list_servers(self) -> List[MCPServerConfig]:
        """Return all registered server configurations."""
        for cfg in self.configs.values():
            if self.trust_store:
                self.trust_store.verify_and_apply_trust(cfg)
            else:
                cfg.process_trust = ProcessTrustLevel.UNTRUSTED
        return list(self.configs.values())

    def list_tools(self, server_id: str) -> List[ToolCapability]:
        """Return tool capabilities for a server, attempting discovery if not cached."""
        if server_id not in self.configs:
            return []
        existing = [c for (s_id, _), c in self.capabilities.items() if s_id == server_id]
        if existing:
            return existing
        try:
            return self.discover_tools_for_server(server_id)
        except Exception:
            return []

    def list_all_tools(self) -> List[ToolCapability]:
        """Return all registered/discovered tool capabilities across all servers."""
        return self.list_all_capabilities()

    def get_client(self, server_id: str) -> MCPClient:
        """Get or construct an active MCPClient for the given server after verifying process trust."""
        if server_id not in self.configs:
            raise KeyError(f"Server '{server_id}' is not registered.")

        cfg = self.configs[server_id]
        if self.trust_store:
            self.trust_store.verify_and_apply_trust(cfg)
        else:
            cfg.process_trust = ProcessTrustLevel.UNTRUSTED

        if not cfg.enabled:
            raise ValueError(f"Server '{server_id}' is disabled.")

        if cfg.process_trust != ProcessTrustLevel.USER_EXPLICITLY_TRUSTED:
            # If trust was revoked or altered, evict existing client
            if server_id in self.clients:
                try:
                    self.clients[server_id].close()
                except Exception:
                    pass
                del self.clients[server_id]
            raise MCPUntrustedProcessError(
                f"MCP server '{server_id}' executable '{cfg.command}' is UNTRUSTED. "
                f"Fusion will not launch unapproved local executables. Explicit user trust is required."
            )

        if server_id not in self.clients:
            if cfg.transport == "stdio":
                transport = StdioMCPTransport(cfg)
            else:
                raise NotImplementedError(f"Transport '{cfg.transport}' is not supported in V1.")
            self.clients[server_id] = MCPClient(server_id=server_id, transport=transport)

        return self.clients[server_id]

    def normalize_tool_capability(self, server_id: str, raw_tool: Dict[str, Any]) -> ToolCapability:
        """Categorize raw MCP tool definitions using authoritative Fusion-controlled rules.
        
        CRITICAL: Tool descriptions from external servers are UNTRUSTED METADATA.
        Classification comes strictly from:
        1. Explicit Fusion / user configuration in MCPServerConfig.tools
        2. Known built-in safe catalog mappings
        3. Conservative default: UNKNOWN capabilities defaulting to DENY.
        """
        name = raw_tool.get("name", "")
        desc = raw_tool.get("description", "")
        schema = raw_tool.get("inputSchema", {})
        server_cfg = self.configs.get(server_id)

        # Compute authoritative classification fingerprint
        fp_payload = {
            "server_id": server_id,
            "tool_name": name,
            "schema": schema,
            "command": server_cfg.command if server_cfg else "",
            "args": server_cfg.args if server_cfg else [],
            "explicit_cfg": server_cfg.tools.get(name) if (server_cfg and server_cfg.tools) else None,
        }
        classification_fp = hashlib.sha256(json.dumps(fp_payload, sort_keys=True).encode("utf-8")).hexdigest()

        # 1. Explicit Fusion/user configuration overrides
        if server_cfg and server_cfg.tools and name in server_cfg.tools:
            tool_cfg = server_cfg.tools[name]
            raw_caps = tool_cfg.get("capabilities", [])
            caps = set()
            for c in raw_caps:
                try:
                    caps.add(CapabilityType(c) if isinstance(c, str) else c)
                except ValueError:
                    caps.add(CapabilityType.UNKNOWN)
            if not caps:
                caps = {CapabilityType.UNKNOWN}

            side_effect = tool_cfg.get("side_effect", SideEffectClassification.DESTRUCTIVE_EXTERNAL)
            if isinstance(side_effect, str):
                try:
                    side_effect = SideEffectClassification(side_effect)
                except ValueError:
                    side_effect = SideEffectClassification.DESTRUCTIVE_EXTERNAL

            sensitivity = tool_cfg.get("sensitivity", DataSensitivityClassification.INTERNAL)
            if isinstance(sensitivity, str):
                try:
                    sensitivity = DataSensitivityClassification(sensitivity)
                except ValueError:
                    sensitivity = DataSensitivityClassification.CONFIDENTIAL
            req_approval = tool_cfg.get("requires_human_approval", True)
            supports_idempotency = tool_cfg.get("supports_idempotency", False)

            return ToolCapability(
                server_id=server_id,
                tool_name=name,
                description=desc,
                input_schema=schema,
                capabilities=caps,
                side_effect=side_effect,
                sensitivity=sensitivity,
                requires_human_approval=req_approval,
                supports_idempotency=supports_idempotency,
                timeout_seconds=server_cfg.timeout_seconds if server_cfg else 30.0,
                classification_fingerprint=classification_fp,
            )

        # 2. Known built-in safe catalog mapping
        if name in KNOWN_SAFE_TOOL_CATALOG:
            cat_entry = KNOWN_SAFE_TOOL_CATALOG[name]
            return ToolCapability(
                server_id=server_id,
                tool_name=name,
                description=desc,
                input_schema=schema,
                capabilities=set(cat_entry["capabilities"]),
                side_effect=cat_entry["side_effect"],
                sensitivity=cat_entry["sensitivity"],
                requires_human_approval=cat_entry["requires_human_approval"],
                supports_idempotency=cat_entry["supports_idempotency"],
                timeout_seconds=server_cfg.timeout_seconds if server_cfg else 30.0,
                classification_fingerprint=classification_fp,
            )

        # 3. Unknown tool on unknown/untrusted server -> Conservative default-deny
        return ToolCapability(
            server_id=server_id,
            tool_name=name,
            description=desc,
            input_schema=schema,
            capabilities={CapabilityType.UNKNOWN},
            side_effect=SideEffectClassification.DESTRUCTIVE_EXTERNAL,
            sensitivity=DataSensitivityClassification.CONFIDENTIAL,
            requires_human_approval=True,
            supports_idempotency=False,
            timeout_seconds=server_cfg.timeout_seconds if server_cfg else 30.0,
            classification_fingerprint=classification_fp,
        )

    def discover_tools_for_server(self, server_id: str) -> List[ToolCapability]:
        """Discover tools from an MCP server and cache normalized capabilities."""
        client = self.get_client(server_id)
        raw_tools = client.list_tools()

        caps: List[ToolCapability] = []
        for raw in raw_tools:
            cap = self.normalize_tool_capability(server_id, raw)
            self.capabilities[(server_id, cap.tool_name)] = cap
            caps.append(cap)
        return caps

    def get_capability(self, server_id: str, tool_name: str) -> Optional[ToolCapability]:
        """Look up a normalized capability from cache, checking static config and safe catalog before discovery."""
        key = (server_id, tool_name)
        if key in self.capabilities:
            return self.capabilities[key]

        # 1. Explicit static tool configuration
        if server_id in self.configs and self.configs[server_id].tools and tool_name in self.configs[server_id].tools:
            cap = self.normalize_tool_capability(server_id, {"name": tool_name})
            self.capabilities[key] = cap
            return cap

        # 2. Known built-in safe catalog mapping
        if tool_name in KNOWN_SAFE_TOOL_CATALOG:
            cap = self.normalize_tool_capability(server_id, {"name": tool_name})
            self.capabilities[key] = cap
            return cap

        # 3. Dynamic discovery only if server is enabled and explicitly trusted
        if server_id in self.configs and self.configs[server_id].enabled:
            cfg = self.configs[server_id]
            if self.trust_store:
                self.trust_store.verify_and_apply_trust(cfg)
            if cfg.process_trust == ProcessTrustLevel.USER_EXPLICITLY_TRUSTED:
                try:
                    self.discover_tools_for_server(server_id)
                    return self.capabilities.get(key)
                except Exception:
                    return None
        return None


    def list_all_capabilities(self) -> List[ToolCapability]:
        """Return all currently cached tool capabilities."""
        return list(self.capabilities.values())

    def check_health(self, server_id: str) -> Tuple[ServerHealthState, float, Optional[str]]:
        """Run health check against a server."""
        if server_id not in self.configs:
            return ServerHealthState.UNKNOWN, 0.0, "Server not registered"

        cfg = self.configs[server_id]
        if not cfg.enabled:
            return ServerHealthState.DISABLED, 0.0, "Server disabled"

        now = datetime.now(timezone.utc).isoformat()
        try:
            client = self.get_client(server_id)
            latency = client.ping()
            self.health_states[server_id] = ServerHealthState.HEALTHY
            self.last_health_checks[server_id] = now
            self.health_latencies[server_id] = latency
            return ServerHealthState.HEALTHY, latency, None
        except Exception as exc:
            self.health_states[server_id] = ServerHealthState.UNREACHABLE
            self.last_health_checks[server_id] = now
            self.health_latencies[server_id] = 0.0
            return ServerHealthState.UNREACHABLE, 0.0, str(exc)

    def close_all(self) -> None:
        """Shut down all active server clients and release transports."""
        for client in self.clients.values():
            try:
                client.close()
            except Exception:
                pass
        self.clients.clear()
