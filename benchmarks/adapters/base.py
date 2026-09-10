"""Base adapter interface and telemetry models for Systems Under Test."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from benchmarks.schema import BenchmarkTask, SystemUnderTest


@dataclass
class AdapterRunTelemetry:
    """Execution telemetry captured directly from SUT execution."""
    system_under_test: SystemUnderTest
    wall_clock_duration_seconds: float = 0.0
    active_provider_duration_seconds: float = 0.0
    
    # Token Telemetry
    native_input_tokens: int = 0
    native_output_tokens: int = 0
    native_reasoning_tokens: Optional[int] = None
    fusion_controlled_context_tokens: Optional[int] = None
    provider_calls_count: int = 0
    mcp_calls_count: int = 0
    
    # Provenance
    provider_model_id: Optional[str] = None
    cli_version: Optional[str] = None
    reasoning_effort: Optional[str] = None
    fusion_config_hash: Optional[str] = None
    
    # Review & Defect Signals (Fusion)
    reviewer_verdict: Optional[str] = None
    reviewer_found_defect: bool = False
    defect_in_test_passing_patch: bool = False
    repair_rounds: int = 0
    repair_successful: bool = False
    human_promotion_disposition: Optional[str] = None
    
    # Operational Signals
    recovery_events: int = 0
    policy_denials: int = 0
    error_message: Optional[str] = None


class BaseSUTAdapter(ABC):
    """Abstract adapter connecting the benchmark harness to a System Under Test."""

    def __init__(self, sut: SystemUnderTest):
        self.sut = sut

    @abstractmethod
    def execute(self, task: BenchmarkTask, repo_path: Path) -> AdapterRunTelemetry:
        """Run the task inside repo_path and return captured telemetry.
        
        The adapter must modify repo_path in-place (if it produces code changes)
        and return execution metrics.
        """
        pass
