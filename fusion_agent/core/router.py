"""Deterministic strategy, task assessment, and provider role router."""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from fusion_agent.config.schema import OptimizationMode
from fusion_agent.models.assessment import (
    ExecutionIntent,
    ReviewRisk,
    ScopeEstimate,
    TaskAssessment,
)
from fusion_agent.models.strategy import StrategyType
from fusion_agent.models.task import Complexity, TaskType
from fusion_agent.providers.base import AgentProvider
from fusion_agent.providers.capabilities import CapabilityStrength, CostTier


@dataclass
class RoutingDecision:
    """Outcome of router analysis for a given task."""
    task_type: TaskType
    complexity: Complexity
    strategy: StrategyType
    primary_provider: str
    secondary_provider: Optional[str]
    rationale: str
    task_assessment: Optional[TaskAssessment] = None
    role_assignments: Dict[str, Optional[str]] = field(default_factory=dict)
    scoring_breakdown: Dict[str, Any] = field(default_factory=dict)


class TaskRouter:
    """Classifies tasks and deterministically chooses collaboration strategies and roles."""

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
        r"\b(implement(ing|ation)?|creat(e|ing)|add(ing)?|writ(e|ing)|build(ing)?|refactor(ing)?|updat(e|ing)|edit(ing)?|modify(ing)?|fix(es|ing|ed)?|repair(ing|s|ed)?|resolv(e|ing|es|ed)?|solv(e|ing|es|ed)?|migrat(e|ing|ion)?)\b",
        r"\b(functions?|class(es)?|methods?|modules?|endpoints?|features?|patch(es)?|tests?|handlers?|routes?|services?)\b",
    ]
    REFACTOR_PATTERNS = [
        r"\b(refactor(ing)?|restructur(e|ing)|reorganiz(e|ing)|renam(e|ing)|clean\s*up|extract|modulariz(e|ing))\b",
    ]
    SECURITY_PATTERNS = [
        r"\b(auth|authentication|authorization|passwords?|tokens?|secrets?|ciphers?|encrypt(ion)?|sanitiz(e|ing)|inject(ion)?|csrf|xss|vulnerabilit(y|ies))\b",
    ]
    SIMPLE_PATTERNS = [
        r"\b(what\s+is|explain|how\s+to|show|list|describe|help|status|where\s+is)\b",
    ]
    MULTI_FILE_PATTERNS = [
        r"\b(across\s+files|multiple\s+files|repository|repo-wide|modules?|packages?|directories|subsystems?)\b",
    ]
    MULTI_COMPONENT_PATTERNS = [
        r"\b(schema\s+and\s+(\w+\s+)?(cli|query|service|command)|cli\s+command\s+and|service\s+and\s+cli|multi-step|database\s+and\s+cli|pipeline\s+and\s+tests?)\b",
        r"\b(and\s+expose\s+a\s+cli|and\s+add\s+a\s+cli|and\s+create\s+a\s+cli|and\s+.*?\btests?)\b",
    ]

    # Action-verb regexes for operational execution intent
    EDIT_VERB_PATTERNS = (
        r"\b(fix(es|ed|ing)?|implement(s|ed|ing|ation)?|creat(e|es|ed|ing)|add(s|ed|ing)?|"
        r"writ(e|es|rote|ing)|build(s|ing|t)?|refactor(s|ed|ing)?|updat(e|es|ed|ing)|"
        r"edit(s|ed|ing)?|modify(ing|ies|ied)?|repair(s|ed|ing)?|migrat(e|es|ed|ing|ion)?|"
        r"resolv(e|es|ed|ing)?|solv(e|es|ed|ing)?|clean\s*up|patch(es|ed|ing)?)\b"
    )
    INVESTIGATION_VERB_PATTERNS = (
        r"\b(investigat(e|es|ed|ing|ion)?|diagnos(e|es|ed|ing|is)?|troubleshoot(ing|s)?|"
        r"find\s+(why|root\s*cause)|why\s+does|why\s+is|inspect(ing|ion)?)\b"
    )
    DESIGN_VERB_PATTERNS = (
        r"\b(architect(ure|ural)?|design(s|ed|ing)?|propos(e|es|ed|al|ing)?|rfc|"
        r"system\s+design|tradeoffs?|blueprint|spec(ification)?)\b"
    )
    QUERY_VERB_PATTERNS = (
        r"\b(what\s+is|explain|how\s+to|show|list|describe|help|status|where\s+is)\b"
    )
    CODE_PATH_PATTERN = r"\b[a-zA-Z0-9_\-./\\]+\.(py|js|ts|jsx|tsx|go|rs|cpp|c|h|java|rb|php|html|css|sql|sh)\b"

    def assess_task(self, title_or_prompt: str) -> TaskAssessment:
        """Perform deterministic TaskAssessment on task prompt deriving ExecutionIntent."""
        text = title_or_prompt.lower()

        debugging = any(re.search(p, text) for p in self.BUG_PATTERNS)
        arch = any(re.search(p, text) for p in self.ARCH_PATTERNS)
        code = any(re.search(p, text) for p in self.CODE_PATTERNS)
        refactor = any(re.search(p, text) for p in self.REFACTOR_PATTERNS)
        security = any(re.search(p, text) for p in self.SECURITY_PATTERNS)
        simple = any(re.search(p, text) for p in self.SIMPLE_PATTERNS)
        multi_file = any(re.search(p, text) for p in self.MULTI_FILE_PATTERNS)
        multi_component = any(re.search(p, text) for p in self.MULTI_COMPONENT_PATTERNS)

        # Code paths mentioned in prompt
        code_paths = re.findall(self.CODE_PATH_PATTERN, text)
        is_multi_file = multi_file or multi_component or len(code_paths) >= 2

        has_edit_verbs = bool(re.search(self.EDIT_VERB_PATTERNS, text))
        has_query_verbs = bool(re.search(self.QUERY_VERB_PATTERNS, text))
        has_investigate_verbs = bool(re.search(self.INVESTIGATION_VERB_PATTERNS, text))
        has_design_verbs = bool(re.search(self.DESIGN_VERB_PATTERNS, text))

        # 1. Determine ExecutionIntent and implementation requirement
        if has_edit_verbs:
            if is_multi_file:
                execution_intent = ExecutionIntent.MULTI_STEP_CODE_EDIT
            else:
                execution_intent = ExecutionIntent.CODE_EDIT
            implementation_required = True
        elif has_design_verbs and not code_paths:
            execution_intent = ExecutionIntent.DESIGN_ANALYSIS
            implementation_required = False
        elif has_investigate_verbs and not code_paths and not has_edit_verbs:
            execution_intent = ExecutionIntent.INVESTIGATION
            implementation_required = False
        elif has_query_verbs and not code_paths and not has_edit_verbs:
            execution_intent = ExecutionIntent.ANSWER_ONLY
            implementation_required = False
        elif code_paths or code or refactor:
            execution_intent = ExecutionIntent.MULTI_STEP_CODE_EDIT if is_multi_file else ExecutionIntent.CODE_EDIT
            implementation_required = True
        elif simple:
            execution_intent = ExecutionIntent.ANSWER_ONLY
            implementation_required = False
        else:
            execution_intent = ExecutionIntent.ANSWER_ONLY
            implementation_required = False

        # 2. Determine TaskType
        if execution_intent in (ExecutionIntent.CODE_EDIT, ExecutionIntent.MULTI_STEP_CODE_EDIT):
            if debugging:
                task_type = TaskType.BUG_INVESTIGATION
            elif refactor:
                task_type = TaskType.CRITICAL_REFACTOR
            else:
                task_type = TaskType.CODE_MODIFICATION
        elif execution_intent == ExecutionIntent.INVESTIGATION:
            task_type = TaskType.BUG_INVESTIGATION
        elif execution_intent == ExecutionIntent.DESIGN_ANALYSIS:
            task_type = TaskType.ARCHITECTURE_DESIGN
        elif execution_intent == ExecutionIntent.ANSWER_ONLY:
            task_type = TaskType.SIMPLE_QUERY
        else:
            task_type = TaskType.GENERAL

        # 3. Determine Complexity
        if security or (debugging and arch):
            complexity = Complexity.CRITICAL
        elif execution_intent == ExecutionIntent.MULTI_STEP_CODE_EDIT or (debugging and not has_edit_verbs) or arch or multi_component:
            complexity = Complexity.HIGH
        elif implementation_required or code or refactor:
            complexity = Complexity.MEDIUM
        elif simple:
            complexity = Complexity.LOW
        else:
            complexity = Complexity.MEDIUM

        # 4. Determine Estimated Scope
        if is_multi_file or execution_intent == ExecutionIntent.MULTI_STEP_CODE_EDIT:
            scope = ScopeEstimate.MULTI_FILE
        elif execution_intent == ExecutionIntent.DESIGN_ANALYSIS or task_type == TaskType.ARCHITECTURE_DESIGN:
            scope = ScopeEstimate.REPO_WIDE
        elif implementation_required:
            scope = ScopeEstimate.SINGLE_FILE
        else:
            scope = ScopeEstimate.SINGLE_FILE

        # 5. Determine Review Risk
        if security:
            risk = ReviewRisk.CRITICAL
        elif complexity in (Complexity.HIGH, Complexity.CRITICAL) or scope in (ScopeEstimate.MULTI_FILE, ScopeEstimate.REPO_WIDE):
            risk = ReviewRisk.HIGH
        elif arch or (debugging and not has_edit_verbs) or is_multi_file or any(w in text for w in ["refactor", "migrate", "redesign"]):
            risk = ReviewRisk.MEDIUM
        elif complexity == Complexity.LOW:
            risk = ReviewRisk.LOW
        elif implementation_required and scope == ScopeEstimate.SINGLE_FILE and len(code_paths) == 1 and not arch and not security and any(w in text for w in ["fix", "repair", "correct", "typo", "minor", "off-by-one"]):
            risk = ReviewRisk.LOW
        elif implementation_required:
            risk = ReviewRisk.MEDIUM
        else:
            risk = ReviewRisk.LOW

        # 6. Expected Files Count
        if len(code_paths) > 0:
            expected_files = len(code_paths)
        elif scope == ScopeEstimate.REPO_WIDE:
            expected_files = 5
        elif scope == ScopeEstimate.MULTI_FILE:
            expected_files = 3
        elif implementation_required:
            expected_files = 1
        else:
            expected_files = 0

        # 7. Second Model Benefit heuristic
        if complexity == Complexity.LOW or execution_intent == ExecutionIntent.ANSWER_ONLY:
            second_model_benefit = False
        elif security or complexity in (Complexity.HIGH, Complexity.CRITICAL) or execution_intent == ExecutionIntent.INVESTIGATION:
            second_model_benefit = True
        elif implementation_required and risk in (ReviewRisk.MEDIUM, ReviewRisk.HIGH, ReviewRisk.CRITICAL):
            second_model_benefit = True
        else:
            second_model_benefit = False

        rationale = (
            f"Assessed as {execution_intent.value} / {task_type.value} "
            f"({complexity.value} complexity, {scope.value} scope, {risk.value} risk). "
            f"Implementation required: {implementation_required}. "
            f"Second model benefit: {second_model_benefit}."
        )

        return TaskAssessment(
            task_type=task_type,
            complexity=complexity,
            estimated_scope=scope,
            architecture_reasoning_required=arch,
            debugging_required=debugging,
            implementation_required=implementation_required,
            review_risk=risk,
            expected_files_count=expected_files,
            security_sensitive=security,
            second_model_benefit=second_model_benefit,
            rationale=rationale,
            execution_intent=execution_intent,
        )

    def classify_task(self, title_or_prompt: str) -> Tuple[TaskType, Complexity]:
        """Backward-compatible tuple extractor returning (TaskType, Complexity)."""
        assessment = self.assess_task(title_or_prompt)
        return assessment.task_type, assessment.complexity

    def _score_provider_for_role(
        self,
        provider_name: str,
        provider: AgentProvider,
        role: str,
        optimization_mode: OptimizationMode,
        task_type: Optional[TaskType] = None,
        stats: Optional[Any] = None,
    ) -> float:
        """Calculate explainable score for a provider filling a specific role."""
        caps = provider.get_capabilities()
        score = 0.0

        pref_task = task_type.value if task_type else ""

        if role == "implementer":
            # Coding strength (40%), structured output (20%), task preference (20%), free/subscription tier (20%)
            coding_score = getattr(caps, "coding_strength", CapabilityStrength.HIGH).numeric_score
            score += coding_score * 0.40
            score += (1.0 if caps.structured_output else 0.5) * 0.20
            score += (1.0 if ("CODE_MODIFICATION" in caps.preferred_task_types or pref_task in caps.preferred_task_types) else 0.7) * 0.20
            score += (0.20 if caps.is_free_or_local() else 0.10 if caps.cost_tier == CostTier.SUBSCRIPTION else 0.05)

            if optimization_mode == OptimizationMode.FASTEST:
                latency = getattr(caps, "typical_latency_ms", 30000.0)
                if stats and hasattr(stats, "average_duration_ms") and stats.average_duration_ms > 0:
                    latency = stats.average_duration_ms
                score += max(0.0, 1.0 - (latency / 60000.0)) * 0.30

            if optimization_mode == OptimizationMode.LOWEST_COST:
                if caps.cost_tier == CostTier.PAID_API:
                    score -= 0.50
                if stats and hasattr(stats, "average_input_tokens") and stats.average_input_tokens:
                    # Token efficiency bonus/penalty based on historical input usage
                    score += max(0.0, 1.0 - (stats.average_input_tokens / 50000.0)) * 0.15

        elif role == "reviewer":
            # Review strength (45%), reasoning strength (35%), task preference (20%)
            review_score = getattr(caps, "review_strength", CapabilityStrength.HIGH).numeric_score
            reasoning_score = getattr(caps, "reasoning_strength", CapabilityStrength.HIGH).numeric_score
            score += review_score * 0.45
            score += reasoning_score * 0.35
            score += (1.0 if "CODE_REVIEW" in caps.preferred_task_types else 0.7) * 0.20

            if stats and hasattr(stats, "review_usefulness_rate"):
                score += (stats.review_usefulness_rate - 0.5) * 0.20

        else:  # "lead" / "proposer"
            # Reasoning strength (50%), task preference match (30%), general reasoning (20%)
            reasoning_score = getattr(caps, "reasoning_strength", CapabilityStrength.HIGH).numeric_score
            pref_match = 1.0 if (pref_task and pref_task in caps.preferred_task_types) else 0.7
            score += reasoning_score * 0.50
            score += pref_match * 0.30
            score += (1.0 if caps.reasoning else 0.5) * 0.20

        return round(score, 3)

    def assess_mcp_capabilities(
        self,
        task_prompt: str,
        mcp_registry: Optional[Any] = None,
    ) -> List[str]:
        """Inspect registered MCP tools to determine if external tools are available."""
        if not mcp_registry or not hasattr(mcp_registry, "list_all_tools"):
            return []
        try:
            tools = mcp_registry.list_all_tools()
            return [f"{t.server_id}/{t.tool_name}" for t in tools]
        except Exception:
            return []

    def route(
        self,
        task_prompt: str,
        available_providers: Dict[str, AgentProvider],
        optimization_mode: OptimizationMode = OptimizationMode.BALANCED,
        provider_stats: Optional[Dict[str, Any]] = None,
        allow_multi_step_planning: bool = True,
        mcp_registry: Optional[Any] = None,
    ) -> RoutingDecision:
        """Deterministically determine collaboration strategy and dynamic provider roles."""
        assessment = self.assess_task(task_prompt)
        task_type = assessment.task_type
        complexity = assessment.complexity
        relevant_mcp_tools = self.assess_mcp_capabilities(task_prompt, mcp_registry)


        # 1. Filter providers by constraints
        eligible_providers = dict(available_providers)
        if optimization_mode == OptimizationMode.LOCAL_PRIVATE:
            eligible_providers = {
                k: v for k, v in eligible_providers.items() if v.get_capabilities().local
            }
            if not eligible_providers:
                raise ValueError("No local providers available for LOCAL_PRIVATE optimization mode.")

        provider_names = list(eligible_providers.keys())
        if not provider_names:
            raise ValueError("No eligible providers available to route the task.")

        # 2. Score providers across roles
        scoring_breakdown: Dict[str, Any] = {}
        implementer_scores: Dict[str, float] = {}
        reviewer_scores: Dict[str, float] = {}
        lead_scores: Dict[str, float] = {}

        p_stats = provider_stats or {}

        for p_name, p_inst in eligible_providers.items():
            st = p_stats.get(p_name)
            imp_s = self._score_provider_for_role(p_name, p_inst, "implementer", optimization_mode, task_type=task_type, stats=st)
            rev_s = self._score_provider_for_role(p_name, p_inst, "reviewer", optimization_mode, task_type=task_type, stats=st)
            lead_s = self._score_provider_for_role(p_name, p_inst, "lead", optimization_mode, task_type=task_type, stats=st)

            implementer_scores[p_name] = imp_s
            reviewer_scores[p_name] = rev_s
            lead_scores[p_name] = lead_s

            scoring_breakdown[p_name] = {
                "implementer_score": imp_s,
                "reviewer_score": rev_s,
                "lead_score": lead_s,
            }

        # Stable sort preserving original insertion order on ties
        ranked_implementers = sorted(
            provider_names,
            key=lambda p: (implementer_scores[p], -provider_names.index(p)),
            reverse=True,
        )
        ranked_reviewers = sorted(
            provider_names,
            key=lambda p: (reviewer_scores[p], -provider_names.index(p)),
            reverse=True,
        )
        ranked_leads = sorted(
            provider_names,
            key=lambda p: (lead_scores[p], -provider_names.index(p)),
            reverse=True,
        )

        # If only 1 provider is available, we must use DIRECT
        if len(provider_names) == 1:
            lead = provider_names[0]
            implementer = provider_names[0] if assessment.implementation_required else lead
            return RoutingDecision(
                task_type=task_type,
                complexity=complexity,
                strategy=StrategyType.DIRECT,
                primary_provider=lead,
                secondary_provider=None,
                rationale=f"Only one provider '{lead}' configured; using DIRECT execution.",
                task_assessment=assessment,
                role_assignments={"lead": lead, "implementer": implementer, "reviewer": None},
                scoring_breakdown=scoring_breakdown,
            )

        # 3. Base strategy selection by task assessment and execution intent
        if assessment.implementation_required:
            # INVARIANT: Tasks requiring code changes MUST execute via an implementation-capable strategy!
            if (
                allow_multi_step_planning
                and (
                    assessment.estimated_scope in (ScopeEstimate.MULTI_FILE, ScopeEstimate.REPO_WIDE)
                    or assessment.execution_intent == ExecutionIntent.MULTI_STEP_CODE_EDIT
                    or (assessment.complexity in (Complexity.HIGH, Complexity.CRITICAL) and assessment.expected_files_count >= 2)
                )
            ):
                base_strategy = StrategyType.CHECKPOINTED_PLAN
                rationale = "Multi-component code task requires checkpointed multi-step execution in an isolated workspace."
            else:
                base_strategy = StrategyType.AUTONOMOUS_EDIT
                rationale = "Code modification executes in an isolated workspace with test verification and peer diff review."

        elif assessment.execution_intent == ExecutionIntent.ANSWER_ONLY or task_type == TaskType.SIMPLE_QUERY or complexity == Complexity.LOW:
            base_strategy = StrategyType.DIRECT
            rationale = "Simple task with low complexity; single agent execution is optimal."

        elif assessment.execution_intent == ExecutionIntent.INVESTIGATION or task_type == TaskType.BUG_INVESTIGATION:
            base_strategy = StrategyType.INDEPENDENT_INVESTIGATION
            rationale = "Bug/concurrency investigation benefits from independent hypotheses from both agents."

        elif assessment.execution_intent == ExecutionIntent.DESIGN_ANALYSIS or task_type == TaskType.ARCHITECTURE_DESIGN:
            base_strategy = StrategyType.PROPOSE_CRITIQUE_REFINE
            rationale = "High-complexity design benefits from cross-agent proposal and critique."

        else:
            base_strategy = StrategyType.EXECUTE_AND_REVIEW
            rationale = "General non-code task executed by primary agent and reviewed by secondary agent."

        # 4. Optimization mode adjustments
        if optimization_mode == OptimizationMode.FASTEST:
            if base_strategy in (StrategyType.PROPOSE_CRITIQUE_REFINE, StrategyType.INDEPENDENT_INVESTIGATION):
                base_strategy = StrategyType.EXECUTE_AND_REVIEW
                rationale += " (Adjusted to EXECUTE_AND_REVIEW for FASTEST mode)"
            elif base_strategy == StrategyType.CHECKPOINTED_PLAN:
                base_strategy = StrategyType.AUTONOMOUS_EDIT
                rationale += " (Adjusted to AUTONOMOUS_EDIT for FASTEST mode)"
            elif base_strategy in (StrategyType.EXECUTE_AND_REVIEW, StrategyType.AUTONOMOUS_EDIT):
                base_strategy = StrategyType.DIRECT
                rationale += " (Adjusted to DIRECT for FASTEST mode)"

        elif optimization_mode == OptimizationMode.LOWEST_COST:
            local_free = [p for p in provider_names if eligible_providers[p].get_capabilities().is_free_or_local()]
            if local_free:
                ranked_leads = [p for p in local_free if p in ranked_leads] + [p for p in ranked_leads if p not in local_free]
                ranked_implementers = [p for p in local_free if p in ranked_implementers] + [p for p in ranked_implementers if p not in local_free]
            if base_strategy != StrategyType.DIRECT and complexity != Complexity.CRITICAL:
                base_strategy = StrategyType.DIRECT
                rationale += " (Downgraded to DIRECT to minimize cost)"

        elif optimization_mode == OptimizationMode.BEST_QUALITY:
            if base_strategy == StrategyType.EXECUTE_AND_REVIEW:
                base_strategy = StrategyType.PROPOSE_CRITIQUE_REFINE
                rationale += " (Escalated to PROPOSE_CRITIQUE_REFINE for BEST_QUALITY mode)"

        # 5. Dynamic Role Assignment
        if len(provider_names) == 1 or base_strategy == StrategyType.DIRECT:
            lead = ranked_leads[0]
            implementer = ranked_implementers[0] if assessment.implementation_required else lead
            reviewer = None
        else:
            # Multi-provider role assignment
            if base_strategy == StrategyType.CHECKPOINTED_PLAN:
                lead = ranked_leads[0]
                impl_candidates = [p for p in ranked_implementers if p != lead]
                implementer = impl_candidates[0] if impl_candidates else ranked_implementers[0]
                peer_candidates = [p for p in ranked_reviewers if p != implementer and p != lead]
                if not peer_candidates:
                    peer_candidates = [p for p in ranked_reviewers if p != implementer]
                reviewer = peer_candidates[0] if (peer_candidates and assessment.second_model_benefit) else None
            elif base_strategy == StrategyType.AUTONOMOUS_EDIT:
                implementer = ranked_implementers[0]
                peer_candidates = [p for p in ranked_reviewers if p != implementer]
                reviewer = peer_candidates[0] if (peer_candidates and assessment.second_model_benefit) else None
                lead = implementer
            else:
                lead = ranked_leads[0]
                peer_candidates = [p for p in ranked_reviewers if p != lead]
                reviewer = peer_candidates[0] if peer_candidates else None
                implementer = ranked_implementers[0]

        if base_strategy == StrategyType.CHECKPOINTED_PLAN:
            primary = lead
            secondary = implementer if implementer != lead else reviewer
        elif base_strategy == StrategyType.AUTONOMOUS_EDIT:
            primary = implementer
            secondary = reviewer
        else:
            primary = lead
            secondary = reviewer

        # If strategy was autonomous edit and single provider or FASTEST mode turned off reviewer
        if base_strategy in (StrategyType.AUTONOMOUS_EDIT, StrategyType.CHECKPOINTED_PLAN) and secondary is None and len(provider_names) > 1 and not assessment.second_model_benefit:
            rationale += " Single-agent autonomous edit selected because second-model benefit is LOW."


        role_assignments = {
            "lead": lead,
            "implementer": implementer,
            "reviewer": reviewer,
        }

        return RoutingDecision(
            task_type=task_type,
            complexity=complexity,
            strategy=base_strategy,
            primary_provider=primary,
            secondary_provider=secondary,
            rationale=rationale,
            task_assessment=assessment,
            role_assignments=role_assignments,
            scoring_breakdown=scoring_breakdown,
        )
