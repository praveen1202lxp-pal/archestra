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
    """Limits and guardrails for multi-agent deliberation."""
    max_rounds: int = 3
    max_model_calls: int = 8
    timeout_seconds: float = 60.0
    auto_synthesize: bool = True

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
