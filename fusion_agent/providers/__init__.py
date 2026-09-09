"""Providers package for Fusion Agent."""

from fusion_agent.providers.base import (
    AgentProvider,
    AgentResponse,
    ContextSnapshot,
    HealthCheckResult,
    ReviewResponse,
    UsageEstimate,
)
from fusion_agent.providers.capabilities import ProviderCapabilities
from fusion_agent.providers.gemini_cli import GeminiCLIProvider
from fusion_agent.providers.antigravity_cli import AntigravityCLIProvider
from fusion_agent.providers.codex_cli import CodexCLIProvider
from fusion_agent.providers.normalizer import StructuredAgentOutput, StructuredOutputNormalizer
from fusion_agent.providers.mock import MockProvider
from fusion_agent.providers.registry import ProviderRegistry

__all__ = [
    "AgentProvider",
    "AgentResponse",
    "ContextSnapshot",
    "HealthCheckResult",
    "ReviewResponse",
    "UsageEstimate",
    "ProviderCapabilities",
    "MockProvider",
    "GeminiCLIProvider",
    "AntigravityCLIProvider",
    "CodexCLIProvider",
    "StructuredAgentOutput",
    "StructuredOutputNormalizer",
    "ProviderRegistry",
]
