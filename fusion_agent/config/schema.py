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
    context_max_files: int = 5                      # Maximum candidate files included in CodeContext
    context_max_chars_per_file: int = 8000          # Max chars per file before truncation
    context_total_source_chars: int = 24000         # Total source code char budget across all files
    context_max_test_output_chars: int = 2000       # Max chars allocated to test failure/stderr output
    context_max_peer_feedback_chars: int = 2000     # Max chars allocated to peer critique in repair
    context_max_architecture_chars: int = 1000      # Max chars for architectural decisions
    context_require_minimal_workspace: bool = False # Mode B toggle: construct minimal read-only workspace on disk
    max_context_expansion_rounds: int = 1          # Max bounded retrieval expansions if context is insufficient
    max_expansion_files: int = 2                    # Max additional files retrieved per expansion round
    max_plan_steps: int = 5                         # Maximum steps in an execution plan
    max_plan_amendments: int = 1                    # Maximum dynamic plan amendments permitted
    allow_multi_step_planning: bool = True          # Enable multi-step planning for complex tasks
    plan_critique_required_for_high_risk: bool = True # Critique plan if review risk is HIGH or CRITICAL
    max_mcp_calls_per_task: int = 10                # Hard ceiling on total MCP calls per task
    max_mcp_calls_per_stage: int = 3                # Limit on MCP tool calls per provider stage
    max_tool_turns_per_stage: int = 2               # Maximum tool turns per provider stage
    max_mcp_duration_seconds: float = 60.0          # Cumulative timeout ceiling for MCP calls
    max_mcp_result_tokens: int = 8000               # Total tokens budget for MCP responses
    mcp_max_result_chars_per_call: int = 8000       # Max chars per tool result before truncation

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
    mcp_servers: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["optimization_mode"] = self.optimization_mode.value
        return data

    def to_safe_dict(self) -> Dict[str, Any]:
        """Convert configuration to a dict with all sensitive fields redacted."""
        raw = self.to_dict()
        sensitive_patterns = ("key", "token", "secret", "password", "auth", "credential")

        def _sanitize(obj: Any) -> Any:
            if isinstance(obj, dict):
                clean = {}
                for k, v in obj.items():
                    if any(p in k.lower() for p in sensitive_patterns) and isinstance(v, str) and v:
                        clean[k] = "[REDACTED]"
                    else:
                        clean[k] = _sanitize(v)
                return clean
            elif isinstance(obj, list):
                return [_sanitize(item) for item in obj]
            return obj

        return _sanitize(raw)

    @classmethod
    def default_starter_config(cls, project_name: str = "MyProject") -> "FusionConfig":
        """Generate a production-ready starter configuration detecting installed CLIs."""
        import shutil
        agents = {}
        has_codex = shutil.which("codex") is not None
        has_agy = shutil.which("agy") is not None

        if has_codex or has_agy:
            if has_codex:
                agents["codex"] = AgentConfig(
                    provider_name="OpenAI Codex CLI",
                    provider_type="codex_cli",
                    model="gpt-5.6-sol",
                    extra_params={"reasoning_effort": "medium"},
                )
            if has_agy:
                agents["antigravity"] = AgentConfig(
                    provider_name="Google Antigravity CLI",
                    provider_type="antigravity_cli",
                    model="gemini-3.8-flash-high",
                    extra_params={"effort": "high"},
                )
        else:
            # Fallback to mock providers if neither CLI is found
            agents = {
                "primary": AgentConfig(
                    provider_name="Primary Agent",
                    provider_type="mock",
                    model="mock-reasoning-v1",
                ),
                "secondary": AgentConfig(
                    provider_name="Reviewer Agent",
                    provider_type="mock",
                    model="mock-critic-v1",
                ),
            }

        return cls(
            project_name=project_name,
            project_root=".",
            storage_dir=".fusion",
            optimization_mode=OptimizationMode.BALANCED,
            agents=agents,
            deliberation=DeliberationConfig(
                max_rounds=3,
                max_repair_rounds=2,
                max_provider_calls=8,
                timeout_seconds=180.0,
                max_task_duration_seconds=300.0,
            ),
        )

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
