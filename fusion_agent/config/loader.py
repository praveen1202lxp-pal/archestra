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
    def get_user_config_path(cls) -> Path:
        """Return the path to user-global configuration (~/.fusion/config.json)."""
        return Path.home() / ".fusion" / cls.DEFAULT_FILENAME

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
    def _deep_merge(cls, base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        """Recursively merge override dictionary into base dictionary."""
        result = dict(base)
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = cls._deep_merge(result[key], value)
            else:
                result[key] = value
        return result

    @classmethod
    def parse_dict(cls, raw_data: Dict[str, Any], default_root: Optional[str] = None) -> FusionConfig:
        """Parse a dictionary into a FusionConfig instance with env expansion."""
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
            max_total_input_tokens=delib_raw.get("max_total_input_tokens"),
            max_total_output_tokens=delib_raw.get("max_total_output_tokens"),
            max_task_duration_seconds=delib_raw.get("max_task_duration_seconds", 300.0),
            max_premium_provider_calls=delib_raw.get("max_premium_provider_calls", 4),
            skip_peer_review_for_low_risk=delib_raw.get("skip_peer_review_for_low_risk", True),
        )

        # Optimization mode
        opt_mode_str = expanded_data.get("optimization_mode", "BALANCED")
        try:
            opt_mode = OptimizationMode(opt_mode_str)
        except ValueError:
            opt_mode = OptimizationMode.BALANCED

        return FusionConfig(
            project_name=expanded_data.get("project_name", "DefaultProject"),
            project_root=expanded_data.get("project_root", default_root or "."),
            storage_dir=expanded_data.get("storage_dir", ".fusion"),
            optimization_mode=opt_mode,
            agents=agents,
            deliberation=deliberation,
            verification_command=expanded_data.get("verification_command"),
            log_level=expanded_data.get("log_level", "INFO"),
            mcp_servers=expanded_data.get("mcp_servers", {}),
        )

    @classmethod
    def load(cls, config_path: str | Path) -> FusionConfig:
        """Load configuration from a specific JSON file."""
        path = Path(config_path)
        if not path.is_file():
            raise FileNotFoundError(f"Config file not found at: {path}")

        with open(path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)

        return cls.parse_dict(raw_data, default_root=str(path.parent.parent))

    @classmethod
    def load_hierarchical(
        cls,
        project_dir: str | Path = ".",
        cli_overrides: Optional[Dict[str, Any]] = None,
    ) -> FusionConfig:
        """Load configuration following strict precedence:
        CLI argument > project config (.fusion/config.json) > user config (~/.fusion/config.json) > environment variables > built-in defaults.
        """
        proj_root = Path(project_dir).resolve()

        # 1. Built-in base defaults
        base_cfg = FusionConfig.default_starter_config(project_name=proj_root.name)
        base_data = base_cfg.to_dict()
        base_data["project_root"] = str(proj_root)

        # 2. Environment variables (FUSION_*) - override built-in defaults
        env_overrides = {}
        if os.getenv("FUSION_OPTIMIZATION_MODE"):
            env_overrides["optimization_mode"] = os.getenv("FUSION_OPTIMIZATION_MODE")
        if os.getenv("FUSION_LOG_LEVEL"):
            env_overrides["log_level"] = os.getenv("FUSION_LOG_LEVEL")
        if os.getenv("FUSION_STORAGE_DIR"):
            env_overrides["storage_dir"] = os.getenv("FUSION_STORAGE_DIR")
        if os.getenv("FUSION_VERIFICATION_COMMAND"):
            env_overrides["verification_command"] = os.getenv("FUSION_VERIFICATION_COMMAND")
        if os.getenv("FUSION_PROJECT_NAME"):
            env_overrides["project_name"] = os.getenv("FUSION_PROJECT_NAME")

        if env_overrides:
            base_data = cls._deep_merge(base_data, env_overrides)

        # 3. User-global config (~/.fusion/config.json) - overrides env variables
        try:
            user_cfg_path = Path(cls.get_user_config_path())
            if user_cfg_path.is_file():
                with open(user_cfg_path, "r", encoding="utf-8") as f:
                    user_data = json.load(f)
                base_data = cls._deep_merge(base_data, user_data)
        except Exception:
            pass

        # 4. Project configuration (.fusion/config.json) - overrides user config
        try:
            project_cfg_path = cls.find_config_file(proj_root)
            if project_cfg_path:
                p = Path(project_cfg_path)
                if p.is_file():
                    with open(p, "r", encoding="utf-8") as f:
                        proj_data = json.load(f)
                    base_data = cls._deep_merge(base_data, proj_data)
        except Exception:
            pass

        # 5. Environment variable overrides for provider paths (take priority over file configs if set)
        if os.getenv("CODEX_CLI_PATH"):
            if "agents" not in base_data:
                base_data["agents"] = {}
            if "codex" not in base_data["agents"]:
                base_data["agents"]["codex"] = {"provider_type": "codex_cli", "provider_name": "Codex CLI", "extra_params": {}}
            base_data["agents"]["codex"].setdefault("extra_params", {})["executable_path"] = os.getenv("CODEX_CLI_PATH")

        if os.getenv("ANTIGRAVITY_CLI_PATH"):
            if "agents" not in base_data:
                base_data["agents"] = {}
            if "antigravity" not in base_data["agents"]:
                base_data["agents"]["antigravity"] = {"provider_type": "antigravity_cli", "provider_name": "Antigravity CLI", "extra_params": {}}
            base_data["agents"]["antigravity"].setdefault("extra_params", {})["executable_path"] = os.getenv("ANTIGRAVITY_CLI_PATH")

        # 6. CLI runtime overrides
        if cli_overrides:
            clean_cli = {k: v for k, v in cli_overrides.items() if v is not None}
            base_data = cls._deep_merge(base_data, clean_cli)

        return cls.parse_dict(base_data, default_root=str(proj_root))

    @classmethod
    def save(cls, config: FusionConfig, target_dir: str | Path) -> Path:
        """Save configuration to target_dir/.fusion/config.json."""
        fusion_dir = Path(target_dir) / config.storage_dir
        fusion_dir.mkdir(parents=True, exist_ok=True)
        config_file = fusion_dir / cls.DEFAULT_FILENAME

        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(config.to_dict(), f, indent=2)

        return config_file
