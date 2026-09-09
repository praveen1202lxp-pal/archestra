"""Usage, token metrics, and cost aggregation."""

from dataclasses import dataclass, field
from typing import Dict


@dataclass
class TaskUsageTracker:
    """Tracks token consumption and cost for a single task."""
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float = 0.0
    calls_by_provider: Dict[str, int] = field(default_factory=dict)

    def record_call(self, provider_name: str, in_tok: int, out_tok: int, cost: float = 0.0):
        self.input_tokens += in_tok
        self.output_tokens += out_tok
        self.estimated_cost_usd += cost
        self.calls_by_provider[provider_name] = self.calls_by_provider.get(provider_name, 0) + 1
