"""Domain models for checkpointed multi-step task execution plans."""

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from fusion_agent.models.assessment import ReviewRisk
from fusion_agent.models.task import Complexity, PromotionDisposition


class StepStatus(str, Enum):
    """Lifecycle status of a single step in an ExecutionPlan."""
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class PlanStatus(str, Enum):
    """Lifecycle status of an ExecutionPlan."""
    PREPARED = "PREPARED"
    RUNNING = "RUNNING"
    INTERRUPTED = "INTERRUPTED"
    RECOVERABLE = "RECOVERABLE"
    RESUMING = "RESUMING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    CANCELLED = "CANCELLED"


class CheckpointTransactionStatus(str, Enum):
    """Write-ahead transaction states for creating a verified checkpoint."""
    PREPARING = "PREPARING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass
class CheckpointTransaction:
    """Write-ahead transaction intent record prior to git commit and checkpoint persistence."""
    id: str
    task_id: str
    plan_id: str
    step_id: str
    expected_parent_sha: str
    verification_id: str
    approved_paths: List[str] = field(default_factory=list)
    verified: bool = True
    status: CheckpointTransactionStatus = CheckpointTransactionStatus.PREPARING
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: Optional[str] = None

    def __getitem__(self, item: str) -> Any:
        val = getattr(self, item)
        if isinstance(val, Enum):
            return val.value
        return val

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "plan_id": self.plan_id,
            "step_id": self.step_id,
            "expected_parent_sha": self.expected_parent_sha,
            "verification_id": self.verification_id,
            "approved_paths": self.approved_paths,
            "verified": 1 if self.verified else 0,
            "status": self.status.value if isinstance(self.status, CheckpointTransactionStatus) else str(self.status),
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CheckpointTransaction":
        raw = dict(data)
        if "status" in raw and isinstance(raw["status"], str):
            try:
                raw["status"] = CheckpointTransactionStatus(raw["status"])
            except ValueError:
                pass
        if "verified" in raw and not isinstance(raw["verified"], bool):
            raw["verified"] = bool(raw["verified"])
        if "approved_paths" in raw and isinstance(raw["approved_paths"], str):
            import json
            try:
                raw["approved_paths"] = json.loads(raw["approved_paths"])
            except Exception:
                raw["approved_paths"] = []
        return cls(**raw)


class PromotionTransactionStatus(str, Enum):
    """Transaction states for promotion to prevent double-promotion and handle crashes."""
    NOT_STARTED = "NOT_STARTED"
    AWAITING_HUMAN = "AWAITING_HUMAN"
    PREPARING = "PREPARING"
    APPLIED = "APPLIED"
    FAILED = "FAILED"
    REQUIRES_MANUAL_RECONCILIATION = "REQUIRES_MANUAL_RECONCILIATION"


@dataclass
class PromotionTransaction:
    """Authoritative record of a promotion attempt to the target base branch."""
    id: str
    task_id: str
    target_branch: str
    expected_target_sha: str
    task_branch: str
    task_head_sha: str
    status: PromotionTransactionStatus = PromotionTransactionStatus.NOT_STARTED
    diff_hash: Optional[str] = None
    resulting_target_sha: Optional[str] = None
    error_message: Optional[str] = None
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "target_branch": self.target_branch,
            "expected_target_sha": self.expected_target_sha,
            "task_branch": self.task_branch,
            "task_head_sha": self.task_head_sha,
            "status": self.status.value if isinstance(self.status, PromotionTransactionStatus) else str(self.status),
            "diff_hash": self.diff_hash,
            "resulting_target_sha": self.resulting_target_sha,
            "error_message": self.error_message,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PromotionTransaction":
        raw = dict(data)
        if "status" in raw and isinstance(raw["status"], str):
            try:
                raw["status"] = PromotionTransactionStatus(raw["status"])
            except ValueError:
                pass
        return cls(**raw)


class VerificationType(str, Enum):
    """Normalized category of verification run."""
    STEP = "STEP"
    FINAL = "FINAL"
    POST_REPAIR = "POST_REPAIR"
    AUTONOMOUS = "AUTONOMOUS"


@dataclass
class StepResult:
    """Consolidated outcome of executing a single PlanStep."""
    step_id: str
    status: StepStatus
    files_modified: List[str] = field(default_factory=list)
    checkpoint_sha: Optional[str] = None
    verification_passed: bool = False
    verification_output: Optional[str] = None
    provider: str = ""
    repair_rounds: int = 0
    telemetry: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StepResult":
        raw = dict(data)
        if "status" in raw and isinstance(raw["status"], str):
            raw["status"] = StepStatus(raw["status"])
        return cls(**raw)


@dataclass
class PlanStep:
    """A single bounded, verifiable unit of work within an ExecutionPlan."""
    id: str
    objective: str
    rationale: str = ""
    expected_files: List[str] = field(default_factory=list)
    expected_symbols: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    verification_expectations: Optional[str] = None
    risk_level: ReviewRisk = ReviewRisk.LOW
    estimated_complexity: Complexity = Complexity.LOW
    status: StepStatus = StepStatus.PENDING
    result: Optional[StepResult] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["risk_level"] = self.risk_level.value
        data["estimated_complexity"] = self.estimated_complexity.value
        data["status"] = self.status.value
        if self.result:
            data["result"] = self.result.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PlanStep":
        raw = dict(data)
        if "risk_level" in raw and isinstance(raw["risk_level"], str):
            raw["risk_level"] = ReviewRisk(raw["risk_level"])
        if "estimated_complexity" in raw and isinstance(raw["estimated_complexity"], str):
            raw["estimated_complexity"] = Complexity(raw["estimated_complexity"])
        if "status" in raw and isinstance(raw["status"], str):
            raw["status"] = StepStatus(raw["status"])
        if raw.get("result") and isinstance(raw["result"], dict):
            raw["result"] = StepResult.from_dict(raw["result"])
        return cls(**raw)


@dataclass
class ExecutionPlan:
    """Structured, bounded plan containing dependent steps for task execution."""
    plan_id: str
    task_id: str
    title: str
    summary: str = ""
    steps: List[PlanStep] = field(default_factory=list)
    current_step_index: int = 0
    amendments_count: int = 0
    status: PlanStatus = PlanStatus.PREPARED
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def get_step(self, step_id: str) -> Optional[PlanStep]:
        """Find a step by its ID."""
        for step in self.steps:
            if step.id == step_id:
                return step
        return None

    def get_pending_steps(self) -> List[PlanStep]:
        """Return all steps whose status is PENDING."""
        return [s for s in self.steps if s.status == StepStatus.PENDING]

    def get_completed_steps(self) -> List[PlanStep]:
        """Return all steps that succeeded."""
        return [s for s in self.steps if s.status == StepStatus.COMPLETED]

    def is_step_runnable(self, step: PlanStep) -> bool:
        """Check if all dependencies of the given step are COMPLETED."""
        for dep_id in step.dependencies:
            dep_step = self.get_step(dep_id)
            if not dep_step or dep_step.status != StepStatus.COMPLETED:
                return False
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "task_id": self.task_id,
            "title": self.title,
            "summary": self.summary,
            "steps": [s.to_dict() for s in self.steps],
            "current_step_index": self.current_step_index,
            "amendments_count": self.amendments_count,
            "status": self.status.value if isinstance(self.status, PlanStatus) else str(self.status),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], task_id: Optional[str] = None) -> "ExecutionPlan":
        raw = dict(data)
        if task_id:
            raw["task_id"] = task_id
        if "plan_id" not in raw and "id" in raw:
            raw["plan_id"] = raw.pop("id")
        elif "plan_id" not in raw:
            raw["plan_id"] = str(uuid.uuid4())[:8]
        steps_data = raw.pop("steps", [])
        steps = [PlanStep.from_dict(s) for s in steps_data]
        if "status" in raw and isinstance(raw["status"], str):
            try:
                raw["status"] = PlanStatus(raw["status"])
            except ValueError:
                pass
        return cls(steps=steps, **raw)


@dataclass
class Checkpoint:
    """Record of a verified Git commit checkpoint on the isolated task branch."""
    checkpoint_id: str
    plan_id: str
    task_id: str
    step_id: str
    commit_sha: str
    base_commit_sha: str
    files_changed: List[str] = field(default_factory=list)
    diff_summary: str = ""
    verification_passed: bool = False
    provider_name: str = ""
    token_metrics: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def checkpoint_sha(self) -> str:
        return self.commit_sha

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Checkpoint":
        return cls(**data)
