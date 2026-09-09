"""Models for agent proposals, critiques, reviews, and deliberation results."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class ReviewStatus(str, Enum):
    APPROVED = "APPROVED"
    NEEDS_REVISION = "NEEDS_REVISION"
    REJECTED = "REJECTED"


@dataclass
class Proposal:
    agent_name: str
    summary: str
    content: str
    confidence: float = 1.0
    duration_ms: float = 0.0
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class Critique:
    reviewer_agent: str
    target_agent: str
    content: str
    strengths: List[str] = field(default_factory=list)
    weaknesses: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    duration_ms: float = 0.0
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class ReviewResult:
    reviewer_agent: str
    subject_agent: str
    status: ReviewStatus
    comments: str
    suggested_fixes: List[str] = field(default_factory=list)
    duration_ms: float = 0.0
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class DeliberationResult:
    strategy_used: str
    synthesized_output: str
    proposals: List[Proposal] = field(default_factory=list)
    critiques: List[Critique] = field(default_factory=list)
    reviews: List[ReviewResult] = field(default_factory=list)
    participating_providers: List[str] = field(default_factory=list)
    rounds_executed: int = 1
    total_input_tokens: Optional[int] = None
    total_output_tokens: Optional[int] = None
    duration_ms: float = 0.0
    stage_metrics: List[Dict[str, Any]] = field(default_factory=list)
