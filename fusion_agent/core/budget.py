"""Task budget controller and guardrail enforcer."""

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from fusion_agent.config.schema import DeliberationConfig


@dataclass
class StageUsageRecord:
    """Record of usage for a single provider call."""
    stage: str
    provider_name: str
    duration_ms: float
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    is_premium: bool = False


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

        if self.elapsed_seconds >= self.max_duration_seconds:
            return False, f"Maximum task duration ({self.max_duration_seconds:.1f}s) exceeded."

        if is_premium and self.premium_calls_made >= self.max_premium_calls:
            return False, f"Maximum premium provider calls ({self.max_premium_calls}) reached."

        if self.max_input_tokens is not None and self.total_input_tokens >= self.max_input_tokens:
            return False, f"Input token budget ({self.max_input_tokens}) exhausted."

        if self.max_output_tokens is not None and self.total_output_tokens >= self.max_output_tokens:
            return False, f"Output token budget ({self.max_output_tokens}) exhausted."

        return True, None

    def can_attempt_repair(self, current_round: int) -> Tuple[bool, Optional[str]]:
        """Check whether another repair round is permitted."""
        if current_round >= self.max_repair_rounds:
            return False, f"Maximum repair rounds ({self.max_repair_rounds}) reached."

        return self.can_call_provider()

    def record_call(
        self,
        provider_name: str,
        duration_ms: float,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        is_premium: bool = False,
        stage: str = "",
    ) -> None:
        """Record provider call telemetry and update budget consumption."""
        self.calls_made += 1
        if is_premium:
            self.premium_calls_made += 1

        if input_tokens is not None and input_tokens > 0:
            self.total_input_tokens += input_tokens
            self.has_measured_tokens = True

        if output_tokens is not None and output_tokens > 0:
            self.total_output_tokens += output_tokens
            self.has_measured_tokens = True

        self.call_history.append(
            StageUsageRecord(
                stage=stage,
                provider_name=provider_name,
                duration_ms=duration_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
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
            "premium_calls_made": self.premium_calls_made,
            "max_premium_calls": self.max_premium_calls,
        }
