"""The primary orchestrator coordinating providers, routing, deliberation, and shared memory."""

import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from fusion_agent.config.schema import FusionConfig
from fusion_agent.core.budget import TaskBudgetController
from fusion_agent.core.deliberation import DeliberationEngine
from fusion_agent.core.router import RoutingDecision, TaskRouter
from fusion_agent.memory.database import Database
from fusion_agent.memory.project_state import ProjectStateManager
from fusion_agent.models.deliberation import (
    DeliberationResult,
    Proposal,
    ReviewResult,
    ReviewStatus,
)
from fusion_agent.models.strategy import StrategyType
from fusion_agent.models.task import Task, TaskStatus
from fusion_agent.providers.base import AgentProvider
from fusion_agent.providers.registry import ProviderRegistry
from fusion_agent.workspace.broker import ExecutionBroker
from fusion_agent.workspace.editor import WorkspaceEditor
from fusion_agent.workspace.session import DirtyWorkingTreeError, WorkspaceSession, WorkspaceState
from fusion_agent.workspace.verifier import VerificationResult, WorkspaceVerifier


class OrchestratorResult:
    """Consolidated result of orchestrating a user task."""

    def __init__(
        self,
        task: Task,
        routing: RoutingDecision,
        deliberation: DeliberationResult,
        final_answer: str,
        context: Optional[Any] = None,
        workspace_session: Optional[WorkspaceSession] = None,
        verification_result: Optional[VerificationResult] = None,
        diff: Optional[str] = None,
        review_result: Optional[ReviewResult] = None,
    ):
        self.task = task
        self.routing = routing
        self.deliberation = deliberation
        self.final_answer = final_answer
        self.context = context
        self.workspace_session = workspace_session
        self.verification_result = verification_result
        self.diff = diff
        self.review_result = review_result


class FusionOrchestrator:
    """Coordinates state, routing, multi-model deliberation, and persistence."""

    def __init__(
        self,
        config: FusionConfig,
        database: Optional[Database] = None,
        providers: Optional[Dict[str, AgentProvider]] = None,
    ):
        self.config = config
        self.db = database or Database(f"{config.storage_dir}/fusion.db")
        self.state_manager = ProjectStateManager(self.db)
        self.router = TaskRouter()
        self.deliberation_engine = DeliberationEngine(config.deliberation)

        # Initialize or discover providers
        self.providers: Dict[str, AgentProvider] = providers if providers is not None else {}
        if not self.providers:
            for name, agent_cfg in config.agents.items():
                try:
                    provider = ProviderRegistry.create(
                        name=agent_cfg.provider_name,
                        provider_type=agent_cfg.provider_type,
                        config=agent_cfg.to_dict(),
                    )
                    self.providers[name] = provider
                except Exception:
                    pass

        # Ensure project exists in state database
        project_id = config.project_name.lower().replace(" ", "-")
        self.project = self.state_manager.get_or_create_project(
            project_id=project_id,
            name=config.project_name,
            root_path=config.project_root,
        )

    def _run_autonomous_edit(
        self,
        task: Task,
        primary_agent: Optional[AgentProvider] = None,
        secondary_agent: Optional[AgentProvider] = None,
        context: Optional[Any] = None,
        emit: Optional[Callable] = None,
        implementer: Optional[AgentProvider] = None,
        reviewer: Optional[AgentProvider] = None,
        budget: Optional[TaskBudgetController] = None,
    ) -> Tuple[DeliberationResult, WorkspaceSession, VerificationResult, str, Optional[ReviewResult]]:
        """Execute safe repository editing loop within an isolated worktree."""
        impl = implementer or primary_agent
        if not impl:
            raise ValueError("An implementer provider must be specified for autonomous edit.")
        rev = reviewer if reviewer is not None else secondary_agent

        if emit is None:
            emit = lambda event, data: None
        if budget is None:
            budget = TaskBudgetController(self.config.deliberation)

        repo_path = Path(self.config.project_root).resolve()
        session = WorkspaceSession(task_id=task.id, repo_root=repo_path)

        # 1. Pre-flight check & worktree allocation
        emit("status", {"message": "Pre-flight check: verifying repository working tree is clean..."})
        session.prepare()
        emit("status", {"message": f"Allocated isolated worktree on task branch '{session.task_branch}'."})

        broker = ExecutionBroker(session)
        verifier = WorkspaceVerifier()

        max_feedback_chars = getattr(self.config.deliberation, "max_feedback_chars", 2000)

        stage_metrics: List[Dict[str, Any]] = []
        proposals: List[Proposal] = []
        reviews: List[ReviewResult] = []

        # 2. Structured patch formulation from implementer (Initial Implementation)
        can_call, call_reason = budget.can_call_provider()
        if not can_call:
            raise RuntimeError(f"Budget ceiling reached before implementation: {call_reason}")

        emit("status", {"message": f"{impl.name} is formulating structured code modifications..."})
        patch_prompt = (
            f"Task: {task.title}\n{task.description}\n\n"
            "RESPONSE CONTRACT:\n"
            "You are an expert autonomous software engineer. Formulate the exact file additions and modifications.\n"
            "For each file to be created or modified, format strictly as:\n\n"
            "### File: relative/path/to/file.ext\n"
            "```language\n"
            "full file content here\n"
            "```\n\n"
            "Provide complete, valid, syntactically correct code. Do not use placeholder comments."
        )
        t_start = time.perf_counter()
        prop_resp = impl.invoke(patch_prompt, context=context)
        t_prop = (time.perf_counter() - t_start) * 1000.0
        budget.record_call(
            provider_name=impl.name,
            duration_ms=prop_resp.duration_ms or t_prop,
            input_tokens=prop_resp.input_tokens,
            output_tokens=prop_resp.output_tokens,
            stage="Initial Implementation",
        )

        proposals.append(
            Proposal(
                agent_name=impl.name,
                summary="Initial implementation patch",
                content=prop_resp.content,
                duration_ms=prop_resp.duration_ms or t_prop,
                input_tokens=prop_resp.input_tokens,
                output_tokens=prop_resp.output_tokens,
            )
        )
        stage_metrics.append({
            "stage": "Initial Implementation",
            "provider": impl.name,
            "role": "proposer",
            "duration_ms": prop_resp.duration_ms or t_prop,
            "input_tokens": prop_resp.input_tokens,
            "output_tokens": prop_resp.output_tokens,
        })

        # 3. Apply edits strictly via ExecutionBroker
        modified_files = WorkspaceEditor.apply_edits(prop_resp.content, broker)
        if modified_files:
            emit("status", {"message": f"Applied modifications to {len(modified_files)} file(s): {', '.join(modified_files)}"})
        else:
            emit("status", {"message": "Notice: No structured file blocks detected in agent response."})

        # 4. Automated verification inside worktree
        emit("status", {"message": "Executing automated verification suite inside isolated worktree..."})
        verif_result = verifier.run_tests(session, test_command=self.config.verification_command, broker=broker)

        # 5. Extract unified diff
        diff = verifier.get_diff(session)

        # 6. Peer review diff (Round 1)
        review_result = None
        can_rev, _ = budget.can_call_provider()
        if rev and can_rev:
            emit("status", {"message": f"{rev.name} is conducting peer diff review (Round 1)..."})
            rev_content = (
                f"### UNIFIED DIFF\n{diff if diff else 'No changes detected in worktree.'}\n\n"
                f"### VERIFICATION RESULT\n"
                f"Status: {'PASSED' if verif_result.passed else 'FAILED'}\n"
                f"Exit Code: {verif_result.exit_code}\n"
            )
            if verif_result.stderr:
                rev_content += f"Stderr:\n{verif_result.stderr[:1000]}\n"
            if verif_result.stdout:
                rev_content += f"Stdout:\n{verif_result.stdout[:1000]}\n"

            t_start = time.perf_counter()
            review_resp = rev.review(
                content=rev_content,
                criteria=(
                    "Evaluate git diff and test results thoroughly for correctness, security, syntax, edge cases, and regressions. "
                    "If any edge cases, missing validations, or unhandled exceptions are found, return [NEEDS_REVISION] with specific critique. "
                    "If the patch is clean, fully verified, and handles all edge cases, return [APPROVED]."
                ),
                context=context,
            )
            t_rev = (time.perf_counter() - t_start) * 1000.0
            budget.record_call(
                provider_name=rev.name,
                duration_ms=review_resp.duration_ms or t_rev,
                input_tokens=review_resp.input_tokens,
                output_tokens=review_resp.output_tokens,
                stage="Review Round 1",
            )

            review_result = ReviewResult(
                reviewer_agent=rev.name,
                subject_agent=impl.name,
                status=review_resp.status,
                comments=review_resp.comments,
                suggested_fixes=review_resp.suggested_fixes,
                duration_ms=review_resp.duration_ms or t_rev,
                input_tokens=review_resp.input_tokens,
                output_tokens=review_resp.output_tokens,
            )
            reviews.append(review_result)
            stage_metrics.append({
                "stage": "Review Round 1",
                "provider": rev.name,
                "role": "critic",
                "duration_ms": review_resp.duration_ms or t_rev,
                "input_tokens": review_resp.input_tokens,
                "output_tokens": review_resp.output_tokens,
            })
        else:
            review_result = ReviewResult(
                reviewer_agent="System",
                subject_agent=impl.name,
                status=ReviewStatus.APPROVED if verif_result.passed else ReviewStatus.NEEDS_REVISION,
                comments="Automated verification passed." if verif_result.passed else "Automated verification failed.",
            )
            reviews.append(review_result)

        # 7. Iterative Repair Loop (when revision needed or tests failed)
        repair_rounds = 0
        while (
            (review_result.status == ReviewStatus.NEEDS_REVISION or not verif_result.passed)
            and budget.can_attempt_repair(repair_rounds)[0]
        ):
            can_call, call_reason = budget.can_call_provider()
            if not can_call:
                emit("status", {"message": f"Budget ceiling reached ({call_reason}); halting repair loop."})
                break

            repair_rounds += 1
            budget.record_repair_round()
            bounded_critique = review_result.comments[:max_feedback_chars]
            emit("status", {"message": f"Peer review requested revision. {impl.name} is attempting targeted repair (Round {repair_rounds}/{budget.max_repair_rounds})..."})

            repair_prompt = (
                f"Task: {task.title}\n{task.description}\n\n"
                f"The previous implementation requires revision based on peer review and verification.\n\n"
                f"### PEER REVIEW CRITIQUE (Round {repair_rounds}):\n"
                f"{bounded_critique}\n\n"
                f"### VERIFICATION SUITE STATUS:\n"
                f"Passed: {verif_result.passed}\n"
                f"Command: {verif_result.command}\n"
            )
            if verif_result.stderr:
                repair_prompt += f"Stderr:\n{verif_result.stderr[:1000]}\n"
            if verif_result.stdout:
                repair_prompt += f"Stdout:\n{verif_result.stdout[:1000]}\n"
            repair_prompt += (
                f"\n### CURRENT UNIFIED DIFF:\n"
                f"{diff if diff else 'None'}\n\n"
                "RESPONSE CONTRACT:\n"
                "Address all reviewer critiques and test failures. Provide the complete updated files formatted strictly as:\n\n"
                "### File: relative/path/to/file.ext\n"
                "```language\n"
                "full updated file content\n"
                "```\n\n"
                "Provide complete, valid, syntactically correct code."
            )

            t_start = time.perf_counter()
            # Token containment: Pass context=None on repair rounds to avoid duplicating static architecture snapshot
            repair_resp = impl.invoke(repair_prompt, context=None)
            t_repair = (time.perf_counter() - t_start) * 1000.0
            budget.record_call(
                provider_name=impl.name,
                duration_ms=repair_resp.duration_ms or t_repair,
                input_tokens=repair_resp.input_tokens,
                output_tokens=repair_resp.output_tokens,
                stage=f"Repair Round {repair_rounds}",
            )

            proposals.append(
                Proposal(
                    agent_name=impl.name,
                    summary=f"Repair Round {repair_rounds}",
                    content=repair_resp.content,
                    duration_ms=repair_resp.duration_ms or t_repair,
                    input_tokens=repair_resp.input_tokens,
                    output_tokens=repair_resp.output_tokens,
                )
            )
            stage_metrics.append({
                "stage": f"Repair Round {repair_rounds}",
                "provider": impl.name,
                "role": "proposer",
                "duration_ms": repair_resp.duration_ms or t_repair,
                "input_tokens": repair_resp.input_tokens,
                "output_tokens": repair_resp.output_tokens,
            })

            repaired_files = WorkspaceEditor.apply_edits(repair_resp.content, broker)
            if repaired_files:
                emit("status", {"message": f"Applied repairs to {len(repaired_files)} file(s): {', '.join(repaired_files)}"})
                for rf in repaired_files:
                    if rf not in modified_files:
                        modified_files.append(rf)

            emit("status", {"message": f"Re-executing verification suite (Round {repair_rounds})..."})
            verif_result = verifier.run_tests(session, test_command=self.config.verification_command, broker=broker)
            diff = verifier.get_diff(session)

            can_rev, _ = budget.can_call_provider()
            if rev and can_rev:
                review_round_num = repair_rounds + 1
                emit("status", {"message": f"{rev.name} is re-reviewing updated diff (Round {review_round_num})..."})
                rev_content = (
                    f"### UNIFIED DIFF (After Repair Round {repair_rounds})\n{diff if diff else 'No changes detected in worktree.'}\n\n"
                    f"### VERIFICATION RESULT\n"
                    f"Status: {'PASSED' if verif_result.passed else 'FAILED'}\n"
                    f"Exit Code: {verif_result.exit_code}\n"
                )
                if verif_result.stderr:
                    rev_content += f"Stderr:\n{verif_result.stderr[:1000]}\n"
                if verif_result.stdout:
                    rev_content += f"Stdout:\n{verif_result.stdout[:1000]}\n"

                t_start = time.perf_counter()
                review_resp = rev.review(
                    content=rev_content,
                    criteria=(
                        "Evaluate git diff and test results thoroughly for correctness, security, syntax, edge cases, and regressions. "
                        "If the repairs successfully resolved the previous critiques, return [APPROVED]. "
                        "If critical issues remain, return [NEEDS_REVISION]."
                    ),
                    context=context,
                )
                t_rev = (time.perf_counter() - t_start) * 1000.0
                budget.record_call(
                    provider_name=rev.name,
                    duration_ms=review_resp.duration_ms or t_rev,
                    input_tokens=review_resp.input_tokens,
                    output_tokens=review_resp.output_tokens,
                    stage=f"Review Round {review_round_num}",
                )

                review_result = ReviewResult(
                    reviewer_agent=rev.name,
                    subject_agent=impl.name,
                    status=review_resp.status,
                    comments=review_resp.comments,
                    suggested_fixes=review_resp.suggested_fixes,
                    duration_ms=review_resp.duration_ms or t_rev,
                    input_tokens=review_resp.input_tokens,
                    output_tokens=review_resp.output_tokens,
                )
                reviews.append(review_result)
                stage_metrics.append({
                    "stage": f"Review Round {review_round_num}",
                    "provider": rev.name,
                    "role": "critic",
                    "duration_ms": review_resp.duration_ms or t_rev,
                    "input_tokens": review_resp.input_tokens,
                    "output_tokens": review_resp.output_tokens,
                })
            elif not rev:
                review_result = ReviewResult(
                    reviewer_agent="System",
                    subject_agent=impl.name,
                    status=ReviewStatus.APPROVED if verif_result.passed else ReviewStatus.NEEDS_REVISION,
                    comments="Automated verification passed." if verif_result.passed else "Automated verification failed.",
                )
                reviews.append(review_result)

        summary_msg = (
            f"Autonomous edit completed in isolated worktree ({session.task_branch}).\n"
            f"Files modified: {', '.join(modified_files) if modified_files else 'None'}\n"
            f"Repair rounds executed: {repair_rounds}\n"
            f"Automated tests: {'PASSED' if verif_result.passed else 'FAILED'}\n"
            f"Peer review: {review_result.status.value} by {review_result.reviewer_agent}"
        )

        has_any_tok = any(sm.get("input_tokens") is not None for sm in stage_metrics)
        tot_in = sum(sm["input_tokens"] for sm in stage_metrics if sm.get("input_tokens") is not None) if has_any_tok else None
        tot_out = sum(sm["output_tokens"] for sm in stage_metrics if sm.get("output_tokens") is not None) if has_any_tok else None
        tot_duration = sum(sm["duration_ms"] for sm in stage_metrics)

        deliberation = DeliberationResult(
            strategy_used=StrategyType.AUTONOMOUS_EDIT.value,
            synthesized_output=summary_msg,
            proposals=proposals,
            reviews=reviews,
            participating_providers=[impl.name] + ([rev.name] if rev else []),
            rounds_executed=repair_rounds + 1,
            total_input_tokens=tot_in,
            total_output_tokens=tot_out,
            duration_ms=tot_duration,
            stage_metrics=stage_metrics,
        )

        return deliberation, session, verif_result, diff, review_result

    def run_task(
        self,
        user_prompt: str,
        on_status: Optional[Callable] = None,
    ) -> OrchestratorResult:
        """Run an end-to-end task through the orchestrator."""
        def emit(event_type: str, data: Dict[str, Any]):
            if on_status:
                try:
                    on_status(event_type, data)
                except TypeError:
                    on_status({"event": event_type, **data})

        # 1. Deterministic Task Routing
        emit("status", {"message": "Analyzing task complexity and routing..."})
        routing = self.router.route(
            task_prompt=user_prompt,
            available_providers=self.providers,
            optimization_mode=self.config.optimization_mode,
        )
        emit("routing_decision", {
            "strategy": routing.strategy.value,
            "primary": routing.primary_provider,
            "secondary": routing.secondary_provider,
            "rationale": routing.rationale,
            "task_assessment": routing.task_assessment.to_dict() if routing.task_assessment else None,
            "role_assignments": routing.role_assignments,
            "scoring_breakdown": routing.scoring_breakdown,
        })

        # 2. Record Task in SQLite
        task = self.state_manager.create_task(
            project_id=self.project["id"],
            title=user_prompt[:100],
            description=user_prompt,
            task_type=routing.task_type,
            complexity=routing.complexity,
            selected_strategy=routing.strategy.value,
        )
        self.state_manager.update_task_status(task.id, TaskStatus.IN_PROGRESS)

        # 3. Construct 3-Tier Context Snapshot
        context = self.state_manager.build_context_snapshot(self.project["id"], current_task=task)

        # 4. Resolve Providers & Health Verification
        budget = TaskBudgetController(self.config.deliberation)

        impl_key = routing.role_assignments.get("implementer") or routing.primary_provider
        rev_key = routing.role_assignments.get("reviewer") or routing.secondary_provider
        lead_key = routing.role_assignments.get("lead") or routing.primary_provider

        implementer_agent = self.providers.get(impl_key)
        reviewer_agent = self.providers.get(rev_key) if rev_key else None
        lead_agent = self.providers.get(lead_key)

        primary_agent = implementer_agent if routing.strategy == StrategyType.AUTONOMOUS_EDIT else lead_agent
        secondary_agent = reviewer_agent

        if not primary_agent:
            raise RuntimeError(f"Primary provider '{impl_key if routing.strategy == StrategyType.AUTONOMOUS_EDIT else lead_key}' not found.")

        active_strategy = routing.strategy
        health_primary = primary_agent.health_check()

        if not health_primary.healthy:
            emit("status", {"message": f"Primary provider '{primary_agent.name}' is unavailable: {health_primary.message}"})
            if secondary_agent and secondary_agent.health_check().healthy:
                emit("status", {"message": f"Gracefully falling back to available provider '{secondary_agent.name}'..."})
                primary_agent = secondary_agent
                secondary_agent = None
                active_strategy = StrategyType.DIRECT
            else:
                self.state_manager.update_task_status(task.id, TaskStatus.FAILED)
                task.status = TaskStatus.FAILED
                err_msg = (
                    f"Provider '{primary_agent.name}' is unavailable:\n{health_primary.message}\n"
                    "No alternative healthy provider is configured to handle this task."
                )
                return OrchestratorResult(
                    task=task,
                    routing=routing,
                    deliberation=DeliberationResult(
                        strategy_used="FAILED",
                        synthesized_output=err_msg,
                        participating_providers=[primary_agent.name],
                    ),
                    final_answer=err_msg,
                )

        # 5. Execute Multi-Agent Deliberation or Autonomous Edit
        workspace_session = None
        verification_result = None
        diff = None
        review_result = None

        if active_strategy == StrategyType.AUTONOMOUS_EDIT:
            emit("status", {"message": f"Executing strategy: {active_strategy.value}..."})
            try:
                deliberation, workspace_session, verification_result, diff, review_result = (
                    self._run_autonomous_edit(
                        task=task,
                        implementer=implementer_agent,
                        reviewer=reviewer_agent,
                        context=context,
                        emit=emit,
                        budget=budget,
                    )
                )
            except DirtyWorkingTreeError as exc:
                self.state_manager.update_task_status(task.id, TaskStatus.FAILED)
                task.status = TaskStatus.FAILED
                err_msg = str(exc)
                emit("status", {"message": f"Refusal: {err_msg}"})
                return OrchestratorResult(
                    task=task,
                    routing=routing,
                    deliberation=DeliberationResult(
                        strategy_used="FAILED",
                        synthesized_output=err_msg,
                        participating_providers=[primary_agent.name],
                    ),
                    final_answer=err_msg,
                )
            except Exception as exc:
                try:
                    WorkspaceSession(task_id=task.id, repo_root=Path(self.config.project_root).resolve()).teardown(delete_branch=True)
                except Exception:
                    pass
                self.state_manager.update_task_status(task.id, TaskStatus.FAILED)
                task.status = TaskStatus.FAILED
                err_msg = f"Autonomous edit failed: {exc}"
                emit("status", {"message": f"Error: {exc}"})
                return OrchestratorResult(
                    task=task,
                    routing=routing,
                    deliberation=DeliberationResult(
                        strategy_used="FAILED",
                        synthesized_output=err_msg,
                        participating_providers=[primary_agent.name],
                    ),
                    final_answer=err_msg,
                )
        else:
            emit("status", {"message": f"Executing strategy: {active_strategy.value}..."})
            try:
                deliberation = self.deliberation_engine.run(
                    strategy=active_strategy,
                    task=task,
                    primary_agent=primary_agent,
                    secondary_agent=secondary_agent,
                    context=context,
                    on_event=on_status,
                )
            except Exception as exc:
                self.state_manager.update_task_status(task.id, TaskStatus.FAILED)
                task.status = TaskStatus.FAILED
                err_msg = f"Task execution halted during deliberation: {exc}"
                emit("status", {"message": f"Error: {exc}"})
                return OrchestratorResult(
                    task=task,
                    routing=routing,
                    deliberation=DeliberationResult(
                        strategy_used="FAILED",
                        synthesized_output=err_msg,
                        participating_providers=[primary_agent.name],
                    ),
                    final_answer=err_msg,
                )

        # 6. Persist Agent Runs, Reviews, and Decisions
        for prop in deliberation.proposals:
            self.state_manager.record_agent_run(
                task_id=task.id,
                provider_name=prop.agent_name,
                role="proposer",
                response_content=prop.content,
                prompt_summary=prop.summary,
                input_tokens=prop.input_tokens or 0,
                output_tokens=prop.output_tokens or 0,
                duration_ms=prop.duration_ms,
            )

        for crit in deliberation.critiques:
            self.state_manager.record_agent_run(
                task_id=task.id,
                provider_name=crit.reviewer_agent,
                role="critic",
                response_content=crit.content,
                prompt_summary=f"Critique for {crit.target_agent}",
                input_tokens=crit.input_tokens or 0,
                output_tokens=crit.output_tokens or 0,
                duration_ms=crit.duration_ms,
            )

        # Record synthesis run if present in stage_metrics
        for sm in getattr(deliberation, "stage_metrics", []):
            if sm.get("role") == "synthesizer":
                self.state_manager.record_agent_run(
                    task_id=task.id,
                    provider_name=sm["provider"],
                    role="synthesizer",
                    response_content=deliberation.synthesized_output,
                    prompt_summary=f"Consolidated synthesis by {sm['provider']}",
                    input_tokens=sm["input_tokens"] or 0,
                    output_tokens=sm["output_tokens"] or 0,
                    duration_ms=sm["duration_ms"],
                )

        for rev in deliberation.reviews:
            self.state_manager.record_review(
                task_id=task.id,
                reviewer_provider=rev.reviewer_agent,
                subject_agent=rev.subject_agent,
                status=rev.status,
                comments=rev.comments,
            )
            if active_strategy == StrategyType.AUTONOMOUS_EDIT:
                self.state_manager.record_agent_run(
                    task_id=task.id,
                    provider_name=rev.reviewer_agent,
                    role="critic",
                    response_content=rev.comments,
                    prompt_summary=f"Diff Review ({rev.status.value})",
                    input_tokens=rev.input_tokens or 0,
                    output_tokens=rev.output_tokens or 0,
                    duration_ms=rev.duration_ms,
                )

        # Auto-record design decision if strategy involved deliberation or autonomous edit
        if len(deliberation.proposals) > 0 or len(deliberation.critiques) > 0:
            self.state_manager.record_decision(
                project_id=self.project["id"],
                task_id=task.id,
                title=task.title,
                decision=deliberation.synthesized_output[:200] + "...",
                rationale=routing.rationale,
                agent_source="Fusion Agent Orchestrator",
            )

        # 7. Update Task Status
        task.status = TaskStatus.COMPLETED
        self.state_manager.update_task_status(
            task.id,
            TaskStatus.COMPLETED,
            verification_passed=(verification_result.passed if verification_result else True),
            repair_rounds=getattr(deliberation, "rounds_executed", 1) - 1 if deliberation else 0,
        )
        emit("status", {"message": "Task completed successfully."})

        return OrchestratorResult(
            task=task,
            routing=routing,
            deliberation=deliberation,
            final_answer=deliberation.synthesized_output,
            context=context,
            workspace_session=workspace_session,
            verification_result=verification_result,
            diff=diff,
            review_result=review_result,
        )
