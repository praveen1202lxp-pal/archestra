"""Tests for MockProvider and ProviderRegistry."""

import pytest

from fusion_agent.models.deliberation import ReviewStatus
from fusion_agent.providers.base import ContextSnapshot
from fusion_agent.providers.capabilities import ProviderCapabilities
from fusion_agent.providers.mock import MockProvider
from fusion_agent.providers.registry import ProviderRegistry


def test_mock_provider_invoke():
    prov = MockProvider(name="test_mock", default_response="Custom mock output")
    prov.initialize()

    health = prov.health_check()
    assert health.healthy is True

    context = ContextSnapshot(permanent_context="Test Project", current_context="Test Task")
    response = prov.invoke("Analyze deadlock", context=context)

    assert "Custom mock output" in response.content
    assert response.input_tokens > 0
    assert response.output_tokens > 0
    assert len(prov.invocations) == 1


def test_mock_provider_review():
    prov = MockProvider(
        name="test_reviewer",
        default_review_status=ReviewStatus.NEEDS_REVISION,
        default_review_comments="Missing null check on buffer.",
    )
    rev = prov.review("Code diff here", criteria="Check security")

    assert rev.status == ReviewStatus.NEEDS_REVISION
    assert "Missing null check" in rev.comments
    assert len(prov.reviews) == 1


def test_mock_provider_failure_modes():
    failing_prov = MockProvider(should_fail_health=True, should_fail_invoke=True)
    assert failing_prov.health_check().healthy is False

    with pytest.raises(RuntimeError):
        failing_prov.invoke("This will fail")


def test_provider_registry():
    registered = ProviderRegistry.list_available()
    assert "mock" in registered

    prov = ProviderRegistry.create("mock_1", "mock", {"some": "config"})
    assert isinstance(prov, MockProvider)
    assert prov.name == "mock_1"

    with pytest.raises(ValueError):
        ProviderRegistry.create("unknown_agent", "non_existent_provider")
