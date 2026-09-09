"""Configuration schemas for Fusion Agent."""

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class OptimizationMode(str, Enum):
    """Execution and deliberation optimization preferences."""
    BEST_QUALITY = "BEST_QUALITY"
    BALANCED = "BALANCED"
    LOWEST_COST = "LOWEST_COST"
    FASTEST = "FASTEST"
    LOCAL_PRIVATE = "LOCAL_PRIVATE"


@dataclass
class AgentConfig:
    """Configuration for a single agent provider."""
    provider_name: str
    provider_type: str  # e.g., "mock", "gemini_cli", "gemini_api", "openai_api", "ollama"
    model: Optional[str] = None
    base_url: Optional[str] = None
    env_api_key: Optional[str] = None
    extra_params: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DeliberationConfig:
    """Limits and guardrails for multi-agent deliberation, budget control, and review repair loops."""
    max_rounds: int = 3
    max_repair_rounds: int = 2          # Maximum iterations of patch-review-repair
    max_provider_calls: int = 8         # Hard ceiling on total provider calls per task
    max_model_calls: int = 8            # Backward compatibility alias
    max_feedback_chars: int = 2000      # Bounds reviewer feedback passed into repair prompt
    timeout_seconds: float = 120.0      # Per-stage timeout ceiling
    auto_synthesize: bool = True
    max_total_input_tokens: Optional[int] = None    # Optional ceiling on native input tokens
    max_total_output_tokens: Optional[int] = None   # Optional ceiling on native output tokens
    max_task_duration_seconds: float = 300.0        # End-to-end task duration ceiling
    max_premium_provider_calls: int = 4             # Ceiling on calls to paid/premium providers
    skip_peer_review_for_low_risk: bool = True      # Skip second-model review if review risk is LOW

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FusionConfig:
    """Master configuration for Fusion Agent."""
    project_name: str = "DefaultProject"
    project_root: str = "."
    storage_dir: str = ".fusion"
    optimization_mode: OptimizationMode = OptimizationMode.BALANCED
    agents: Dict[str, AgentConfig] = field(default_factory=dict)
    deliberation: DeliberationConfig = field(default_factory=DeliberationConfig)
    verification_command: Optional[str] = None
    log_level: str = "INFO"

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["optimization_mode"] = self.optimization_mode.value
        return data

    @classmethod
    def default_mock_config(cls, project_name: str = "DemoProject") -> "FusionConfig":
        """Generate a default configuration using mock providers for testing."""
        return cls(
            project_name=project_name,
            project_root=".",
            storage_dir=".fusion",
            optimization_mode=OptimizationMode.BALANCED,
            agents={
                "primary": AgentConfig(
                    provider_name="Primary Mock Agent",
                    provider_type="mock",
                    model="mock-reasoning-v1",
                ),
                "secondary": AgentConfig(
                    provider_name="Secondary Mock Agent",
                    provider_type="mock",
                    model="mock-critic-v1",
                ),
            },
            deliberation=DeliberationConfig(
                max_rounds=2,
                max_model_calls=6,
            ),
        )
