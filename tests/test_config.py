"""Tests for configuration schemas and loader."""

import os
from pathlib import Path

from fusion_agent.config.loader import ConfigLoader
from fusion_agent.config.schema import (
    AgentConfig,
    DeliberationConfig,
    FusionConfig,
    OptimizationMode,
)


def test_default_mock_config():
    cfg = FusionConfig.default_mock_config("TestProj")
    assert cfg.project_name == "TestProj"
    assert cfg.optimization_mode == OptimizationMode.BALANCED
    assert "primary" in cfg.agents
    assert "secondary" in cfg.agents
    assert cfg.agents["primary"].provider_type == "mock"


def test_save_and_load_config(tmp_path: Path):
    cfg = FusionConfig(
        project_name="SavedProject",
        project_root=str(tmp_path),
        storage_dir=".fusion",
        optimization_mode=OptimizationMode.BEST_QUALITY,
        agents={
            "agent_1": AgentConfig(
                provider_name="Primary Agent",
                provider_type="mock",
                model="test-model",
            )
        },
        deliberation=DeliberationConfig(max_rounds=4),
    )

    saved_file = ConfigLoader.save(cfg, tmp_path)
    assert saved_file.exists()

    loaded_cfg = ConfigLoader.load(saved_file)
    assert loaded_cfg.project_name == "SavedProject"
    assert loaded_cfg.optimization_mode == OptimizationMode.BEST_QUALITY
    assert loaded_cfg.deliberation.max_rounds == 4
    assert "agent_1" in loaded_cfg.agents
    assert loaded_cfg.agents["agent_1"].model == "test-model"


def test_env_var_expansion(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "secret_key_12345")
    cfg = FusionConfig(
        project_name="EnvProject",
        agents={
            "api_agent": AgentConfig(
                provider_name="API Agent",
                provider_type="mock",
                env_api_key="${TEST_API_KEY}",
            )
        },
    )
    saved_file = ConfigLoader.save(cfg, tmp_path)
    loaded_cfg = ConfigLoader.load(saved_file)
    assert loaded_cfg.agents["api_agent"].env_api_key == "secret_key_12345"
