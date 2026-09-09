"""Deterministic task assessment data structures."""

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Dict

from fusion_agent.models.task import Complexity, TaskType


class ScopeEstimate(str, Enum):
    """Estimated repository footprint of the task."""
    SINGLE_FILE = "SINGLE_FILE"
    MULTI_FILE = "MULTI_FILE"
    REPO_WIDE = "REPO_WIDE"


class ReviewRisk(str, Enum):
    """Estimated regression, safety, or architectural risk of changes."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass
class TaskAssessment:
    """Normalized deterministic signals extracted from user task."""
    task_type: TaskType
    complexity: Complexity
    estimated_scope: ScopeEstimate
    architecture_reasoning_required: bool
    debugging_required: bool
    implementation_required: bool
    review_risk: ReviewRisk
    expected_files_count: int
    security_sensitive: bool
    second_model_benefit: bool
    rationale: str

    def to_dict(self) -> Dict[str, Any]:
        """Serialize assessment to dictionary."""
        data = asdict(self)
        data["task_type"] = self.task_type.value
        data["complexity"] = self.complexity.value
        data["estimated_scope"] = self.estimated_scope.value
        data["review_risk"] = self.review_risk.value
        return data
