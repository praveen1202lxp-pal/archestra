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

        self.start_time = time.perf_counter()
        self.calls_made: int = 0
        self.premium_calls_made: int = 0
        self.repair_rounds_used: int = 0
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
        """Return elapsed duration since budget controller initialization."""
        return time.perf_counter() - self.start_time

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

    def can_attempt_repair(self, current_repair_count: int) -> Tuple[bool, Optional[str]]:
        """Check whether another repair round is permitted within budget."""
        if current_repair_count >= self.max_repair_rounds:
            return False, f"Maximum repair rounds ({self.max_repair_rounds}) reached."
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
