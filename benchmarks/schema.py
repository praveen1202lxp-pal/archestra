"""Schema and data models for Milestone 11 End-to-End Evaluation & Benchmarking."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set


class SystemUnderTest(str, Enum):
    """The system or agent architecture being evaluated."""
    FUSION = "fusion"
    CODEX_ALONE = "codex_alone"
    ANTIGRAVITY_ALONE = "antigravity_alone"


class BenchmarkCategory(str, Enum):
    """Engineering task category."""
    SINGLE_FILE_BUG_FIX = "single_file_bug_fix"
    FAILING_TEST_REPAIR = "failing_test_repair"
    SMALL_FEATURE_ADDITION = "small_feature_addition"
    MULTI_FILE_FEATURE = "multi_file_feature"
    API_INTERFACE_CHANGE = "api_interface_change"
    REFACTOR = "refactor"
    EDGE_CASE_BUG = "edge_case_bug"
    TEST_GENERATION = "test_generation"
    ARCHITECTURE_DESIGN = "architecture_design"
    MULTI_STEP_DEPENDENT = "multi_step_dependent"
    RESOURCE_LIFECYCLE = "resource_lifecycle"
    MCP_ISSUE_DRIVEN = "mcp_issue_driven"


class BenchmarkScore(str, Enum):
    """Objective task outcome score."""
    PASS = "PASS"
    PARTIAL = "PARTIAL"
    FAIL = "FAIL"


@dataclass
class BenchmarkTask:
    """Specification of an engineering task in the benchmark catalog.
    
    Adheres strictly to the SUT-independent scope oracle: required_paths,
    allowed_paths, and forbidden_paths are defined independently of any model.
    """
    task_id: str
    category: BenchmarkCategory
    title: str
    prompt: str
    
    # Visible vs Hidden evaluation separation
    visible_preexisting_tests: List[str] = field(default_factory=list)
    visible_reproduction_tests: List[str] = field(default_factory=list)
    visible_test_command: str = ""
    hidden_evaluator_module: str = ""
    full_regression_command: Optional[str] = None
    
    # SUT-Independent Scope Criteria
    required_paths: List[str] = field(default_factory=list)
    allowed_paths: List[str] = field(default_factory=list)
    forbidden_paths: List[str] = field(default_factory=list)
    
    # Pre-registered defect/acceptance criteria for objective cross-model review scoring
    registered_defect_criteria: List[str] = field(default_factory=list)
    
    timeout_seconds: float = 300.0
    mcp_context: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["category"] = self.category.value
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BenchmarkTask":
        d = dict(data)
        if isinstance(d.get("category"), str):
            d["category"] = BenchmarkCategory(d["category"])
        return cls(**d)


@dataclass
class ScoringResult:
    """Detailed outcome of deterministic acceptance & regression evaluation."""
    score: BenchmarkScore
    task_tests_passed: bool
    hidden_tests_passed: bool
    regressions_count: int
    regressions_passed: bool
    syntax_valid: bool
    files_touched: List[str] = field(default_factory=list)
    scope_violated: bool = False
    unintended_files: List[str] = field(default_factory=list)
    verification_exit_code: int = 0
    verification_output: str = ""
    failure_reasons: List[str] = field(default_factory=list)
    pre_review_criteria_failed: List[str] = field(default_factory=list)
    reviewer_mapped_defect_criteria: List[str] = field(default_factory=list)
    reviewer_found_valid_defect: bool = False

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["score"] = self.score.value
        return data


@dataclass
class BenchmarkRunRecord:
    """Persistent execution record with complete provenance and telemetry."""
    # Provenance & Suite Versioning
    run_id: str
    benchmark_suite_version: str = "1.0.0"
    benchmark_suite_hash: str = ""
    task_definition_hash: str = ""
    hidden_evaluator_hash: str = ""
    baseline_snapshot_hash: str = ""
    task_id: str = ""
    category: str = ""
    system_under_test: SystemUnderTest = SystemUnderTest.FUSION
    repetition_index: int = 0
    
    # Environment & Timestamps
    start_time: str = ""
    end_time: str = ""
    wall_clock_duration_seconds: float = 0.0
    active_provider_duration_seconds: float = 0.0
    os_platform: str = ""
    python_version: str = ""
    provider_model_id: Optional[str] = None
    cli_version: Optional[str] = None
    reasoning_effort: Optional[str] = None
    fusion_config_hash: Optional[str] = None
    
    # Outcome & Scoring
    score: BenchmarkScore = BenchmarkScore.FAIL
    verification_passed: bool = False
    hidden_tests_passed: bool = False
    regressions_count: int = 0
    files_touched: List[str] = field(default_factory=list)
    unintended_files: List[str] = field(default_factory=list)
    git_diff: str = ""
    
    # Objective Cross-Model Review Value (Fusion)
    reviewer_verdict: Optional[str] = None
    reviewer_found_defect: bool = False
    reviewer_found_valid_defect: bool = False
    pre_review_criteria_failed: List[str] = field(default_factory=list)
    reviewer_mapped_defect_criteria: List[str] = field(default_factory=list)
    defect_in_test_passing_patch: bool = False
    repair_rounds: int = 0
    repair_successful: bool = False
    human_promotion_disposition: Optional[str] = None
    
    # Token Telemetry (Separately tracked)
    fusion_controlled_context_tokens: Optional[int] = None
    native_input_tokens: int = 0
    native_output_tokens: int = 0
    native_reasoning_tokens: Optional[int] = None
    provider_managed_overhead_residual: Optional[int] = None
    provider_calls_count: int = 0
    mcp_calls_count: int = 0
    
    # Operational Signals
    recovery_events: int = 0
    policy_denials: int = 0
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["system_under_test"] = self.system_under_test.value
        data["score"] = self.score.value
        return data
