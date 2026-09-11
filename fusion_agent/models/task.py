"""Task representation and classification models."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    DELIBERATING = "DELIBERATING"
    IMPLEMENTING = "IMPLEMENTING"
    REVIEWING = "REVIEWING"
    INTERRUPTED = "INTERRUPTED"
    RECOVERABLE = "RECOVERABLE"
    RESUMING = "RESUMING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TaskType(str, Enum):
    SIMPLE_QUERY = "SIMPLE_QUERY"
    CODE_MODIFICATION = "CODE_MODIFICATION"
    BUG_INVESTIGATION = "BUG_INVESTIGATION"
    ARCHITECTURE_DESIGN = "ARCHITECTURE_DESIGN"
    CRITICAL_REFACTOR = "CRITICAL_REFACTOR"
    GENERAL = "GENERAL"


class Complexity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class PromotionDisposition(str, Enum):
    """Disposition of task promotion to base branch."""
    NOT_OFFERED = "NOT_OFFERED"
    PENDING = "PENDING"
    PROMOTED = "PROMOTED"
    DECLINED = "DECLINED"
    BLOCKED = "BLOCKED"


@dataclass
class Task:
    id: str
    title: str
    description: str
    project_id: str
    task_type: TaskType = TaskType.GENERAL
    complexity: Complexity = Complexity.MEDIUM
    status: TaskStatus = TaskStatus.PENDING
    selected_strategy: Optional[str] = None
    verification_passed: bool = False
    repair_rounds: int = 0
    promotion_disposition: PromotionDisposition = PromotionDisposition.NOT_OFFERED
    active_stage: Optional[str] = None
    last_checkpoint_sha: Optional[str] = None
    interruption_reason: Optional[str] = None
    interrupted_at: Optional[str] = None
    recovery_attempts: int = 0
    resumed_at: Optional[str] = None
    execution_config_snapshot: Optional[str] = None
    repo_fingerprint: Optional[str] = None
    base_commit: Optional[str] = None
    scope_expansions_json: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: Optional[str] = None
