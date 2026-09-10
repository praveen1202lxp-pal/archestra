"""Central MCP Tool Gateway enforcing policy, budgeting, audit logging, and process execution."""

import hashlib
import json
import os
import re
import sys
import time
import uuid
from typing import Any, Dict, List, Optional, Set

from fusion_agent.core.budget import TaskBudgetController
from fusion_agent.mcp.approval import MCPApprovalHandler, hash_tool_request
from fusion_agent.mcp.budgeter import ToolResultBudgeter
from fusion_agent.mcp.models import (
    MCPEvidence,
    NormalizedProviderOutput,
    PolicyDecision,
    ProcessTrustLevel,
    ProviderOutputType,
    SideEffectClassification,
    ToolCapability,
    ToolCategory,
    ToolRequest,
    ToolResult,
)
from fusion_agent.mcp.policy import MCPPolicyEngine
from fusion_agent.mcp.redaction import recursive_redact_secrets
from fusion_agent.mcp.registry import MCPServerRegistry
from fusion_agent.mcp.transport import MCPTimeoutError, MCPTransportError, MCPUntrustedProcessError
from fusion_agent.repository.ignore import SecretFilter


class MCPGateway:
    """The authoritative execution and policy boundary for MCP tools in Fusion."""

    def __init__(
        self,
        registry: MCPServerRegistry,
        policy_engine: Optional[MCPPolicyEngine] = None,
        budgeter: Optional[ToolResultBudgeter] = None,
        approval_handler: Optional[MCPApprovalHandler] = None,
    ):
        self.registry = registry
        self.policy_engine = policy_engine or MCPPolicyEngine()
        self.budgeter = budgeter or ToolResultBudgeter()
        self.approval_handler = approval_handler or MCPApprovalHandler()
        self.secret_filter = SecretFilter()

    def execute_tool(
        self,
        request: ToolRequest,
        task_id: str,
        plan_id: Optional[str] = None,
        step_id: Optional[str] = None,
        stage: str = "implementation",
        stage_calls_made: int = 0,
        budget_controller: Optional[TaskBudgetController] = None,
        state_manager: Optional[Any] = None,
    ) -> ToolResult:
        """Execute a ToolRequest through the full policy, approval, budget, and isolation pipeline."""
        start_time = time.perf_counter()
        arg_hash = hash_tool_request(request)

        # Collect known ephemeral secrets resolved dynamically for this MCP process
        known_secrets: Set[str] = set()
        server_cfg = self.registry.get_server_config(request.server_id)
        if server_cfg:
            for var_name in server_cfg.env_vars:
                val = os.environ.get(var_name)
                if val and len(val.strip()) >= 3:
                    known_secrets.add(val.strip())
            for host_var in server_cfg.env_mapping.values():
                val = os.environ.get(host_var)
                if val and len(val.strip()) >= 3:
                    known_secrets.add(val.strip())

        sanitized_args = recursive_redact_secrets(
            json.dumps(request.arguments, sort_keys=True),
            known_secrets,
            self.secret_filter,
        )

        # 1. Look up server and capability
        server_cfg = self.registry.get_server_config(request.server_id)
        if not server_cfg or not server_cfg.enabled:
            return ToolResult(
                success=False,
                server_id=request.server_id,
                tool_name=request.tool_name,
                error=f"MCP server '{request.server_id}' is not configured or is disabled.",
            )

        if server_cfg.process_trust != ProcessTrustLevel.USER_EXPLICITLY_TRUSTED:
            return ToolResult(
                success=False,
                server_id=request.server_id,
                tool_name=request.tool_name,
                error=(
                    f"MCP server '{request.server_id}' executable '{server_cfg.command}' is UNTRUSTED. "
                    f"Fusion will not launch unapproved local executables. Explicit user trust is required."
                ),
            )

        capability = self.registry.get_capability(request.server_id, request.tool_name)
        if not capability:
            return ToolResult(
                success=False,
                server_id=request.server_id,
                tool_name=request.tool_name,
                error=f"Denied: Tool '{request.tool_name}' not found or unclassified on MCP server '{request.server_id}'.",
            )

        # 2. Evaluate Policy
        decision, reason = self.policy_engine.evaluate(request, capability, server_cfg)
        if decision == PolicyDecision.DENY:
            # Audit log denial
            if state_manager and hasattr(state_manager, "record_mcp_invocation"):
                state_manager.record_mcp_invocation(
                    task_id=task_id,
                    plan_id=plan_id,
                    step_id=step_id,
                    provider_stage=stage,
                    server_id=request.server_id,
                    tool_name=request.tool_name,
                    arguments_hash=arg_hash,
                    sanitized_arguments=sanitized_args,
                    policy_decision="DENY",
                    approval_disposition="NOT_REQUESTED",
                    status="DENIED",
                    error_message=reason,
                )
            return ToolResult(
                success=False,
                server_id=request.server_id,
                tool_name=request.tool_name,
                error=reason,
            )

        # 3. Budget Check
        if budget_controller is not None:
            can_call, budget_err = budget_controller.can_call_mcp_tool(stage_calls_made=stage_calls_made)
            if not can_call:
                return ToolResult(
                    success=False,
                    server_id=request.server_id,
                    tool_name=request.tool_name,
                    error=f"MCP budget ceiling reached: {budget_err}",
                )

        # 4. Human Approval Gate
        approval_disposition = "NOT_REQUIRED"
        if decision == PolicyDecision.REQUIRE_HUMAN_APPROVAL or capability.requires_human_approval:
            approved = self.approval_handler.request_approval(request, capability)
            if not approved:
                is_non_interactive = not sys.stdin.isatty()
                err_msg = (
                    "External action declined: non-interactive environment requires explicit approval."
                    if is_non_interactive
                    else "External action declined by user."
                )
                if state_manager and hasattr(state_manager, "record_mcp_invocation"):
                    state_manager.record_mcp_invocation(
                        task_id=task_id,
                        plan_id=plan_id,
                        step_id=step_id,
                        provider_stage=stage,
                        server_id=request.server_id,
                        tool_name=request.tool_name,
                        arguments_hash=arg_hash,
                        sanitized_arguments=sanitized_args,
                        policy_decision=decision.value,
                        approval_disposition="DECLINED",
                        status="DECLINED",
                        error_message=err_msg,
                    )
                return ToolResult(
                    success=False,
                    server_id=request.server_id,
                    tool_name=request.tool_name,
                    error=err_msg,
                )
            approval_disposition = "APPROVED"

        # 5. Write-Ahead Invocation Recording
        invocation_id = str(uuid.uuid4())[:8]
        logical_id = request.logical_invocation_id or f"{task_id}:{plan_id or 'none'}:{step_id or 'none'}:{stage}:{request.server_id}:{request.tool_name}"
        if state_manager and hasattr(state_manager, "record_mcp_invocation_started"):
            state_manager.record_mcp_invocation_started(
                invocation_id=invocation_id,
                task_id=task_id,
                plan_id=plan_id,
                step_id=step_id,
                provider_stage=stage,
                server_id=request.server_id,
                tool_name=request.tool_name,
                arguments_hash=arg_hash,
                sanitized_arguments=sanitized_args,
                policy_decision=decision.value,
                approval_disposition=approval_disposition,
                logical_tool_invocation_id=logical_id,
                idempotency_key=request.idempotency_key,
            )

        # 6. Execute Tool Call via Client
        try:
            client = self.registry.get_client(request.server_id)
            if hasattr(client, "transport") and hasattr(client.transport, "ephemeral_secrets"):
                known_secrets.update(client.transport.ephemeral_secrets)

            raw_response = client.call_tool(
                tool_name=request.tool_name,
                arguments=request.arguments,
                timeout=capability.timeout_seconds,
            )
            duration_ms = (time.perf_counter() - start_time) * 1000.0

            # 7. Parse Content
            content_pieces = []
            structured_content = raw_response.get("content", [])
            is_error = raw_response.get("isError", False)
            server_telemetry = raw_response.get("telemetry") or raw_response.get("meta") or {}
            if "sideEffectsOccurred" in raw_response:
                if not isinstance(server_telemetry, dict):
                    server_telemetry = {"raw": server_telemetry}
                server_telemetry["sideEffectsOccurred"] = raw_response["sideEffectsOccurred"]

            if isinstance(structured_content, list):
                for item in structured_content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        content_pieces.append(item.get("text", ""))
                    elif isinstance(item, str):
                        content_pieces.append(item)
            elif isinstance(structured_content, str):
                content_pieces.append(structured_content)

            full_text = "\n".join(content_pieces)

            # Recursive secret redaction across full_text, structured_data, and telemetry
            full_text = recursive_redact_secrets(full_text, known_secrets, self.secret_filter)
            structured_content = recursive_redact_secrets(structured_content, known_secrets, self.secret_filter)
            server_telemetry = recursive_redact_secrets(server_telemetry, known_secrets, self.secret_filter) if server_telemetry else None

            res = ToolResult(
                success=not is_error,
                server_id=request.server_id,
                tool_name=request.tool_name,
                content=full_text,
                structured_data=structured_content,
                duration_ms=duration_ms,
                side_effects_occurred=(capability.side_effect != SideEffectClassification.NONE),
                server_reported_telemetry=server_telemetry,
                is_accepted=(not is_error),
            )

            # 8. Budget and Bounding
            res = self.budgeter.budget_result(res)

            # 9. Update Audit Ledger
            if state_manager and hasattr(state_manager, "complete_mcp_invocation"):
                state_manager.complete_mcp_invocation(
                    invocation_id=invocation_id,
                    status="COMPLETED" if res.success else "FAILED",
                    duration_ms=duration_ms,
                    result_chars=res.size_chars,
                    result_tokens=res.tokens_consumed,
                    is_truncated=res.is_truncated,
                    error_message=res.error,
                    is_accepted=res.is_accepted,
                )

            # 10. Record in TaskBudgetController
            if budget_controller is not None:
                budget_controller.record_mcp_call(duration_ms=duration_ms, result_tokens=res.tokens_consumed)

            return res

        except Exception as exc:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            err_msg = recursive_redact_secrets(str(exc), known_secrets, self.secret_filter)

            if state_manager and hasattr(state_manager, "complete_mcp_invocation"):
                state_manager.complete_mcp_invocation(
                    invocation_id=invocation_id,
                    status="FAILED",
                    duration_ms=duration_ms,
                    result_chars=0,
                    result_tokens=0,
                    is_truncated=False,
                    error_message=err_msg,
                )

            return ToolResult(
                success=False,
                server_id=request.server_id,
                tool_name=request.tool_name,
                error=err_msg,
                duration_ms=duration_ms,
            )

    def parse_provider_output(self, raw_content: str) -> NormalizedProviderOutput:
        """Parse raw model output into a NormalizedProviderOutput union.

        Returns:
            NormalizedProviderOutput with type TOOL_REQUEST, CONTEXT_INSUFFICIENT, or FINAL_RESPONSE.
        """
        if not raw_content or not str(raw_content).strip():
            return NormalizedProviderOutput(
                output_type=ProviderOutputType.FINAL_RESPONSE,
                final_content="",
            )

        text = str(raw_content).strip()

        # 1. First check if text matches structured ToolRequest (JSON or markdown fence)
        tool_req = self._extract_tool_request(text)
        if tool_req:
            return NormalizedProviderOutput(
                output_type=ProviderOutputType.TOOL_REQUEST,
                tool_request=tool_req,
            )

        # 2. Check for CONTEXT_INSUFFICIENT
        if "CONTEXT_INSUFFICIENT" in text.upper():
            try:
                from fusion_agent.providers.normalizer import StructuredOutputNormalizer
                exp = StructuredOutputNormalizer.parse_context_expansion_request(text)
                if exp:
                    return NormalizedProviderOutput(
                        output_type=ProviderOutputType.CONTEXT_INSUFFICIENT,
                        context_request=exp,
                    )
            except Exception:
                pass

        # 3. Default to FINAL_RESPONSE
        return NormalizedProviderOutput(
            output_type=ProviderOutputType.FINAL_RESPONSE,
            final_content=text,
        )

    def _extract_tool_request(self, text: str) -> Optional[ToolRequest]:
        """Extract a single ToolRequest from raw content if present."""
        # 1. Try parsing whole text as JSON
        if text.startswith("{") and text.endswith("}"):
            try:
                data = json.loads(text)
                if isinstance(data, dict):
                    req = self._dict_to_tool_request(data)
                    if req:
                        return req
            except Exception:
                pass

        # 2. Check for fenced code blocks with ```tool_request or ```json
        pattern = r"```(?:tool_request|json)?\s*(\{.*?\})\s*```"
        for match in re.finditer(pattern, text, re.DOTALL):
            try:
                data = json.loads(match.group(1))
                if isinstance(data, dict):
                    req = self._dict_to_tool_request(data)
                    if req:
                        return req
            except Exception:
                pass

        # 3. Check for TOOL_REQUEST keyword block
        if "TOOL_REQUEST" in text:
            json_match = re.search(r"\{[^{}]*\"tool_name\"[^{}]*\}", text, re.DOTALL)
            if not json_match:
                json_match = re.search(r"\{[^{}]*\"server_id\"[^{}]*\}", text, re.DOTALL)
            if json_match:
                try:
                    data = json.loads(json_match.group(0))
                    if isinstance(data, dict):
                        req = self._dict_to_tool_request(data)
                        if req:
                            return req
                except Exception:
                    pass

            srv = re.search(r"(?:server_id|server)\s*[:=]\s*[`'\"]?([\w\-.]+)[\"']?", text, re.IGNORECASE)
            tool = re.search(r"(?:tool_name|tool)\s*[:=]\s*[`'\"]?([\w\-.]+)[\"']?", text, re.IGNORECASE)
            if srv and tool:
                args = {}
                args_match = re.search(r"(?:arguments|args|parameters)\s*[:=]\s*(\{.*?\})", text, re.DOTALL | re.IGNORECASE)
                if args_match:
                    try:
                        args = json.loads(args_match.group(1))
                    except Exception:
                        args = {}
                reason_match = re.search(r"reason\s*[:=]\s*(.+)", text, re.IGNORECASE)
                reason = reason_match.group(1).strip() if reason_match else ""
                return ToolRequest(
                    server_id=srv.group(1).strip(),
                    tool_name=tool.group(1).strip(),
                    arguments=args if isinstance(args, dict) else {},
                    reason=reason,
                )

        return None

    def _dict_to_tool_request(self, data: Dict[str, Any]) -> Optional[ToolRequest]:
        """Convert a parsed dict into a ToolRequest if valid."""
        if "tool_request" in data and isinstance(data["tool_request"], dict):
            data = data["tool_request"]

        srv = data.get("server_id") or data.get("server")
        tool = data.get("tool_name") or data.get("tool")
        if not srv or not tool:
            return None

        args = data.get("arguments") or data.get("args") or data.get("parameters") or {}
        if not isinstance(args, dict):
            args = {}

        return ToolRequest(
            server_id=str(srv).strip(),
            tool_name=str(tool).strip(),
            arguments=args,
            reason=str(data.get("reason", "")),
            expected_use=str(data.get("expected_use", "")),
            logical_invocation_id=data.get("logical_invocation_id"),
            idempotency_key=data.get("idempotency_key"),
        )

    def assess_interrupted_invocation_recovery(self, invocation: Dict[str, Any]) -> str:
        """Evaluate how an interrupted MCP invocation should be handled during task recovery.

        Returns:
            - 'READ_ONLY_RETRY_PERMITTED': Safe read-only tool invocation can be retried.
            - 'IDEMPOTENT_RETRY_PERMITTED': Authoritatively verified idempotent mutation can be safely retried.
            - 'REQUIRES_MANUAL_RECONCILIATION': Ambiguous mutation without verified idempotency requires manual reconciliation.
        """
        server_id = invocation.get("server_id", "")
        tool_name = invocation.get("tool_name", "")
        idempotency_key = invocation.get("idempotency_key")

        capability = self.registry.get_capability(server_id, tool_name)
        if not capability:
            return "REQUIRES_MANUAL_RECONCILIATION"

        # If read-only and no side effects, safe to retry
        if capability.side_effect == SideEffectClassification.NONE:
            return "READ_ONLY_RETRY_PERMITTED"

        # For mutations, ONLY allow retry if explicitly configured or authoritatively known to support idempotency
        if capability.supports_idempotency and idempotency_key:
            return "IDEMPOTENT_RETRY_PERMITTED"

        return "REQUIRES_MANUAL_RECONCILIATION"
