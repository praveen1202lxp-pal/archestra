"""Multi-agent deliberation workflows with round limits and synthesis."""

import time
from typing import Any, Callable, Dict, List, Optional

from fusion_agent.config.schema import DeliberationConfig
from fusion_agent.models.deliberation import (
    Critique,
    DeliberationResult,
    Proposal,
    ReviewResult,
    ReviewStatus,
)
from fusion_agent.models.strategy import StrategyType
from fusion_agent.models.task import Task
from fusion_agent.providers.base import AgentProvider, ContextSnapshot

EventCallback = Callable[[str, Dict[str, Any]], None]


def _bound_text(text: str, max_chars: int = 1800) -> str:
    """Bound message length while preserving critical summary points for token efficiency."""
    clean = text.strip()
    if len(clean) <= max_chars:
        return clean
    return clean[:max_chars].rstrip() + "\n\n... [Truncated for brevity & token efficiency]"


def _sum_tokens(tokens_list: list) -> Optional[int]:
    """Sum valid token counts, returning None if no valid counts exist."""
    valid = [t for t in tokens_list if t is not None]
    return sum(valid) if valid else None


class DeliberationEngine:
    """Orchestrates multi-agent deliberation patterns with token-efficient response contracts."""

    def __init__(self, config: Optional[DeliberationConfig] = None):
        self.config = config or DeliberationConfig()

    def run(
        self,
        strategy: StrategyType,
        task: Task,
        primary_agent: AgentProvider,
        secondary_agent: Optional[AgentProvider] = None,
        context: Optional[ContextSnapshot] = None,
        on_event: Optional[EventCallback] = None,
    ) -> DeliberationResult:
        """Run the chosen collaboration strategy."""
        return self.execute_strategy(
            strategy=strategy,
            task=task,
            primary_agent=primary_agent,
            secondary_agent=secondary_agent,
            context=context,
            on_step=on_event,
        )

    def execute_strategy(
        self,
        strategy: StrategyType,
        task: Task,
        primary_agent: AgentProvider,
        secondary_agent: Optional[AgentProvider] = None,
        context: Optional[ContextSnapshot] = None,
        on_step: Optional[EventCallback] = None,
    ) -> DeliberationResult:
        """Execute the chosen deliberation strategy."""
        start_time = time.perf_counter()
        
        def emit(event_type: str, data: Dict[str, Any]):
            if on_step:
                on_step(event_type, data)

        if strategy == StrategyType.DIRECT or not secondary_agent:
            result = self._run_direct(task, primary_agent, context, emit)
        elif strategy == StrategyType.EXECUTE_AND_REVIEW:
            result = self._run_execute_and_review(task, primary_agent, secondary_agent, context, emit)
        elif strategy == StrategyType.PROPOSE_CRITIQUE_REFINE:
            result = self._run_propose_critique_refine(task, primary_agent, secondary_agent, context, emit)
        elif strategy == StrategyType.INDEPENDENT_INVESTIGATION:
            result = self._run_independent_investigation(task, primary_agent, secondary_agent, context, emit)
        else:
            result = self._run_direct(task, primary_agent, context, emit)

        result.duration_ms = (time.perf_counter() - start_time) * 1000.0
        return result

    def _run_direct(
        self,
        task: Task,
        agent: AgentProvider,
        context: Optional[ContextSnapshot],
        emit: EventCallback,
    ) -> DeliberationResult:
        emit("deliberation_step", {"step": f"Direct execution via {agent.name}"})
        prompt = (
            f"Task: {task.title}\n{task.description}\n\n"
            "RESPONSE CONTRACT (Keep concise, under 400 words):\n"
            "- Provide a clear, direct engineering solution with concrete rationale."
        ) if task.description else (
            f"Task: {task.title}\n\n"
            "RESPONSE CONTRACT (Keep concise, under 400 words):\n"
            "- Provide a clear, direct engineering solution with concrete rationale."
        )
        resp = agent.invoke(prompt, context=context)
        
        proposal = Proposal(
            agent_name=agent.name,
            summary=f"Direct response by {agent.name}",
            content=resp.content,
            duration_ms=resp.duration_ms,
            input_tokens=resp.input_tokens,
            output_tokens=resp.output_tokens,
        )
        stage_metrics = [
            {
                "stage": f"{agent.name} execution",
                "provider": agent.name,
                "role": "executor",
                "duration_ms": resp.duration_ms,
                "input_tokens": resp.input_tokens,
                "output_tokens": resp.output_tokens,
            }
        ]
        return DeliberationResult(
            strategy_used=StrategyType.DIRECT.value,
            synthesized_output=resp.content,
            proposals=[proposal],
            participating_providers=[agent.name],
            rounds_executed=1,
            total_input_tokens=resp.input_tokens,
            total_output_tokens=resp.output_tokens,
            duration_ms=resp.duration_ms,
            stage_metrics=stage_metrics,
        )

    def _run_execute_and_review(
        self,
        task: Task,
        primary: AgentProvider,
        reviewer: AgentProvider,
        context: Optional[ContextSnapshot],
        emit: EventCallback,
    ) -> DeliberationResult:
        task_desc = f"\n{task.description}" if task.description else ""
        prompt = (
            f"Task: {task.title}{task_desc}\n\n"
            "RESPONSE CONTRACT (Keep concise, max 350 words):\n"
            "- **Approach**: Core technical design.\n"
            "- **Implementation**: Key code or architectural change.\n"
            "- **Edge Cases**: Relevant edge cases handled."
        )
        
        # Step 1: Primary agent generates solution
        emit("deliberation_step", {"step": f"Generating implementation with {primary.name}..."})
        exec_resp = primary.invoke(prompt, context=context)
        
        stage_metrics = [
            {
                "stage": f"{primary.name} execution",
                "provider": primary.name,
                "role": "proposer",
                "duration_ms": exec_resp.duration_ms,
                "input_tokens": exec_resp.input_tokens,
                "output_tokens": exec_resp.output_tokens,
            }
        ]
        
        # Step 2: Reviewer reviews
        bounded_content = _bound_text(exec_resp.content, max_chars=1800)
        emit("deliberation_step", {"step": f"Cross-model review by {reviewer.name}..."})
        review_context = ContextSnapshot(
            permanent_context=context.permanent_context if context else "",
            current_context=context.current_context if context else "",
            recent_context=context.recent_context if context else "",
            peer_context=f"IMPLEMENTATION TO REVIEW:\n{bounded_content}",
        )
        review_resp = reviewer.review(
            content=bounded_content,
            criteria=(
                f"Task: {task.title}\n"
                "RESPONSE CONTRACT (Max 200 words):\n"
                "- Verify correctness, edge cases, and design simplicity."
            ),
            context=review_context,
        )
        stage_metrics.append({
            "stage": f"{reviewer.name} review",
            "provider": reviewer.name,
            "role": "reviewer",
            "duration_ms": review_resp.duration_ms,
            "input_tokens": review_resp.input_tokens,
            "output_tokens": review_resp.output_tokens,
        })

        review_result = ReviewResult(
            reviewer_agent=reviewer.name,
            subject_agent=primary.name,
            status=review_resp.status,
            comments=review_resp.comments,
            suggested_fixes=review_resp.suggested_fixes,
            duration_ms=review_resp.duration_ms,
            input_tokens=review_resp.input_tokens,
            output_tokens=review_resp.output_tokens,
        )

        final_output = exec_resp.content
        rounds = 1
        all_in_tokens = [exec_resp.input_tokens, review_resp.input_tokens]
        all_out_tokens = [exec_resp.output_tokens, review_resp.output_tokens]
        total_duration = exec_resp.duration_ms + review_resp.duration_ms

        # If review requested revisions and rounds permit, perform repair
        if review_resp.status != ReviewStatus.APPROVED and self.config.max_rounds > 1:
            emit("deliberation_step", {"step": f"Refining implementation based on review comments..."})
            bounded_comments = _bound_text(review_resp.comments, max_chars=1000)
            repair_prompt = (
                f"Task: {task.title}\n\n"
                f"Previous implementation:\n{bounded_content}\n\n"
                f"Review feedback from {reviewer.name}:\n{bounded_comments}\n\n"
                "RESPONSE CONTRACT (Max 350 words):\n"
                "Please produce an updated, repaired version addressing the feedback."
            )
            repair_resp = primary.invoke(repair_prompt, context=context)
            all_in_tokens.append(repair_resp.input_tokens)
            all_out_tokens.append(repair_resp.output_tokens)
            total_duration += repair_resp.duration_ms
            stage_metrics.append({
                "stage": f"{primary.name} refinement",
                "provider": primary.name,
                "role": "synthesizer",
                "duration_ms": repair_resp.duration_ms,
                "input_tokens": repair_resp.input_tokens,
                "output_tokens": repair_resp.output_tokens,
            })
            final_output = repair_resp.content
            rounds += 1

        return DeliberationResult(
            strategy_used=StrategyType.EXECUTE_AND_REVIEW.value,
            synthesized_output=final_output,
            proposals=[
                Proposal(
                    agent_name=primary.name,
                    summary="Primary implementation",
                    content=exec_resp.content,
                    duration_ms=exec_resp.duration_ms,
                    input_tokens=exec_resp.input_tokens,
                    output_tokens=exec_resp.output_tokens,
                )
            ],
            reviews=[review_result],
            participating_providers=[primary.name, reviewer.name],
            rounds_executed=rounds,
            total_input_tokens=_sum_tokens(all_in_tokens),
            total_output_tokens=_sum_tokens(all_out_tokens),
            duration_ms=total_duration,
            stage_metrics=stage_metrics,
        )

    def _run_propose_critique_refine(
        self,
        task: Task,
        agent_a: AgentProvider,
        agent_b: AgentProvider,
        context: Optional[ContextSnapshot],
        emit: EventCallback,
    ) -> DeliberationResult:
        task_desc = f"\n{task.description}" if task.description else ""
        prompt = f"Task: {task.title}{task_desc}"
        
        # Round 1: Agent A proposes with strict response contract
        emit("deliberation_step", {"step": f"{agent_a.name} is drafting initial proposal..."})
        proposal_prompt = (
            f"{prompt}\n\n"
            "RESPONSE CONTRACT (Keep concise, max 350 words):\n"
            "- **Approach**: Core technical strategy and architecture.\n"
            "- **Rationale**: Why this design solves the problem.\n"
            "- **Major Risks**: 2-3 key technical risks or tradeoffs.\n"
            "- **Implementation Outline**: 3-5 concrete steps.\n"
            "- **Confidence**: Score between 0.0 and 1.0."
        )
        prop_a = agent_a.invoke(proposal_prompt, context=context)
        
        # Round 2: Agent B critiques with bounded peer context
        bounded_proposal = _bound_text(prop_a.content, max_chars=1800)
        emit("deliberation_step", {"step": f"{agent_b.name} is analyzing proposal tradeoffs..."})
        crit_context = ContextSnapshot(
            permanent_context=context.permanent_context if context else "",
            current_context=context.current_context if context else "",
            recent_context=context.recent_context if context else "",
            peer_context=f"PROPOSAL FROM {agent_a.name}:\n{bounded_proposal}",
        )
        crit_prompt = (
            f"{prompt}\n\n"
            f"Critique the proposal from {agent_a.name}.\n"
            "RESPONSE CONTRACT (Keep concise, max 250 words):\n"
            "- **Agreement**: Agree or Disagree.\n"
            "- **Key Flaws**: Top 1-2 edge cases, flaws, or gaps.\n"
            "- **Improvements**: Specific actionable recommendations.\n"
            "- **Blocking Concerns**: Any showstoppers or 'None'.\n"
            "- **Recommendation**: Concrete next action."
        )
        crit_b = agent_b.invoke(crit_prompt, context=crit_context)

        # Round 3: Synthesis with bounded inputs
        bounded_critique = _bound_text(crit_b.content, max_chars=1200)
        emit("deliberation_step", {"step": f"Synthesizing refined solution..."})
        synth_context = ContextSnapshot(
            permanent_context=context.permanent_context if context else "",
            current_context=context.current_context if context else "",
            recent_context=context.recent_context if context else "",
            peer_context=f"INITIAL PROPOSAL:\n{bounded_proposal}\n\nCRITIQUE:\n{bounded_critique}",
        )
        synth_prompt = (
            f"{prompt}\n\n"
            "RESPONSE CONTRACT (Synthesize the definitive solution, max 400 words):\n"
            "- **Selected Approach**: Definitive architecture consensus.\n"
            "- **Adjustments Made**: Changes incorporated from critique.\n"
            "- **Implementation Steps**: 3-5 concrete phases.\n"
            "- **Unresolved Risks**: Any residual risks."
        )
        synth_resp = agent_a.invoke(synth_prompt, context=synth_context)

        stage_metrics = [
            {
                "stage": "Antigravity proposal" if "antigravity" in agent_a.name.lower() else f"{agent_a.name} proposal",
                "provider": agent_a.name,
                "role": "proposer",
                "duration_ms": prop_a.duration_ms,
                "input_tokens": prop_a.input_tokens,
                "output_tokens": prop_a.output_tokens,
            },
            {
                "stage": "Codex critique" if "codex" in agent_b.name.lower() else f"{agent_b.name} critique",
                "provider": agent_b.name,
                "role": "critic",
                "duration_ms": crit_b.duration_ms,
                "input_tokens": crit_b.input_tokens,
                "output_tokens": crit_b.output_tokens,
            },
            {
                "stage": "Antigravity synthesis" if "antigravity" in agent_a.name.lower() else f"{agent_a.name} synthesis",
                "provider": agent_a.name,
                "role": "synthesizer",
                "duration_ms": synth_resp.duration_ms,
                "input_tokens": synth_resp.input_tokens,
                "output_tokens": synth_resp.output_tokens,
            },
        ]
        total_in = _sum_tokens([prop_a.input_tokens, crit_b.input_tokens, synth_resp.input_tokens])
        total_out = _sum_tokens([prop_a.output_tokens, crit_b.output_tokens, synth_resp.output_tokens])
        total_duration = prop_a.duration_ms + crit_b.duration_ms + synth_resp.duration_ms

        return DeliberationResult(
            strategy_used=StrategyType.PROPOSE_CRITIQUE_REFINE.value,
            synthesized_output=synth_resp.content,
            proposals=[
                Proposal(
                    agent_name=agent_a.name,
                    summary="Initial Proposal",
                    content=prop_a.content,
                    duration_ms=prop_a.duration_ms,
                    input_tokens=prop_a.input_tokens,
                    output_tokens=prop_a.output_tokens,
                )
            ],
            critiques=[
                Critique(
                    reviewer_agent=agent_b.name,
                    target_agent=agent_a.name,
                    content=crit_b.content,
                    duration_ms=crit_b.duration_ms,
                    input_tokens=crit_b.input_tokens,
                    output_tokens=crit_b.output_tokens,
                )
            ],
            participating_providers=[agent_a.name, agent_b.name],
            rounds_executed=3,
            total_input_tokens=total_in,
            total_output_tokens=total_out,
            duration_ms=total_duration,
            stage_metrics=stage_metrics,
        )

    def _run_independent_investigation(
        self,
        task: Task,
        agent_a: AgentProvider,
        agent_b: AgentProvider,
        context: Optional[ContextSnapshot],
        emit: EventCallback,
    ) -> DeliberationResult:
        task_desc = f"\n{task.description}" if task.description else ""
        prompt = (
            f"Investigate the following issue independently:\n{task.title}{task_desc}\n\n"
            "RESPONSE CONTRACT (Keep concise, max 300 words):\n"
            "- **Hypothesis**: Root cause identification.\n"
            "- **Evidence/Mechanism**: How the bug occurs.\n"
            "- **Recommended Fix**: Concrete resolution."
        )
        
        emit("deliberation_step", {"step": f"{agent_a.name} is developing Hypothesis A..."})
        res_a = agent_a.invoke(prompt, context=context)
        
        emit("deliberation_step", {"step": f"{agent_b.name} is developing Hypothesis B..."})
        res_b = agent_b.invoke(prompt, context=context)

        # Cross-synthesize findings
        emit("deliberation_step", {"step": "Reconciling independent hypotheses..."})
        bounded_a = _bound_text(res_a.content, max_chars=1200)
        bounded_b = _bound_text(res_b.content, max_chars=1200)
        synth_context = ContextSnapshot(
            permanent_context=context.permanent_context if context else "",
            current_context=context.current_context if context else "",
            recent_context=context.recent_context if context else "",
            peer_context=f"HYPOTHESIS A ({agent_a.name}):\n{bounded_a}\n\nHYPOTHESIS B ({agent_b.name}):\n{bounded_b}",
        )
        synth_prompt = (
            f"Issue: {task.title}\n\n"
            "RESPONSE CONTRACT (Synthesize reconciled diagnosis, max 350 words):\n"
            "- **Reconciled Root Cause**: Consensus on bug cause.\n"
            "- **Key Differences**: How the two analyses differed.\n"
            "- **Definitive Fix**: Concrete fix steps."
        )
        synth_resp = agent_a.invoke(synth_prompt, context=synth_context)
        stage_metrics = [
            {
                "stage": f"{agent_a.name} hypothesis",
                "provider": agent_a.name,
                "role": "investigator_a",
                "duration_ms": res_a.duration_ms,
                "input_tokens": res_a.input_tokens,
                "output_tokens": res_a.output_tokens,
            },
            {
                "stage": f"{agent_b.name} hypothesis",
                "provider": agent_b.name,
                "role": "investigator_b",
                "duration_ms": res_b.duration_ms,
                "input_tokens": res_b.input_tokens,
                "output_tokens": res_b.output_tokens,
            },
            {
                "stage": f"{agent_a.name} reconciliation",
                "provider": agent_a.name,
                "role": "synthesizer",
                "duration_ms": synth_resp.duration_ms,
                "input_tokens": synth_resp.input_tokens,
                "output_tokens": synth_resp.output_tokens,
            },
        ]
        total_in = _sum_tokens([res_a.input_tokens, res_b.input_tokens, synth_resp.input_tokens])
        total_out = _sum_tokens([res_a.output_tokens, res_b.output_tokens, synth_resp.output_tokens])
        total_duration = res_a.duration_ms + res_b.duration_ms + synth_resp.duration_ms

        return DeliberationResult(
            strategy_used=StrategyType.INDEPENDENT_INVESTIGATION.value,
            synthesized_output=synth_resp.content,
            proposals=[
                Proposal(
                    agent_name=agent_a.name,
                    summary="Hypothesis A",
                    content=res_a.content,
                    duration_ms=res_a.duration_ms,
                    input_tokens=res_a.input_tokens,
                    output_tokens=res_a.output_tokens,
                ),
                Proposal(
                    agent_name=agent_b.name,
                    summary="Hypothesis B",
                    content=res_b.content,
                    duration_ms=res_b.duration_ms,
                    input_tokens=res_b.input_tokens,
                    output_tokens=res_b.output_tokens,
                ),
            ],
            participating_providers=[agent_a.name, agent_b.name],
            rounds_executed=2,
            total_input_tokens=total_in,
            total_output_tokens=total_out,
            duration_ms=total_duration,
            stage_metrics=stage_metrics,
        )
