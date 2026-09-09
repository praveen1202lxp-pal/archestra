"""The primary orchestrator coordinating providers, routing, deliberation, and shared memory."""

import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from fusion_agent.config.schema import FusionConfig
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
        primary_agent: AgentProvider,
        secondary_agent: Optional[AgentProvider],
        context: Optional[Any],
        emit: Callable,
    ) -> Tuple[DeliberationResult, WorkspaceSession, VerificationResult, str, Optional[ReviewResult]]:
        """Execute safe repository editing loop within an isolated worktree."""
        repo_path = Path(self.config.project_root).resolve()
        session = WorkspaceSession(task_id=task.id, repo_root=repo_path)

        # 1. Pre-flight check & worktree allocation
        emit("status", {"message": "Pre-flight check: verifying repository working tree is clean..."})
        session.prepare()
        emit("status", {"message": f"Allocated isolated worktree on task branch '{session.task_branch}'."})

        broker = ExecutionBroker(session)
        verifier = WorkspaceVerifier()

        # 2. Structured patch formulation from primary agent
        emit("status", {"message": f"{primary_agent.name} is formulating structured code modifications..."})
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
        prop_resp = primary_agent.invoke(patch_prompt, context=context)

        # 3. Apply edits strictly via ExecutionBroker
        modified_files = WorkspaceEditor.apply_edits(prop_resp.content, broker)
        if modified_files:
            emit("status", {"message": f"Applied modifications to {len(modified_files)} file(s): {', '.join(modified_files)}"})
        else:
            emit("status", {"message": "Notice: No structured file blocks detected in agent response."})

        # 4. Automated verification inside worktree
        emit("status", {"message": "Executing automated verification suite inside isolated worktree..."})
        verif_result = verifier.run_tests(session, test_command=self.config.verification_command, broker=broker)

        # 5. Automated repair turn if verification failed
        if not verif_result.passed and self.config.deliberation.max_rounds > 1:
            emit("status", {"message": f"Verification failed (code {verif_result.exit_code}). Primary agent is attempting repair..."})
            repair_prompt = (
                f"Previous implementation attempt failed automated verification.\n"
                f"Command: {verif_result.command}\n"
                f"Stderr:\n{verif_result.stderr}\n"
                f"Stdout:\n{verif_result.stdout}\n\n"
                "Please fix the issues and provide the updated complete file(s) formatted as:\n"
                "### File: relative/path/to/file.ext\n"
                "```language\n"
                "updated file content\n"
                "```"
            )
            repair_resp = primary_agent.invoke(repair_prompt, context=context)
            repaired_files = WorkspaceEditor.apply_edits(repair_resp.content, broker)
            if repaired_files:
                emit("status", {"message": f"Applied repairs to: {', '.join(repaired_files)}"})
            verif_result = verifier.run_tests(session, test_command=self.config.verification_command, broker=broker)

        # 6. Extract unified diff
        diff = verifier.get_diff(session)

        # 7. Peer review diff
        review_result = None
        if secondary_agent:
            emit("status", {"message": f"{secondary_agent.name} is conducting cross-model diff review..."})
            review_resp = secondary_agent.review(
                content=diff if diff else "No changes detected in worktree.",
                criteria="Evaluate git diff for correctness, security, syntax, and regression risks.",
                context=context,
            )
            review_result = ReviewResult(
                reviewer_agent=secondary_agent.name,
                subject_agent=primary_agent.name,
                status=review_resp.status,
                comments=review_resp.comments,
                suggested_fixes=review_resp.suggested_fixes,
                duration_ms=review_resp.duration_ms,
                input_tokens=review_resp.input_tokens,
                output_tokens=review_resp.output_tokens,
            )
        else:
            review_result = ReviewResult(
                reviewer_agent="System",
                subject_agent=primary_agent.name,
                status=ReviewStatus.APPROVED,
                comments="No secondary reviewer configured.",
            )

        proposals = [
            Proposal(
                agent_name=primary_agent.name,
                summary="Structured code modifications",
                content=prop_resp.content,
                duration_ms=prop_resp.duration_ms,
                input_tokens=prop_resp.input_tokens,
                output_tokens=prop_resp.output_tokens,
            )
        ]

        summary_msg = (
            f"Autonomous edit completed in isolated worktree ({session.task_branch}).\n"
            f"Files modified: {', '.join(modified_files) if modified_files else 'None'}\n"
            f"Automated tests: {'PASSED' if verif_result.passed else 'FAILED'}\n"
            f"Peer review: {review_result.status.value}"
        )

        deliberation = DeliberationResult(
            strategy_used=StrategyType.AUTONOMOUS_EDIT.value,
            synthesized_output=summary_msg,
            proposals=proposals,
            reviews=[review_result],
            participating_providers=[primary_agent.name] + ([secondary_agent.name] if secondary_agent else []),
            rounds_executed=1,
            total_input_tokens=(prop_resp.input_tokens or 0) + (review_result.input_tokens or 0),
            total_output_tokens=(prop_resp.output_tokens or 0) + (review_result.output_tokens or 0),
            duration_ms=prop_resp.duration_ms + review_result.duration_ms + (verif_result.duration_seconds * 1000.0),
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
        primary_agent = self.providers.get(routing.primary_provider)
        if not primary_agent:
            raise RuntimeError(f"Primary provider '{routing.primary_provider}' not found.")
        secondary_agent = self.providers.get(routing.secondary_provider) if routing.secondary_provider else None

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
                        primary_agent=primary_agent,
                        secondary_agent=secondary_agent,
                        context=context,
                        emit=emit,
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
        self.state_manager.update_task_status(task.id, TaskStatus.COMPLETED)
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
