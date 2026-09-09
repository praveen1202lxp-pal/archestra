"""Tests for ProviderCapabilities."""

from fusion_agent.providers.capabilities import ProviderCapabilities


def test_capabilities_defaults():
    caps = ProviderCapabilities()
    assert caps.reasoning is True
    assert caps.structured_output is True
    assert caps.streaming is False
    assert caps.tool_calling is False
    assert caps.repository_read is False
    assert caps.repository_write is False
    assert caps.is_cli is False
    assert caps.context_window == 128_000


def test_can_handle_repository_ops():
    caps_no_repo = ProviderCapabilities(repository_read=True, repository_write=False)
    assert caps_no_repo.can_handle_repository_ops() is False

    caps_repo = ProviderCapabilities(repository_read=True, repository_write=True)
    assert caps_repo.can_handle_repository_ops() is True


def test_is_free_or_local():
    cloud_paid = ProviderCapabilities(
        local=False,
        estimated_cost_per_1k_input=0.005,
        estimated_cost_per_1k_output=0.015,
    )
    assert cloud_paid.is_free_or_local() is False

    local_model = ProviderCapabilities(local=True)
    assert local_model.is_free_or_local() is True

    free_cloud = ProviderCapabilities(local=False, estimated_cost_per_1k_input=0.0, estimated_cost_per_1k_output=0.0)
    assert free_cloud.is_free_or_local() is True
