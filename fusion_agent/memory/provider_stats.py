"""Provider execution statistics tracker and rolling metrics calculator."""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from fusion_agent.memory.database import Database


@dataclass
class ProviderHistoricalStats:
    """Rolling performance statistics for a specific provider."""
    provider_name: str
    total_tasks_attempted: int = 0
    tasks_succeeded: int = 0
    task_success_rate: float = 1.0  # Default 1.0 when no history
    total_reviews_conducted: int = 0
    actionable_reviews_count: int = 0
    review_usefulness_rate: float = 0.8  # Default 0.8 when no history
    average_repair_rounds: float = 0.5
    average_duration_ms: float = 0.0
    average_input_tokens: Optional[int] = None
    average_output_tokens: Optional[int] = None
    timeout_count: int = 0
    failure_rate: float = 0.0


class ProviderStatsTracker:
    """Calculates deterministic rolling performance metrics from SQLite."""

    def __init__(self, database: Database):
        self.db = database

    def get_provider_stats(self, provider_name: str, window: int = 20) -> ProviderHistoricalStats:
        """Query recent task runs and reviews to compute rolling stats."""
        conn = self.db.connect()

        # 1. Query recent agent_runs for this provider
        runs_cursor = conn.execute(
            """
            SELECT ar.role, ar.duration_ms, ar.input_tokens, ar.output_tokens, ar.status,
                   t.status as task_status, t.verification_passed, t.repair_rounds
            FROM agent_runs ar
            LEFT JOIN tasks t ON ar.task_id = t.id
            WHERE ar.provider_name = ?
            ORDER BY ar.timestamp DESC
            LIMIT ?;
            """,
            (provider_name, window),
        )
        runs = [dict(r) for r in runs_cursor.fetchall()]

        # 2. Query recent reviews conducted by this provider
        reviews_cursor = conn.execute(
            """
            SELECT r.status as review_status, t.status as task_status, t.verification_passed
            FROM reviews r
            LEFT JOIN tasks t ON r.task_id = t.id
            WHERE r.reviewer_provider = ?
            ORDER BY r.timestamp DESC
            LIMIT ?;
            """,
            (provider_name, window),
        )
        reviews = [dict(r) for r in reviews_cursor.fetchall()]

        if not runs and not reviews:
            return ProviderHistoricalStats(provider_name=provider_name)

        total_runs = len(runs)
        succeeded_runs = sum(
            1 for r in runs if r.get("status") == "SUCCESS" and r.get("task_status") == "COMPLETED"
        )
        success_rate = (succeeded_runs / total_runs) if total_runs > 0 else 1.0
        failure_rate = 1.0 - success_rate

        total_duration = sum(r.get("duration_ms", 0.0) or 0.0 for r in runs)
        avg_duration = (total_duration / total_runs) if total_runs > 0 else 0.0

        in_tokens = [
            r["input_tokens"] for r in runs if r.get("input_tokens") is not None and r["input_tokens"] > 0
        ]
        avg_in_tokens = int(sum(in_tokens) / len(in_tokens)) if in_tokens else None

        out_tokens = [
            r["output_tokens"] for r in runs if r.get("output_tokens") is not None and r["output_tokens"] > 0
        ]
        avg_out_tokens = int(sum(out_tokens) / len(out_tokens)) if out_tokens else None

        repair_counts = [r["repair_rounds"] for r in runs if r.get("repair_rounds") is not None]
        avg_repairs = (sum(repair_counts) / len(repair_counts)) if repair_counts else 0.0

        # Review stats
        total_revs = len(reviews)
        actionable_revs = sum(
            1 for r in reviews if r.get("verification_passed") == 1 or r.get("task_status") == "COMPLETED"
        )
        review_usefulness = (actionable_revs / total_revs) if total_revs > 0 else 0.8

        return ProviderHistoricalStats(
            provider_name=provider_name,
            total_tasks_attempted=total_runs,
            tasks_succeeded=succeeded_runs,
            task_success_rate=round(success_rate, 3),
            total_reviews_conducted=total_revs,
            actionable_reviews_count=actionable_revs,
            review_usefulness_rate=round(review_usefulness, 3),
            average_repair_rounds=round(avg_repairs, 2),
            average_duration_ms=round(avg_duration, 1),
            average_input_tokens=avg_in_tokens,
            average_output_tokens=avg_out_tokens,
            failure_rate=round(failure_rate, 3),
        )

    def get_all_provider_stats(
        self,
        provider_names: Optional[List[str]] = None,
        window: int = 20,
    ) -> Dict[str, ProviderHistoricalStats]:
        """Compute rolling stats for multiple providers, or all providers found in database."""
        conn = self.db.connect()
        if not provider_names:
            cursor = conn.execute(
                "SELECT DISTINCT provider_name FROM agent_runs UNION SELECT DISTINCT reviewer_provider FROM reviews"
            )
            names = [row[0] for row in cursor.fetchall() if row[0]]
        else:
            names = provider_names

        return {name: self.get_provider_stats(name, window=window) for name in names}
