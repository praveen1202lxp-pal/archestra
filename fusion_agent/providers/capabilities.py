"""Normalized capabilities model for AI agent providers."""

from dataclasses import dataclass, field
from typing import List


@dataclass
class ProviderCapabilities:
    """Normalized capabilities advertised by an agent provider."""
    # Cognitive / Output capabilities
    reasoning: bool = True
    structured_output: bool = True
    streaming: bool = False
    
    # Environment & Tool capabilities
    tool_calling: bool = False
    repository_read: bool = False
    repository_write: bool = False
    shell: bool = False
    git: bool = False
    
    # Architecture & Deployment
    local: bool = False
    is_cli: bool = False  # e.g., Codex CLI, Gemini CLI with native repo access
    
    # Operational metrics
    context_window: int = 128_000
    estimated_cost_per_1k_input: float = 0.0
    estimated_cost_per_1k_output: float = 0.0
    
    # Task preferences
    preferred_task_types: List[str] = field(default_factory=list)

    def can_handle_repository_ops(self) -> bool:
        """Check if provider can read and modify files natively."""
        return self.repository_read and self.repository_write

    def is_free_or_local(self) -> bool:
        """Check if provider incurs no marginal API cost."""
        return self.local or (self.estimated_cost_per_1k_input == 0.0 and self.estimated_cost_per_1k_output == 0.0)
