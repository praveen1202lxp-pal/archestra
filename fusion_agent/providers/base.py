"""Base abstract interface for all Fusion Agent providers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from fusion_agent.models.deliberation import ReviewStatus
from fusion_agent.providers.capabilities import ProviderCapabilities


@dataclass
class HealthCheckResult:
    """Outcome of a provider health check."""
    healthy: bool
    message: str = "OK"
    latency_ms: float = 0.0


@dataclass
class ContextSnapshot:
    """3-tier context snapshot delivered to agent providers with budgeting and instrumentation."""
    permanent_context: str = ""
    current_context: str = ""
    recent_context: str = ""
    peer_context: str = ""
    code_context: Optional[Any] = None
    metrics: Dict[str, int] = field(default_factory=dict)

    def to_prompt_context(self) -> str:
        """Render context snapshot into a structured, bounded markdown prompt block."""
        sections = []
        if self.permanent_context.strip():
            sections.append(f"### PROJECT OVERVIEW & ARCHITECTURE\n{self.permanent_context.strip()}")
        if self.current_context.strip():
            sections.append(f"### CURRENT TASK & RELEVANT STATE\n{self.current_context.strip()}")
        if self.recent_context.strip():
            sections.append(f"### RECENT FINDINGS & DECISIONS\n{self.recent_context.strip()}")
        if self.peer_context.strip():
            sections.append(f"### PEER AGENT INPUT\n{self.peer_context.strip()}")
        if self.code_context is not None:
            if hasattr(self.code_context, "to_prompt_context"):
                sections.append(self.code_context.to_prompt_context())
            else:
                sections.append(str(self.code_context))

        total_text = "\n\n".join(sections)
        self.metrics = {
            "permanent_chars": len(self.permanent_context),
            "current_chars": len(self.current_context),
            "recent_chars": len(self.recent_context),
            "peer_chars": len(self.peer_context),
            "total_chars": len(total_text),
            "estimated_tokens": max(1, len(total_text) // 4),
        }
        return total_text


@dataclass
class AgentResponse:
    """Standardized response from an agent provider."""
    content: str
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: float = 0.0
    raw: Optional[Any] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    reasoning_tokens: Optional[int] = None
    visible_output_tokens: Optional[int] = None
    cached_tokens: Optional[int] = None
    fusion_context_tokens: Optional[int] = None


@dataclass
class ReviewResponse:
    """Standardized response from an agent performing a code/plan review."""
    status: ReviewStatus
    comments: str
    suggested_fixes: List[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    reasoning_tokens: Optional[int] = None
    visible_output_tokens: Optional[int] = None
    cached_tokens: Optional[int] = None
    fusion_context_tokens: Optional[int] = None


@dataclass
class UsageEstimate:
    """Estimated cost or token usage."""
    estimated_cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0


class AgentProvider(ABC):
    """Abstract base class for all AI models, CLIs, and endpoints."""

    def __init__(self, name: str, config: Optional[Dict[str, Any]] = None):
        self.name = name
        self.config = config or {}
        self._is_initialized = False

    @abstractmethod
    def initialize(self) -> bool:
        """Initialize connections, CLI verification, or auth tokens."""
        pass

    @abstractmethod
    def health_check(self) -> HealthCheckResult:
        """Verify provider availability and measure latency."""
        pass

    @abstractmethod
    def get_capabilities(self) -> ProviderCapabilities:
        """Return normalized capabilities supported by this provider."""
        pass

    @abstractmethod
    def invoke(
        self,
        prompt: str,
        context: Optional[Any] = None,
        cwd: Optional[str] = None,
        **kwargs
    ) -> AgentResponse:
        """Invoke the provider with a prompt and context snapshot or CodeContext."""
        pass

    @abstractmethod
    def review(
        self,
        content: str,
        criteria: str,
        context: Optional[Any] = None,
        cwd: Optional[str] = None,
        **kwargs
    ) -> ReviewResponse:
        """Ask the provider to review a proposal, code diff, or plan."""
        pass

    def estimate_cost_or_usage(self, input_tokens: int, output_tokens: int) -> UsageEstimate:
        """Calculate estimated cost based on token usage and capabilities."""
        caps = self.get_capabilities()
        cost = (
            (input_tokens / 1000.0) * caps.estimated_cost_per_1k_input
            + (output_tokens / 1000.0) * caps.estimated_cost_per_1k_output
        )
        return UsageEstimate(
            estimated_cost_usd=cost,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def cancel(self) -> None:
        """Signal cancellation to any ongoing generation/process."""
        pass

    def close(self) -> None:
        """Clean up resources, processes, or network sessions."""
        pass
