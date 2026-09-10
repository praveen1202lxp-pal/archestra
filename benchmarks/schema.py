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
    INFRASTRUCTURE_FAILURE = "INFRASTRUCTURE_FAILURE"


class RunExecutionStatus(str, Enum):
    """SUT execution process status separated from patch correctness."""
    COMPLETED = "COMPLETED"
    TIMEOUT = "TIMEOUT"
    INFRASTRUCTURE_FAILURE = "INFRASTRUCTURE_FAILURE"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    AUTH_FAILURE = "AUTH_FAILURE"
    HARNESS_FAILURE = "HARNESS_FAILURE"


class ValidityDisposition(str, Enum):
    """Benchmark run validity disposition ensuring auditability without silent mixing."""
    VALID = "VALID"
    INVALIDATED_METHODOLOGY = "INVALIDATED_METHODOLOGY"
    INVALIDATED_INFRASTRUCTURE = "INVALIDATED_INFRASTRUCTURE"


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

    # Test Modification Policy (Phase B.1)
    protected_paths: List[str] = field(default_factory=list)
    allowed_new_test_paths: List[str] = field(default_factory=list)
    allowed_source_paths: List[str] = field(default_factory=list)
    
    # Pre-registered defect/acceptance criteria for objective cross-model review scoring
    registered_defect_criteria: List[str] = field(default_factory=list)
    
    timeout_seconds: float = 300.0
    mcp_context: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["category"] = self.category.value
        return data


@dataclass
class SyntaxValidationResult:
    """Result of syntax/parse check on modified files."""
    valid: bool
    invalid_files: List[str] = field(default_factory=list)
    error_messages: Dict[str, str] = field(default_factory=dict)


@dataclass
class ScopeOracleResult:
    """Result of evaluating modified files against SUT-independent path constraints."""
    passed: bool
    required_missing: List[str] = field(default_factory=list)
    unintended_files: List[str] = field(default_factory=list)
    forbidden_modified: List[str] = field(default_factory=list)
    protected_test_mutations: List[str] = field(default_factory=list)


@dataclass
class ScoringResult:
    """Detailed outcome of deterministic acceptance & regression evaluation."""
    score: BenchmarkScore
    task_tests_passed: bool
    hidden_tests_passed: bool
    regressions_count: int = 0
    regressions_passed: bool = True
    syntax_valid: bool = True
    scope_valid: bool = True
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


TaskScoringResult = ScoringResult


@dataclass
class BenchmarkRunRecord:
    """Persistent execution record with complete provenance, telemetry, and validity audit trail."""
    # Provenance & Suite Versioning
    run_id: str
    benchmark_suite_version: str = "1.1.0"
    benchmark_suite_hash: str = ""
    task_definition_hash: str = ""
    hidden_evaluator_hash: str = ""
    baseline_snapshot_hash: str = ""
    task_id: str = ""
    category: str = ""
    system_under_test: SystemUnderTest = SystemUnderTest.FUSION
    repetition_index: int = 0
    
    # Execution Status & Validity Audit Trail (Phase B.1)
    run_status: RunExecutionStatus = RunExecutionStatus.COMPLETED
    validity_disposition: str = ValidityDisposition.VALID.value
    invalidation_reasons: List[str] = field(default_factory=list)
    execution_mode: str = "MOCK"  # "MOCK" | "LIVE"
    experiment_phase: str = "PHASE_B_PILOT"

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
    sut_tree_hash: Optional[str] = None

    # Candidate Snapshot Fields (Phase B.1)
    sut_candidate_tree_hash: Optional[str] = None
    sut_git_diff: Optional[str] = None
    sut_touched_files: List[str] = field(default_factory=list)

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
    pre_review_patch: Optional[str] = None
    pre_review_test_passed: Optional[bool] = None
    reviewer_findings: Optional[str] = None
    repair_patch: Optional[str] = None
    
    # Token Telemetry (Separately tracked)
    fusion_controlled_context_tokens: Optional[int] = None
    native_input_tokens: Optional[int] = None
    native_output_tokens: Optional[int] = None
    native_reasoning_tokens: Optional[int] = None
    provider_managed_overhead_residual: Optional[int] = None
    provider_calls_count: int = 0
    provider_stages: List[str] = field(default_factory=list)
    verification_duration_seconds: Optional[float] = None
    mcp_calls_count: int = 0
    
    # Operational Signals
    recovery_events: int = 0
    policy_denials: int = 0
    error_message: Optional[str] = None

    def __post_init__(self):
        # Synchronize candidate aliases
        if self.sut_candidate_tree_hash is None and self.sut_tree_hash is not None:
            self.sut_candidate_tree_hash = self.sut_tree_hash
        elif self.sut_tree_hash is None and self.sut_candidate_tree_hash is not None:
            self.sut_tree_hash = self.sut_candidate_tree_hash

        if self.sut_git_diff is None and self.git_diff:
            self.sut_git_diff = self.git_diff
        elif not self.git_diff and self.sut_git_diff:
            self.git_diff = self.sut_git_diff

        if not self.sut_touched_files and self.files_touched:
            self.sut_touched_files = list(self.files_touched)
        elif not self.files_touched and self.sut_touched_files:
            self.files_touched = list(self.sut_touched_files)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["system_under_test"] = self.system_under_test.value
        data["score"] = self.score.value
        data["run_status"] = self.run_status.value if isinstance(self.run_status, RunExecutionStatus) else str(self.run_status)
        return data
