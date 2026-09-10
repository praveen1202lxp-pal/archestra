"""MCP Client implementing JSON-RPC 2.0 protocol over an MCP transport."""

import time
from typing import Any, Dict, List, Optional

from fusion_agent.mcp.transport import MCPTransport, MCPTransportError


class MCPClientError(Exception):
    """Base error for MCP protocol client."""
    pass


class MCPRemoteError(MCPClientError):
    """Raised when the MCP server returns a JSON-RPC error response."""
    def __init__(self, code: int, message: str, data: Optional[Any] = None):
        super().__init__(f"MCP error {code}: {message}")
        self.code = code
        self.error_message = message
        self.data = data


class MCPClient:
    """Client for speaking JSON-RPC 2.0 with an MCP Server."""

    def __init__(self, server_id: str, transport: MCPTransport):
        self.server_id = server_id
        self.transport = transport
        self._req_id = 0
        self._is_initialized = False
        self.server_info: Dict[str, Any] = {}
        self.server_capabilities: Dict[str, Any] = {}

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def initialize(self, client_name: str = "fusion", client_version: str = "0.1.0") -> Dict[str, Any]:
        """Execute MCP initialization handshake."""
        if self._is_initialized:
            return self.server_info

        req_id = self._next_id()
        init_payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": client_name,
                    "version": client_version,
                },
            },
        }

        resp = self.transport.send_receive(init_payload)
        if "error" in resp:
            err = resp["error"]
            raise MCPRemoteError(err.get("code", -1), err.get("message", "Unknown error"), err.get("data"))

        result = resp.get("result", {})
        self.server_info = result.get("serverInfo", {})
        self.server_capabilities = result.get("capabilities", {})

        # Send notifications/initialized per MCP spec
        self.transport.send_notification({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        })
        self._is_initialized = True
        return self.server_info

    def list_tools(self) -> List[Dict[str, Any]]:
        """Query available tools via tools/list, handling pagination if cursor returned."""
        if not self._is_initialized:
            self.initialize()

        tools: List[Dict[str, Any]] = []
        cursor: Optional[str] = None

        while True:
            params: Dict[str, Any] = {}
            if cursor:
                params["cursor"] = cursor

            req_id = self._next_id()
            req_payload = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "tools/list",
                "params": params,
            }

            resp = self.transport.send_receive(req_payload)
            if "error" in resp:
                err = resp["error"]
                raise MCPRemoteError(err.get("code", -1), err.get("message", "Unknown error"), err.get("data"))

            result = resp.get("result", {})
            current_tools = result.get("tools", [])
            tools.extend(current_tools)

            cursor = result.get("nextCursor")
            if not cursor:
                break

        return tools

    def call_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Invoke a tool via tools/call and return raw MCP tool response."""
        if not self._is_initialized:
            self.initialize()

        req_id = self._next_id()
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": arguments,
            },
        }

        resp = self.transport.send_receive(payload, timeout=timeout)
        if "error" in resp:
            err = resp["error"]
            raise MCPRemoteError(err.get("code", -1), err.get("message", "Unknown error"), err.get("data"))

        return resp.get("result", {})

    def ping(self) -> float:
        """Measure latency using a ping call or initialization check."""
        start = time.perf_counter()
        if not self._is_initialized:
            self.initialize()
        else:
            req_id = self._next_id()
            payload = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "ping",
                "params": {},
            }
            try:
                self.transport.send_receive(payload, timeout=5.0)
            except Exception:
                # If ping not implemented by server, check if process is alive
                if not self.transport.is_alive():
                    raise MCPTransportError("Transport process is dead.")
        return (time.perf_counter() - start) * 1000.0

    def close(self) -> None:
        """Shut down the client and transport."""
        self._is_initialized = False
        self.transport.close()
