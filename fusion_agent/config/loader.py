"""Configuration loader and saver with environment variable expansion."""

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

from fusion_agent.config.schema import (
    AgentConfig,
    DeliberationConfig,
    FusionConfig,
    OptimizationMode,
)


def _expand_env_vars(data: Any) -> Any:
    """Recursively expand environment variables in string values (e.g. ${API_KEY} or $API_KEY)."""
    if isinstance(data, str):
        # Match ${VAR} or $VAR
        def replace(match):
            var_name = match.group(1) or match.group(2)
            return os.getenv(var_name, match.group(0))
        return re.sub(r"\$\{([A-Za-z0-9_]+)\}|\$([A-Za-z0-9_]+)", replace, data)
    elif isinstance(data, dict):
        return {k: _expand_env_vars(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [_expand_env_vars(v) for v in data]
    return data


class ConfigLoader:
    """Handles discovery, loading, and persistence of Fusion Agent configs."""

    DEFAULT_FILENAME = "config.json"

    @classmethod
    def find_config_file(cls, start_dir: str = ".") -> Optional[Path]:
        """Search current directory and parent directories for .fusion/config.json."""
        current = Path(start_dir).resolve()
        for directory in [current] + list(current.parents):
            candidate = directory / ".fusion" / cls.DEFAULT_FILENAME
            if candidate.is_file():
                return candidate
        return None

    @classmethod
    def load(cls, config_path: str | Path) -> FusionConfig:
        """Load configuration from a JSON file."""
        path = Path(config_path)
        if not path.is_file():
            raise FileNotFoundError(f"Config file not found at: {path}")

        with open(path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)

        expanded_data = _expand_env_vars(raw_data)

        # Parse agents
        agents = {}
        for agent_id, agent_raw in expanded_data.get("agents", {}).items():
            agents[agent_id] = AgentConfig(
                provider_name=agent_raw.get("provider_name", agent_id),
                provider_type=agent_raw.get("provider_type") or agent_raw.get("provider") or "mock",
                model=agent_raw.get("model"),
                base_url=agent_raw.get("base_url"),
                env_api_key=agent_raw.get("env_api_key"),
                extra_params=agent_raw.get("extra_params", {}),
            )

        # Parse deliberation
        delib_raw = expanded_data.get("deliberation", {})
        deliberation = DeliberationConfig(
            max_rounds=delib_raw.get("max_rounds", 3),
            max_repair_rounds=delib_raw.get("max_repair_rounds", 2),
            max_provider_calls=delib_raw.get("max_provider_calls", delib_raw.get("max_model_calls", 8)),
            max_model_calls=delib_raw.get("max_model_calls", delib_raw.get("max_provider_calls", 8)),
            max_feedback_chars=delib_raw.get("max_feedback_chars", 2000),
            timeout_seconds=delib_raw.get("timeout_seconds", 120.0),
            auto_synthesize=delib_raw.get("auto_synthesize", True),
        )

        # Optimization mode
        opt_mode_str = expanded_data.get("optimization_mode", "BALANCED")
        try:
            opt_mode = OptimizationMode(opt_mode_str)
        except ValueError:
            opt_mode = OptimizationMode.BALANCED

        return FusionConfig(
            project_name=expanded_data.get("project_name", "DefaultProject"),
            project_root=expanded_data.get("project_root", str(path.parent.parent)),
            storage_dir=expanded_data.get("storage_dir", ".fusion"),
            optimization_mode=opt_mode,
            agents=agents,
            deliberation=deliberation,
            log_level=expanded_data.get("log_level", "INFO"),
        )

    @classmethod
    def save(cls, config: FusionConfig, target_dir: str | Path) -> Path:
        """Save configuration to target_dir/.fusion/config.json."""
        fusion_dir = Path(target_dir) / config.storage_dir
        fusion_dir.mkdir(parents=True, exist_ok=True)
        config_file = fusion_dir / cls.DEFAULT_FILENAME

        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(config.to_dict(), f, indent=2)

        return config_file
