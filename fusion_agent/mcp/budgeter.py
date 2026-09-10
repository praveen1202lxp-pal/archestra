"""Context budgeting and output truncation for MCP tool results."""

from typing import Optional, Tuple

from fusion_agent.mcp.models import ToolResult


class ToolResultBudgeter:
    """Enforces strict character and token limits on MCP tool output to prevent context bloat."""

    def __init__(
        self,
        max_result_chars: int = 8000,
        max_result_tokens: int = 2000,
        max_chars_per_call: Optional[int] = None,
    ):
        self.max_result_chars = max_chars_per_call if max_chars_per_call is not None else max_result_chars
        self.max_result_tokens = max_result_tokens

    def budget_result(self, raw_result: ToolResult) -> ToolResult:
        """Apply bounded limits to ToolResult content."""
        content = raw_result.content or ""
        orig_len = len(content)

        if orig_len <= self.max_result_chars:
            raw_result.size_chars = orig_len
            raw_result.tokens_consumed = max(1, orig_len // 4)
            return raw_result

        # Truncate output preserving head and tail, ensuring bounded_content <= max_result_chars
        marker_template = f"\n\n... [TRUNCATED: omitted {orig_len} characters of external tool output to satisfy context budget ({self.max_result_chars} chars max)] ...\n\n"
        marker_len = len(marker_template)
        remaining_chars = max(50, self.max_result_chars - marker_len)
        keep_head = int(remaining_chars * 0.7)
        keep_tail = max(0, remaining_chars - keep_head)
        omitted = orig_len - (keep_head + keep_tail)

        marker = f"\n\n... [TRUNCATED: omitted {omitted} characters of external tool output to satisfy context budget ({self.max_result_chars} chars max)] ...\n\n"
        bounded_content = content[:keep_head] + marker + (content[-keep_tail:] if keep_tail > 0 else "")

        raw_result.content = bounded_content
        raw_result.size_chars = len(bounded_content)
        raw_result.tokens_consumed = max(1, len(bounded_content) // 4)
        raw_result.is_truncated = True
        raw_result.omission_reason = f"Exceeded max MCP tool result limit ({self.max_result_chars} chars). Omitted {omitted} chars."

        return raw_result
