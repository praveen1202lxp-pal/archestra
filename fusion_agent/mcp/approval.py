"""Interactive human approval gate for external mutating MCP tool calls."""

import hashlib
import json
import sys
from typing import Any, Callable, Dict, Optional

from fusion_agent.mcp.models import ToolCapability, ToolRequest


def hash_tool_request(request: ToolRequest) -> str:
    """Compute a deterministic SHA-256 hash of the tool invocation request."""
    payload = json.dumps(
        {
            "server_id": request.server_id,
            "tool_name": request.tool_name,
            "arguments": request.arguments,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class MCPApprovalHandler:
    """Manages explicit human approval for actions with external side effects.
    
    CRITICAL: Default is always 'N'. No --yes bypass flag is permitted.
    Approvals bind strictly to the exact validated ToolRequest.
    """

    def __init__(
        self,
        interactive_prompt_fn: Optional[Callable[[ToolRequest, ToolCapability], bool]] = None,
        is_interactive: Optional[bool] = None,
    ):
        self._prompt_fn = interactive_prompt_fn
        self._is_interactive = is_interactive

    def request_approval(self, request: ToolRequest, capability: ToolCapability) -> bool:
        """Prompt user or execute configured approval callback. Returns True if approved, False otherwise."""
        if self._prompt_fn is not None:
            return bool(self._prompt_fn(request, capability))

        # Check interactive environment
        is_tty = sys.stdin is not None and hasattr(sys.stdin, "isatty") and sys.stdin.isatty()
        interactive = self._is_interactive if self._is_interactive is not None else is_tty

        if not interactive:
            # Noninteractive/headless/test execution automatically declines
            return False

        # Default CLI interactive prompt
        try:
            sanitized_args = json.dumps(request.arguments, indent=2)
        except Exception:
            sanitized_args = str(request.arguments)

        print("\n" + "=" * 60)
        print("MCP EXTERNAL ACTION APPROVAL REQUIRED")
        print("=" * 60)
        print(f"Tool:    {request.server_id}.{request.tool_name}")
        print(f"Action:  {capability.description or request.reason or 'External side effect'}")
        print(f"Reason:  {request.expected_use or request.reason or 'Not specified'}")
        print(f"Args:\n{sanitized_args}")
        print("-" * 60)

        try:
            choice = input("Execute external action? [y/N]: ").strip().lower()
            approved = choice in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            approved = False

        print("=" * 60 + "\n")
        return approved
