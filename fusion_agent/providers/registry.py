"""Provider registry and factory."""

from typing import Callable, Dict, Optional, Type

from fusion_agent.providers.base import AgentProvider
from fusion_agent.providers.mock import MockProvider

ProviderFactory = Callable[[str, Dict], AgentProvider]

class ProviderRegistry:
    """Registry of available agent provider factories."""

    _registry: Dict[str, ProviderFactory] = {}

    @classmethod
    def register(cls, provider_type: str, factory: ProviderFactory) -> None:
        """Register a provider factory function for a provider type."""
        cls._registry[provider_type.lower()] = factory

    @classmethod
    def get(cls, provider_type: str) -> Optional[ProviderFactory]:
        """Get factory for a given provider type."""
        return cls._registry.get(provider_type.lower())

    @classmethod
    def create(cls, name: str, provider_type: str, config: Optional[Dict] = None) -> AgentProvider:
        """Instantiate a provider by type."""
        factory = cls.get(provider_type)
        if not factory:
            available = ", ".join(cls._registry.keys())
            raise ValueError(
                f"Unknown provider type '{provider_type}'. Available providers: [{available}]"
            )
        return factory(name, config or {})

    @classmethod
    def list_available(cls) -> list[str]:
        """List all registered provider types."""
        return list(cls._registry.keys())


# Default registrations
ProviderRegistry.register(
    "mock",
    lambda name, cfg: MockProvider(name=name, config=cfg)
)

from fusion_agent.providers.gemini_cli import GeminiCLIProvider
from fusion_agent.providers.antigravity_cli import AntigravityCLIProvider
from fusion_agent.providers.codex_cli import CodexCLIProvider

ProviderRegistry.register(
    "gemini_cli",
    lambda name, cfg: GeminiCLIProvider(name=name, config=cfg)
)

ProviderRegistry.register(
    "antigravity_cli",
    lambda name, cfg: AntigravityCLIProvider(name=name, config=cfg)
)

ProviderRegistry.register(
    "codex_cli",
    lambda name, cfg: CodexCLIProvider(name=name, config=cfg)
)


