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
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


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
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: Optional[str] = None
