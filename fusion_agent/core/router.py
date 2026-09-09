"""Deterministic strategy and provider router."""

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from fusion_agent.config.schema import OptimizationMode
from fusion_agent.models.strategy import StrategyType
from fusion_agent.models.task import Complexity, TaskType
from fusion_agent.providers.base import AgentProvider


@dataclass
class RoutingDecision:
    """Outcome of router analysis for a given task."""
    task_type: TaskType
    complexity: Complexity
    strategy: StrategyType
    primary_provider: str
    secondary_provider: Optional[str]
    rationale: str


class TaskRouter:
    """Classifies tasks and deterministically chooses collaboration strategies."""

    # Keywords for task classification
    BUG_PATTERNS = [
        r"\b(deadlocks?|bugs?|crash(es)?|errors?|exceptions?|fail(s|ure|ures|ing)?|hangs?|leaks?|race\s+conditions?|freez(e|es|ing))\b",
        r"\b(investigat(e|ing|ion)?|debug(ging)?|diagnos(e|ing|is)?|troubleshoot(ing)?|why\b)",
    ]
    ARCH_PATTERNS = [
        r"\b(architect(ure|ural)?|design(ing)?|propos(e|al|ing)?|rfc|system\s+design|tradeoffs?|schemas?)\b",
        r"\b(plan\s+out|blueprint|structur(e|ing))\b",
    ]
    CODE_PATTERNS = [
        r"\b(implement(ing|ation)?|creat(e|ing)|add(ing)?|writ(e|ing)|build(ing)?|refactor(ing)?|updat(e|ing)|edit(ing)?|modify(ing)?)\b",
        r"\b(functions?|class(es)?|methods?|modules?|endpoints?|features?)\b",
    ]
    SIMPLE_PATTERNS = [
        r"\b(what\s+is|explain|how\s+to|show|list|describe|help|status|where\s+is)\b",
    ]

    def classify_task(self, title_or_prompt: str) -> tuple[TaskType, Complexity]:
        """Determine TaskType and Complexity from task description."""
        text = title_or_prompt.lower()

        # Check for bug / deadlock investigation
        if any(re.search(p, text) for p in self.BUG_PATTERNS):
            return TaskType.BUG_INVESTIGATION, Complexity.HIGH

        # Check for architectural design
        if any(re.search(p, text) for p in self.ARCH_PATTERNS):
            return TaskType.ARCHITECTURE_DESIGN, Complexity.HIGH

        # Check for code modification
        if any(re.search(p, text) for p in self.CODE_PATTERNS):
            return TaskType.CODE_MODIFICATION, Complexity.MEDIUM

        # Check for simple queries
        if any(re.search(p, text) for p in self.SIMPLE_PATTERNS):
            return TaskType.SIMPLE_QUERY, Complexity.LOW

        return TaskType.GENERAL, Complexity.MEDIUM

    def route(
        self,
        task_prompt: str,
        available_providers: Dict[str, AgentProvider],
        optimization_mode: OptimizationMode = OptimizationMode.BALANCED,
    ) -> RoutingDecision:
        """Deterministically determine the strategy and provider assignments."""
        task_type, complexity = self.classify_task(task_prompt)
        provider_names = list(available_providers.keys())

        if not provider_names:
            raise ValueError("No providers available to route the task.")

        primary = provider_names[0]
        secondary = provider_names[1] if len(provider_names) > 1 else None

        # If only 1 provider is available, we must use DIRECT
        if not secondary:
            return RoutingDecision(
                task_type=task_type,
                complexity=complexity,
                strategy=StrategyType.DIRECT,
                primary_provider=primary,
                secondary_provider=None,
                rationale=f"Only one provider '{primary}' configured; using DIRECT execution.",
            )

        # Base strategy by task type and complexity
        if task_type == TaskType.SIMPLE_QUERY or complexity == Complexity.LOW:
            base_strategy = StrategyType.DIRECT
            rationale = "Simple task with low complexity; single agent execution is optimal."

        elif task_type == TaskType.BUG_INVESTIGATION:
            base_strategy = StrategyType.INDEPENDENT_INVESTIGATION
            rationale = "Bug/concurrency investigation benefits from independent hypotheses from both agents."

        elif task_type == TaskType.ARCHITECTURE_DESIGN or complexity in (Complexity.HIGH, Complexity.CRITICAL):
            base_strategy = StrategyType.PROPOSE_CRITIQUE_REFINE
            rationale = "High-complexity design benefits from cross-agent proposal and critique."

        elif task_type == TaskType.CODE_MODIFICATION:
            base_strategy = StrategyType.AUTONOMOUS_EDIT
            rationale = "Code modification executes in an isolated workspace with test verification and peer diff review."

        else:
            base_strategy = StrategyType.EXECUTE_AND_REVIEW
            rationale = "General task executed by primary agent and reviewed by secondary agent."

        # Adjust for optimization mode
        if optimization_mode == OptimizationMode.FASTEST:
            if base_strategy in (StrategyType.PROPOSE_CRITIQUE_REFINE, StrategyType.INDEPENDENT_INVESTIGATION):
                base_strategy = StrategyType.EXECUTE_AND_REVIEW
                rationale += " (Adjusted to EXECUTE_AND_REVIEW for FASTEST mode)"
            elif base_strategy in (StrategyType.EXECUTE_AND_REVIEW, StrategyType.AUTONOMOUS_EDIT):
                base_strategy = StrategyType.DIRECT
                rationale += " (Adjusted to DIRECT for FASTEST mode)"

        elif optimization_mode == OptimizationMode.LOWEST_COST:
            # Check if any provider is free or local
            local_free = [p for p, prov in available_providers.items() if prov.get_capabilities().is_free_or_local()]
            if local_free:
                primary = local_free[0]
            if base_strategy != StrategyType.DIRECT and complexity != Complexity.CRITICAL:
                base_strategy = StrategyType.DIRECT
                rationale += " (Downgraded to DIRECT to minimize cost)"

        elif optimization_mode == OptimizationMode.BEST_QUALITY:
            if base_strategy == StrategyType.EXECUTE_AND_REVIEW:
                base_strategy = StrategyType.PROPOSE_CRITIQUE_REFINE
                rationale += " (Escalated to PROPOSE_CRITIQUE_REFINE for BEST_QUALITY mode)"

        return RoutingDecision(
            task_type=task_type,
            complexity=complexity,
            strategy=base_strategy,
            primary_provider=primary,
            secondary_provider=secondary,
            rationale=rationale,
        )
