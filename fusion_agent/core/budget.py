"""Task budget controller and guardrail enforcer with refined telemetry for Milestone 7."""

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from fusion_agent.config.schema import DeliberationConfig


@dataclass
class StageUsageRecord:
    """Record of usage for a single provider call with detailed token breakdown."""
    stage: str
    provider_name: str
    duration_ms: float
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    fusion_context_tokens: Optional[int] = None
    provider_managed_input_overhead: Optional[int] = None
    reasoning_tokens: Optional[int] = None
    visible_output_tokens: Optional[int] = None
    cached_tokens: Optional[int] = None
    raw_usage: Optional[Dict[str, Any]] = None
    is_premium: bool = False

    def __post_init__(self):
        if self.provider_managed_input_overhead is None and self.input_tokens is not None and self.fusion_context_tokens is not None:
            self.provider_managed_input_overhead = max(0, self.input_tokens - self.fusion_context_tokens)


class TaskBudgetController:
    """Tracks and enforces hard bounds on calls, tokens, duration, and repairs."""

    def __init__(self, config: DeliberationConfig):
        self.config = config
        self.max_provider_calls = config.max_provider_calls
        self.max_repair_rounds = config.max_repair_rounds
        self.max_duration_seconds = config.max_task_duration_seconds
        self.max_input_tokens = config.max_total_input_tokens
        self.max_output_tokens = config.max_total_output_tokens
        self.max_premium_calls = config.max_premium_provider_calls

        self.max_mcp_calls = getattr(config, "max_mcp_calls_per_task", 10)
        self.max_mcp_calls_per_stage = getattr(config, "max_mcp_calls_per_stage", 3)
        self.max_mcp_duration_seconds = getattr(config, "max_mcp_duration_seconds", 60.0)
        self.max_mcp_result_tokens = getattr(config, "max_mcp_result_tokens", 8000)

        self.start_time = time.perf_counter()
        self.previously_consumed_active_seconds: float = 0.0
        self.calls_made: int = 0
        self.premium_calls_made: int = 0
        self.repair_rounds_used: int = 0
        self.mcp_calls_made: int = 0
        self.cumulative_mcp_duration_ms: float = 0.0
        self.cumulative_mcp_result_tokens: int = 0
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.total_fusion_context_tokens: int = 0
        self.total_provider_managed_overhead: int = 0
        self.total_reasoning_tokens: int = 0
        self.total_cached_tokens: int = 0
        self.total_visible_output_tokens: int = 0
        self.has_measured_tokens: bool = False
        self.call_history: List[StageUsageRecord] = []

    @property
    def elapsed_seconds(self) -> float:
        """Return cumulative active duration since start including prior sessions."""
        return self.previously_consumed_active_seconds + (time.perf_counter() - self.start_time)

    @property
    def repair_rounds_attempted(self) -> int:
        """Alias for repair_rounds_used."""
        return self.repair_rounds_used

    @classmethod
    def restore_from_history(
        cls,
        first: Any,
        second: Any,
        third: Any = None,
    ) -> "TaskBudgetController":
        """Reconstruct budget controller state from SQLite records after an interruption.
        Supports both (task_id, state_manager, config) and (config, task_id, state_manager).
        """
        if isinstance(first, DeliberationConfig):
            config = first
            task_id = str(second)
            state_manager = third
        elif isinstance(third, DeliberationConfig):
            task_id = str(first)
            state_manager = second
            config = third
        elif third is None:
            task_id = str(first)
            state_manager = second
            config = DeliberationConfig()
        else:
            task_id = str(first)
            state_manager = second
            config = third

        controller = cls(config)
        conn = state_manager.db.connect()
        rows = conn.execute(
            "SELECT * FROM agent_runs WHERE task_id = ? ORDER BY timestamp ASC;",
            (task_id,),
        ).fetchall()

        calls_made = 0
        repair_rounds_used = 0
        total_input_tokens = 0
        total_output_tokens = 0
        total_reasoning_tokens = 0
        total_cached_tokens = 0
        total_visible_output_tokens = 0
        total_fusion_context_tokens = 0
        total_provider_managed_overhead = 0
        has_measured = False
        cumulative_duration_sec = 0.0

        for r in rows:
            calls_made += 1
            stage = r["stage"] or ""
            if "repair" in stage.lower():
                repair_rounds_used += 1

            dur_ms = r["duration_ms"] or 0.0
            cumulative_duration_sec += (dur_ms / 1000.0)

            inp = r["input_tokens"]
            outp = r["output_tokens"]
            f_tokens = r["fusion_context_tokens"]
            reas = r["reasoning_tokens"]
            cac = r["cached_tokens"]
            vis = r["visible_output_tokens"]

            if inp is not None:
                total_input_tokens += inp
                has_measured = True
                if f_tokens is not None:
                    overhead = max(0, inp - f_tokens)
                    total_provider_managed_overhead += overhead
            if outp is not None:
                total_output_tokens += outp
                has_measured = True
            if f_tokens is not None:
                total_fusion_context_tokens += f_tokens
            if reas is not None:
                total_reasoning_tokens += reas
            if cac is not None:
                total_cached_tokens += cac
            if vis is not None:
                total_visible_output_tokens += vis

        controller.calls_made = calls_made
        controller.repair_rounds_used = repair_rounds_used
        controller.total_input_tokens = total_input_tokens
        controller.total_output_tokens = total_output_tokens
        controller.total_reasoning_tokens = total_reasoning_tokens
        controller.total_cached_tokens = total_cached_tokens
        controller.total_visible_output_tokens = total_visible_output_tokens
        controller.total_fusion_context_tokens = total_fusion_context_tokens
        controller.total_provider_managed_overhead = total_provider_managed_overhead
        controller.has_measured_tokens = has_measured
        controller.previously_consumed_active_seconds = cumulative_duration_sec

        try:
            mcp_rows = conn.execute(
                "SELECT * FROM mcp_tool_invocations WHERE task_id = ? AND status NOT IN ('DENIED', 'DECLINED');",
                (task_id,),
            ).fetchall()
            for mr in mcp_rows:
                controller.mcp_calls_made += 1
                controller.cumulative_mcp_duration_ms += (mr["duration_ms"] or 0.0)
                controller.cumulative_mcp_result_tokens += (mr["result_tokens"] or 0)
        except Exception:
            pass

        return controller

    def can_call_provider(self, is_premium: bool = False) -> Tuple[bool, Optional[str]]:
        """Check whether budget allows invoking a provider."""
        if self.calls_made >= self.max_provider_calls:
            return False, f"Maximum provider calls ({self.max_provider_calls}) reached."
        if is_premium and self.premium_calls_made >= self.max_premium_calls:
            return False, f"Maximum premium provider calls ({self.max_premium_calls}) reached."
        if self.elapsed_seconds >= self.max_duration_seconds:
            return False, f"Maximum task duration ({self.max_duration_seconds:.1f}s) exceeded."
        if self.max_input_tokens is not None and self.total_input_tokens >= self.max_input_tokens:
            return False, f"Input token budget ({self.max_input_tokens}) exhausted."
        if self.max_output_tokens is not None and self.total_output_tokens >= self.max_output_tokens:
            return False, f"Output token budget ({self.max_output_tokens}) exhausted."
        return True, None

    def can_call_mcp_tool(self, stage_calls_made: int = 0) -> Tuple[bool, Optional[str]]:
        """Check whether task budget allows invoking an MCP tool."""
        if self.mcp_calls_made >= self.max_mcp_calls:
            return False, f"Maximum MCP calls per task ({self.max_mcp_calls}) reached."
        if stage_calls_made >= self.max_mcp_calls_per_stage:
            return False, f"Maximum MCP calls per provider stage ({self.max_mcp_calls_per_stage}) reached."
        if (self.cumulative_mcp_duration_ms / 1000.0) >= self.max_mcp_duration_seconds:
            return False, f"Maximum cumulative MCP duration ({self.max_mcp_duration_seconds:.1f}s) exceeded."
        if self.cumulative_mcp_result_tokens >= self.max_mcp_result_tokens:
            return False, f"MCP result token budget ({self.max_mcp_result_tokens}) exhausted."
        return True, None

    def record_mcp_call(self, duration_ms: float, result_tokens: int) -> None:
        """Record consumption of an MCP tool invocation."""
        self.mcp_calls_made += 1
        self.cumulative_mcp_duration_ms += duration_ms
        self.cumulative_mcp_result_tokens += result_tokens

    def can_attempt_repair(self, current_repair_count: Optional[int] = None) -> Tuple[bool, Optional[str]]:
        """Check whether another repair round is permitted within budget."""
        count = self.repair_rounds_used if current_repair_count is None else current_repair_count
        if count >= self.max_repair_rounds:
            return False, f"Maximum repair rounds ({self.max_repair_rounds}) reached."
        return self.can_call_provider()

    def can_start_step(
        self,
        remaining_steps_count: int,
        requires_final_review: bool = True,
    ) -> Tuple[bool, Optional[str]]:
        """Check whether sufficient budget remains to complete this step and reserve pipeline completion."""
        min_required_calls = 1 + (1 if requires_final_review else 0)
        if self.calls_made + min_required_calls > self.max_provider_calls:
            return (
                False,
                f"Insufficient provider calls remaining ({self.max_provider_calls - self.calls_made} left; "
                f"need at least {min_required_calls} to execute step and reserve final peer review)."
            )
        return self.can_call_provider()


    def record_call(
        self,
        provider_name: str,
        duration_ms: float,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        fusion_context_tokens: Optional[int] = None,
        reasoning_tokens: Optional[int] = None,
        visible_output_tokens: Optional[int] = None,
        cached_tokens: Optional[int] = None,
        raw_usage: Optional[Dict[str, Any]] = None,
        is_premium: bool = False,
        stage: str = "",
    ) -> None:
        """Record provider call telemetry and update budget consumption."""
        self.calls_made += 1
        if is_premium:
            self.premium_calls_made += 1

        overhead = None
        if input_tokens is not None and input_tokens > 0:
            self.total_input_tokens += input_tokens
            self.has_measured_tokens = True
            if fusion_context_tokens is not None:
                overhead = max(0, input_tokens - fusion_context_tokens)
                self.total_provider_managed_overhead += overhead

        if output_tokens is not None and output_tokens > 0:
            self.total_output_tokens += output_tokens
            self.has_measured_tokens = True

        if fusion_context_tokens is not None and fusion_context_tokens > 0:
            self.total_fusion_context_tokens += fusion_context_tokens

        if reasoning_tokens is not None and reasoning_tokens > 0:
            self.total_reasoning_tokens += reasoning_tokens

        if visible_output_tokens is not None and visible_output_tokens > 0:
            self.total_visible_output_tokens += visible_output_tokens

        if cached_tokens is not None and cached_tokens > 0:
            self.total_cached_tokens += cached_tokens

        self.call_history.append(
            StageUsageRecord(
                stage=stage,
                provider_name=provider_name,
                duration_ms=duration_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                fusion_context_tokens=fusion_context_tokens,
                provider_managed_input_overhead=overhead,
                reasoning_tokens=reasoning_tokens,
                visible_output_tokens=visible_output_tokens,
                cached_tokens=cached_tokens,
                raw_usage=raw_usage,
                is_premium=is_premium,
            )
        )

    def record_repair_round(self) -> None:
        """Increment repair round counter."""
        self.repair_rounds_used += 1

    def is_exhausted(self) -> Tuple[bool, Optional[str]]:
        """Check if any hard budget ceiling has been exhausted."""
        allowed, reason = self.can_call_provider()
        return not allowed, reason

    def get_summary(self) -> Dict[str, Any]:
        """Return structured summary of budget consumption."""
        return {
            "calls_made": self.calls_made,
            "max_calls": self.max_provider_calls,
            "repair_rounds_used": self.repair_rounds_used,
            "max_repair_rounds": self.max_repair_rounds,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "max_duration_seconds": self.max_duration_seconds,
            "total_input_tokens": self.total_input_tokens if self.has_measured_tokens else None,
            "total_output_tokens": self.total_output_tokens if self.has_measured_tokens else None,
            "total_fusion_context_tokens": self.total_fusion_context_tokens,
            "total_provider_managed_overhead": self.total_provider_managed_overhead,
            "total_reasoning_tokens": self.total_reasoning_tokens,
            "total_visible_output_tokens": self.total_visible_output_tokens,
            "total_cached_tokens": self.total_cached_tokens,
            "premium_calls_made": self.premium_calls_made,
            "max_premium_calls": self.max_premium_calls,
        }

    get_usage_summary = get_summary
