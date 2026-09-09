"""Normalized capabilities model for AI agent providers."""

from dataclasses import dataclass, field
from enum import Enum
from typing import List


class CostTier(str, Enum):
    """Marginal cost classification for provider billing."""
    FREE = "FREE"                      # Fully free / local model
    SUBSCRIPTION = "SUBSCRIPTION"      # Flat-rate subscription (e.g. ChatGPT Plus/Pro, Antigravity CLI)
    PAID_API = "PAID_API"              # Pay-per-token API usage


class CapabilityStrength(str, Enum):
    """Normalized strength tiers for explainable capability scoring."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    EXPERT = "EXPERT"

    @property
    def numeric_score(self) -> float:
        """Convert strength tier to normalized float score [0.25 .. 1.0]."""
        mapping = {
            CapabilityStrength.LOW: 0.25,
            CapabilityStrength.MEDIUM: 0.50,
            CapabilityStrength.HIGH: 0.75,
            CapabilityStrength.EXPERT: 1.00,
        }
        return mapping.get(self, 0.50)


@dataclass
class ProviderCapabilities:
    """Normalized capabilities advertised by an agent provider."""
    # Cognitive / Output capabilities
    reasoning: bool = True
    structured_output: bool = True
    streaming: bool = False
    
    # Normalized strength dimensions
    reasoning_strength: CapabilityStrength = CapabilityStrength.HIGH
    coding_strength: CapabilityStrength = CapabilityStrength.HIGH
    review_strength: CapabilityStrength = CapabilityStrength.HIGH
    
    # Environment & Tool capabilities
    tool_calling: bool = False
    repository_read: bool = False
    repository_write: bool = False
    shell: bool = False
    git: bool = False
    
    # Architecture & Deployment
    local: bool = False
    is_cli: bool = False  # e.g., Codex CLI, Gemini CLI with native repo access
    cost_tier: CostTier = CostTier.SUBSCRIPTION
    
    # Operational metrics
    context_window: int = 128_000
    typical_latency_ms: float = 25_000.0
    native_token_baseline: int = 15_000
    estimated_cost_per_1k_input: float = 0.0
    estimated_cost_per_1k_output: float = 0.0
    
    # Task preferences
    preferred_task_types: List[str] = field(default_factory=list)

    def can_handle_repository_ops(self) -> bool:
        """Check if provider can read and modify files natively."""
        return self.repository_read and self.repository_write

    def is_free_or_local(self) -> bool:
        """Check if provider incurs no marginal per-token API cost."""
        if self.estimated_cost_per_1k_input > 0.0 or self.estimated_cost_per_1k_output > 0.0:
            return False
        return self.local or self.cost_tier in (CostTier.FREE, CostTier.SUBSCRIPTION)
