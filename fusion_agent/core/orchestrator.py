"""The primary orchestrator coordinating providers, routing, deliberation, and shared memory."""

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from fusion_agent.config.schema import FusionConfig
from fusion_agent.core.budget import TaskBudgetController
from fusion_agent.core.deliberation import DeliberationEngine
from fusion_agent.core.router import RoutingDecision, TaskRouter
from fusion_agent.memory.database import Database
from fusion_agent.memory.project_state import ProjectStateManager
from fusion_agent.memory.provider_stats import ProviderStatsTracker
from fusion_agent.core.planner import PlanEngine, PlanValidationError
from fusion_agent.models.context import CodeContext, ContextBudgetConfig, ContextExpansionRequest
from fusion_agent.models.deliberation import (
    DeliberationResult,
    Proposal,
    ReviewResult,
    ReviewStatus,
)
from fusion_agent.models.plan import (
    Checkpoint,
    ExecutionPlan,
    PlanStep,
    PlanStatus,
    StepResult,
    StepStatus,
    VerificationType,
)
from fusion_agent.models.strategy import StrategyType
from fusion_agent.models.task import Complexity, Task, TaskStatus, TaskType
from fusion_agent.providers.base import AgentProvider
from fusion_agent.providers.normalizer import StructuredOutputNormalizer
from fusion_agent.providers.registry import ProviderRegistry
from fusion_agent.repository.budgeter import ContextBudgeter
from fusion_agent.repository.ignore import SecretFilter
from fusion_agent.repository.indexer import RepositoryIndexer
from fusion_agent.repository.selector import RankedCandidate, RelevantFileSelector
from fusion_agent.workspace.broker import ExecutionBroker
from fusion_agent.workspace.checkpoint import (
    CheckpointManager,
    CheckpointRollbackError,
    UnexpectedFilesError,
)
from fusion_agent.workspace.editor import WorkspaceEditor

from fusion_agent.workspace.session import DirtyWorkingTreeError, WorkspaceSession, WorkspaceState
from fusion_agent.workspace.verifier import VerificationResult, WorkspaceVerifier
from fusion_agent.workspace.lock import TaskExecutionLock, TaskLockError
from fusion_agent.workspace.recovery import CheckpointRecoveryManager, RecoveryError, ReconciliationResult
from fusion_agent.workspace.promotion import PromotionEngine, PromotionResult, PromotionTransactionStatus
import json
import os
import socket

from fusion_agent.mcp.approval import MCPApprovalHandler
from fusion_agent.mcp.budgeter import ToolResultBudgeter
from fusion_agent.mcp.gateway import MCPGateway
from fusion_agent.mcp.models import (
    MCPEvidence,
    NormalizedProviderOutput,
    PolicyDecision,
    ProviderOutputType,
    ToolRequest,
    ToolResult,
)
from fusion_agent.mcp.policy import MCPPolicyEngine
from fusion_agent.mcp.registry import MCPServerRegistry


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
        mcp_registry: Optional[MCPServerRegistry] = None,
        mcp_gateway: Optional[MCPGateway] = None,
    ):
        self.config = config
        self.db = database or Database(f"{config.storage_dir}/fusion.db")
        self.state_manager = ProjectStateManager(self.db)
        self.router = TaskRouter()
        self.deliberation_engine = DeliberationEngine(config.deliberation)

        # Initialize MCP Registry and Gateway
        self.mcp_registry = mcp_registry or MCPServerRegistry()
        if hasattr(config, "mcp_servers") and config.mcp_servers:
            for s_id, s_cfg in config.mcp_servers.items():
                self.mcp_registry.register_server(s_cfg)
        self.mcp_gateway = mcp_gateway or MCPGateway(
            registry=self.mcp_registry,
            policy_engine=MCPPolicyEngine(self.config),
            budgeter=ToolResultBudgeter(max_chars_per_call=getattr(self.config.deliberation, "mcp_max_result_chars_per_call", 8000)),
            approval_handler=MCPApprovalHandler(self.config),
        )

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

    def _invoke_provider_stage_with_tools(
        self,
        provider: AgentProvider,
        prompt: str,
        code_context: CodeContext,
        task_id: str,
        stage_name: str,
        budget: TaskBudgetController,
        emit: Callable,
        plan_id: Optional[str] = None,
        step_id: Optional[str] = None,
        role: str = "implementer",
        db_stage: Optional[str] = None,
    ) -> Tuple[Any, CodeContext, List[Dict[str, Any]]]:
        """Execute a provider stage within the bounded tool-assisted loop.

        Evaluates provider output:
        - FINAL_RESPONSE: completes loop and returns response.
        - CONTEXT_INSUFFICIENT: breaks loop to allow context expansion.
        - TOOL_REQUEST: validates policy, budgets, and executes tool; reinvokes provider.
        """
        max_tool_turns = getattr(self.config.deliberation, "max_tool_turns_per_stage", 2)
        tool_turns = 0
        current_prompt = prompt
        current_context = code_context
        stage_metrics: List[Dict[str, Any]] = []
        last_resp = None

        while True:
            can_call, call_reason = budget.can_call_provider()
            if not can_call:
                if last_resp is not None:
                    return last_resp, current_context, stage_metrics
                raise RuntimeError(f"Budget ceiling reached in {stage_name}: {call_reason}")

            stage_run_id = self.state_manager.record_provider_stage_started(
                task_id=task_id,
                stage=db_stage or stage_name,
                provider=provider.name,
                role=role,
                plan_id=plan_id,
                step_id=step_id,
                round_number=tool_turns,
                prompt_summary=current_prompt[:200],
            )
            t_start = time.perf_counter()
            try:
                resp = provider.invoke(current_prompt, context=current_context)
            except Exception as exc:
                self.state_manager.fail_provider_stage(stage_run_id, error_message=str(exc))
                raise exc
            t_dur = (time.perf_counter() - t_start) * 1000.0

            ctx_tokens = current_context.metrics.get("fusion_context_tokens", 0) if hasattr(current_context, "metrics") and isinstance(current_context.metrics, dict) else 0
            budget.record_call(
                provider_name=provider.name,
                duration_ms=resp.duration_ms or t_dur,
                input_tokens=resp.input_tokens,
                output_tokens=resp.output_tokens,
                fusion_context_tokens=resp.fusion_context_tokens or ctx_tokens,
                reasoning_tokens=resp.reasoning_tokens,
                visible_output_tokens=resp.visible_output_tokens,
                cached_tokens=resp.cached_tokens,
                raw_usage=resp.metadata.get("usage") if hasattr(resp, "metadata") and resp.metadata else None,
                stage=stage_name if tool_turns == 0 else f"{stage_name} (turn {tool_turns})",
            )
            self.state_manager.complete_provider_stage(
                run_id=stage_run_id,
                response=resp,
                context=current_context,
                duration_ms=resp.duration_ms or t_dur,
                status="success",
            )
            stage_metrics.append({
                "stage": stage_name,
                "provider": provider.name,
                "role": role,
                "duration_ms": resp.duration_ms or t_dur,
                "input_tokens": resp.input_tokens,
                "output_tokens": resp.output_tokens,
            })
            last_resp = resp

            # Evaluate output via MCP Gateway normalizer
            norm = self.mcp_gateway.parse_provider_output(resp.content)
            if norm.output_type != ProviderOutputType.TOOL_REQUEST or not norm.tool_request:
                return resp, current_context, stage_metrics

            # Tool request received: check turn bound
            tool_req = norm.tool_request
            if tool_turns >= max_tool_turns:
                if tool_turns > max_tool_turns:
                    emit("status", {"message": f"Hard tool turn limit reached for {stage_name}; terminating tool loop."})
                    return resp, current_context, stage_metrics

                emit("status", {"message": f"Tool turn limit ({max_tool_turns}) reached for {stage_name}; prompting final answer."})
                current_prompt = (
                    f"{current_prompt}\n\n"
                    f"### Tool Execution Denied\n"
                    f"Maximum tool turns per stage ({max_tool_turns}) reached. Please provide your final structured engineering response without further tool requests."
                )
                tool_turns += 1
                continue

            emit("status", {"message": f"Executing MCP tool '{tool_req.tool_name}' on server '{tool_req.server_id}'..."})
            tool_res = self.mcp_gateway.execute_tool(
                request=tool_req,
                task_id=task_id,
                plan_id=plan_id,
                step_id=step_id,
                stage=stage_name,
                stage_calls_made=tool_turns,
                budget_controller=budget,
                state_manager=self.state_manager,
            )

            # Accumulate evidence
            evidence = MCPEvidence(
                server_id=tool_res.server_id,
                tool_name=tool_res.tool_name,
                content=tool_res.content if tool_res.success else "",
                duration_ms=tool_res.duration_ms,
                error=tool_res.error if not tool_res.success else None,
            )
            if hasattr(current_context, "mcp_evidence"):
                current_context.mcp_evidence.append(evidence)

            tool_feedback = (
                f"### Bounded Tool Result from {tool_res.server_id}/{tool_res.tool_name}:\n"
                f"{tool_res.content if tool_res.success else 'ERROR: ' + (tool_res.error or 'Tool execution failed')}\n"
            )
            if tool_res.is_truncated and tool_res.omission_reason:
                tool_feedback += f"\n[Result truncated: {tool_res.omission_reason}]\n"

            current_prompt = (
                f"{current_prompt}\n\n"
                f"{tool_feedback}\n"
                f"Please incorporate this external data and provide your final structured engineering patch or answer."
            )
            tool_turns += 1

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

        # 2. Structured patch formulation from implementer (Initial Implementation with Bounded CodeContext)
        can_call, call_reason = budget.can_call_provider()
        if not can_call:
            raise RuntimeError(f"Budget ceiling reached before implementation: {call_reason}")

        # Deterministic repository indexing and relevant-file selection
        emit("status", {"message": "Indexing repository and selecting relevant files deterministically..."})
        indexer = RepositoryIndexer()
        repo_index = indexer.index_project(repo_path)
        selector = RelevantFileSelector(repo_index)
        ranked_candidates, symbol_refs = selector.select_relevant_files(f"{task.title}\n{task.description}")

        budget_cfg = ContextBudgetConfig(
            max_files=getattr(self.config.deliberation, "context_max_files", 5),
            max_chars_per_file=getattr(self.config.deliberation, "context_max_chars_per_file", 8000),
            total_source_chars=getattr(self.config.deliberation, "context_total_source_chars", 24000),
            max_test_output_chars=getattr(self.config.deliberation, "context_max_test_output_chars", 2000),
            max_peer_feedback_chars=getattr(self.config.deliberation, "context_max_peer_feedback_chars", 2000),
            max_architecture_chars=getattr(self.config.deliberation, "context_max_architecture_chars", 1000),
            require_minimal_workspace=getattr(self.config.deliberation, "context_require_minimal_workspace", False),
        )
        budgeter = ContextBudgeter(budget_cfg)
        arch_summary = context.permanent_context if (context and hasattr(context, "permanent_context")) else ""
        code_context = budgeter.build_code_context(
            task_requirements=f"{task.title}\n{task.description}",
            ranked_candidates=ranked_candidates,
            symbol_refs=symbol_refs,
            index=repo_index,
            architecture_decisions=arch_summary,
        )

        selected_names = [sf.path for sf in code_context.selected_files]
        ctx_tokens = code_context.metrics.get("fusion_context_tokens", 0)
        emit("status", {"message": f"Constructed bounded CodeContext ({len(selected_names)} files: {', '.join(selected_names) if selected_names else 'None'}, ~{ctx_tokens} tokens)."})

        emit("status", {"message": f"{impl.name} is formulating structured code modifications..."})
        task_text = task.description if task.description == task.title or task.description.startswith(task.title) else f"{task.title}\n{task.description}"
        patch_prompt = (
            f"Task: {task_text.strip()}\n\n"
            "RESPONSE CONTRACT:\n"
            "Format file additions and modifications strictly as:\n"
            "### File: relative/path/to/file.ext\n"
            "```language\n"
            "full file content here\n"
            "```\n"
            "Provide complete, valid code without placeholder comments.\n"
            "If context is strictly insufficient, respond:\n"
            "CONTEXT_INSUFFICIENT\n- need_file: path/to/file.ext (reason: why needed)"
        )
        prop_resp, code_context, impl_metrics = self._invoke_provider_stage_with_tools(
            provider=impl,
            prompt=patch_prompt,
            code_context=code_context,
            task_id=task.id,
            stage_name="Initial Implementation",
            db_stage="step_implementation",
            budget=budget,
            emit=emit,
            role="implementer",
        )
        stage_metrics.extend(impl_metrics)
        t_prop = impl_metrics[-1]["duration_ms"] if impl_metrics else 0.0

        # Check for Context Expansion Request
        max_expansions = getattr(self.config.deliberation, "max_context_expansion_rounds", 1)
        max_exp_files = getattr(self.config.deliberation, "max_expansion_files", 2)
        expansion_req = StructuredOutputNormalizer.parse_context_expansion_request(prop_resp.content)
        if expansion_req and max_expansions > 0 and budget.can_call_provider()[0]:
            emit("status", {"message": f"{impl.name} requested context expansion: {expansion_req.requested_files or expansion_req.requested_symbols}. Validating..."})
            added_candidates = []
            for req_f in expansion_req.requested_files[:max_exp_files]:
                norm_f = req_f.replace("\\", "/").strip("./")
                is_secret, _ = SecretFilter.is_secret_or_sensitive(norm_f)
                if not is_secret and norm_f in repo_index.file_tree:
                    added_candidates.append(RankedCandidate(rel_path=norm_f, score=999.0, reasons=[f"Model requested expansion: {expansion_req.reason}"]))

            if added_candidates:
                expanded_candidates = added_candidates + [c for c in ranked_candidates if c.rel_path not in {a.rel_path for a in added_candidates}]
                code_context = budgeter.build_code_context(
                    task_requirements=f"{task.title}\n{task.description}",
                    ranked_candidates=expanded_candidates,
                    symbol_refs=symbol_refs,
                    index=repo_index,
                    architecture_decisions=arch_summary,
                )
                emit("status", {"message": f"Retrying {impl.name} with expanded context ({len(code_context.selected_files)} files)..."})
                prop_resp, code_context, retry_metrics = self._invoke_provider_stage_with_tools(
                    provider=impl,
                    prompt=patch_prompt,
                    code_context=code_context,
                    task_id=task.id,
                    stage_name="Initial Implementation",
                    db_stage="step_implementation",
                    budget=budget,
                    emit=emit,
                    role="implementer",
                )
                stage_metrics.extend(retry_metrics)
                t_prop = retry_metrics[-1]["duration_ms"] if retry_metrics else t_prop

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
        self.state_manager.record_verification(
            task_id=task.id,
            verification_type=VerificationType.STEP,
            command=verif_result.command or (self.config.verification_command or ""),
            exit_code=verif_result.exit_code,
            passed=verif_result.passed,
            duration_seconds=verif_result.duration_seconds,
            stdout=verif_result.stdout,
            stderr=verif_result.stderr,
        )

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
                fusion_context_tokens=review_resp.fusion_context_tokens or (len(rev_content) // 4),
                reasoning_tokens=review_resp.reasoning_tokens,
                visible_output_tokens=review_resp.visible_output_tokens,
                cached_tokens=review_resp.cached_tokens,
                raw_usage=review_resp.metadata.get("usage"),
                stage="Review Round 1",
            )
            self.state_manager.record_provider_stage(
                task_id=task.id,
                stage="final_review",
                provider=rev.name,
                role="reviewer",
                response=review_resp,
                round_number=1,
                context=context,
                duration_ms=review_resp.duration_ms or t_rev,
                status="approved" if review_resp.status == ReviewStatus.APPROVED else "needs_revision",
                prompt_summary=rev_content[:200],
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
                f"Task: {task_text.strip()}\n\n"
                f"Revision required based on peer review and verification:\n\n"
                f"### PEER REVIEW CRITIQUE (Round {repair_rounds}):\n"
                f"{bounded_critique}\n\n"
                f"### VERIFICATION SUITE STATUS:\n"
                f"Passed: {verif_result.passed} | Command: {verif_result.command}\n"
            )
            if verif_result.stderr:
                repair_prompt += f"Stderr:\n{verif_result.stderr[:800]}\n"
            if verif_result.stdout:
                repair_prompt += f"Stdout:\n{verif_result.stdout[:800]}\n"
            repair_prompt += (
                f"\n### CURRENT UNIFIED DIFF:\n"
                f"{diff if diff else 'None'}\n\n"
                "RESPONSE CONTRACT:\n"
                "Address all critiques. Format updated files strictly as:\n"
                "### File: relative/path/to/file.ext\n"
                "```language\n"
                "full updated file content\n"
                "```"
            )

            # Targeted repair context: extract post-patch file overrides from worktree
            worktree_overrides: Dict[str, str] = {}
            for mf in modified_files:
                mf_path = session.worktree_path / mf
                if mf_path.is_file():
                    try:
                        worktree_overrides[mf] = mf_path.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        pass

            repair_candidates = [
                RankedCandidate(rel_path=mf, score=100.0, reasons=["Modified file in worktree under targeted repair"])
                for mf in modified_files
                if (session.worktree_path / mf).is_file()
            ]

            test_fail_output = ""
            if verif_result.stderr:
                test_fail_output += f"Stderr:\n{verif_result.stderr}\n"
            if verif_result.stdout:
                test_fail_output += f"Stdout:\n{verif_result.stdout}\n"

            repair_code_context = budgeter.build_code_context(
                task_requirements=f"{task.title}\n{task.description}",
                ranked_candidates=repair_candidates,
                symbol_refs=[s for s in symbol_refs if s.file_path in modified_files],
                index=repo_index,
                architecture_decisions="",  # Token containment: omit broad architecture during repair
                test_failures=test_fail_output if not verif_result.passed else None,
                current_diff=diff,
                peer_feedback=bounded_critique,
                worktree_file_overrides=worktree_overrides,
            )
            repair_ctx_tokens = repair_code_context.metrics.get("fusion_context_tokens", 0)

            t_start = time.perf_counter()
            repair_resp = impl.invoke(repair_prompt, context=repair_code_context)
            t_repair = (time.perf_counter() - t_start) * 1000.0
            budget.record_call(
                provider_name=impl.name,
                duration_ms=repair_resp.duration_ms or t_repair,
                input_tokens=repair_resp.input_tokens,
                output_tokens=repair_resp.output_tokens,
                fusion_context_tokens=repair_resp.fusion_context_tokens or repair_ctx_tokens,
                reasoning_tokens=repair_resp.reasoning_tokens,
                visible_output_tokens=repair_resp.visible_output_tokens,
                cached_tokens=repair_resp.cached_tokens,
                raw_usage=repair_resp.metadata.get("usage"),
                stage=f"Repair Round {repair_rounds}",
            )
            self.state_manager.record_provider_stage(
                task_id=task.id,
                stage="final_repair",
                provider=impl.name,
                role="implementer",
                response=repair_resp,
                round_number=repair_rounds,
                context=repair_code_context,
                duration_ms=repair_resp.duration_ms or t_repair,
                status="success",
                prompt_summary=repair_prompt[:200],
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
            self.state_manager.record_verification(
                task_id=task.id,
                verification_type=VerificationType.POST_REPAIR,
                command=verif_result.command or (self.config.verification_command or ""),
                exit_code=verif_result.exit_code,
                passed=verif_result.passed,
                duration_seconds=verif_result.duration_seconds,
                stdout=verif_result.stdout,
                stderr=verif_result.stderr,
            )
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
                    fusion_context_tokens=review_resp.fusion_context_tokens or (len(rev_content) // 4),
                    reasoning_tokens=review_resp.reasoning_tokens,
                    visible_output_tokens=review_resp.visible_output_tokens,
                    cached_tokens=review_resp.cached_tokens,
                    raw_usage=review_resp.metadata.get("usage"),
                    stage=f"Review Round {review_round_num}",
                )
                self.state_manager.record_provider_stage(
                    task_id=task.id,
                    stage="final_rereview",
                    provider=rev.name,
                    role="reviewer",
                    response=review_resp,
                    round_number=review_round_num,
                    context=context,
                    duration_ms=review_resp.duration_ms or t_rev,
                    status="approved" if review_resp.status == ReviewStatus.APPROVED else "needs_revision",
                    prompt_summary=rev_content[:200],
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

    def _run_checkpointed_plan(
        self,
        task: Task,
        primary_agent: Optional[AgentProvider] = None,
        secondary_agent: Optional[AgentProvider] = None,
        context: Optional[Any] = None,
        emit: Optional[Callable] = None,
        lead: Optional[AgentProvider] = None,
        implementer: Optional[AgentProvider] = None,
        reviewer: Optional[AgentProvider] = None,
        budget: Optional[TaskBudgetController] = None,
        session: Optional[WorkspaceSession] = None,
        existing_plan: Optional[ExecutionPlan] = None,
        start_step_index: int = 0,
        last_verified_sha: Optional[str] = None,
        rerun_final_verification: bool = False,
    ) -> Tuple[DeliberationResult, WorkspaceSession, VerificationResult, str, Optional[ReviewResult]]:
        """Execute multi-step checkpointed execution plan inside an isolated worktree."""
        planner_agent = lead or primary_agent
        if not planner_agent:
            raise ValueError("A planning provider must be specified for checkpointed planning.")
        impl_agent = implementer or primary_agent
        if not impl_agent:
            raise ValueError("An implementer provider must be specified for checkpointed planning.")
        rev_agent = reviewer if reviewer is not None else secondary_agent

        if emit is None:
            emit = lambda event, data: None
        if budget is None:
            budget = TaskBudgetController(self.config.deliberation)

        repo_path = Path(self.config.project_root).resolve()
        if session is None:
            session = WorkspaceSession(task_id=task.id, repo_root=repo_path)
            # 1. Pre-flight check & worktree allocation
            emit("status", {"message": "Pre-flight check: verifying repository working tree is clean..."})
            session.prepare()
            emit("status", {"message": f"Allocated isolated worktree on task branch '{session.task_branch}' (base: {session.base_branch})."})
            self.state_manager.update_task_recovery_fields(task.id, base_commit=session.base_commit)

        broker = ExecutionBroker(session)
        verifier = WorkspaceVerifier()
        checkpoint_mgr = CheckpointManager()

        max_steps = getattr(self.config.deliberation, "max_plan_steps", 5)
        max_amendments = getattr(self.config.deliberation, "max_plan_amendments", 1)
        plan_engine = PlanEngine(max_steps=max_steps, max_amendments=max_amendments)

        budget_cfg = ContextBudgetConfig(
            max_files=getattr(self.config.deliberation, "context_max_files", 5),
            max_chars_per_file=getattr(self.config.deliberation, "context_max_chars_per_file", 8000),
            total_source_chars=getattr(self.config.deliberation, "context_total_source_chars", 24000),
            max_test_output_chars=getattr(self.config.deliberation, "context_max_test_output_chars", 2000),
            max_peer_feedback_chars=getattr(self.config.deliberation, "context_max_peer_feedback_chars", 2000),
            max_architecture_chars=getattr(self.config.deliberation, "context_max_architecture_chars", 1000),
            require_minimal_workspace=getattr(self.config.deliberation, "context_require_minimal_workspace", False),
        )
        budgeter = ContextBudgeter(budget_cfg)
        indexer = RepositoryIndexer()

        if existing_plan is not None:
            plan = existing_plan
            plan_code_context = context
        else:
            # 2. Build high-level planning context
            repo_index = indexer.index_project(session.worktree_path)
            selector = RelevantFileSelector(repo_index)
            ranked_candidates, symbol_refs = selector.select_relevant_files(f"{task.title}\n{task.description}")

            arch_summary = context.permanent_context if (context and hasattr(context, "permanent_context")) else ""
            plan_code_context = budgeter.build_code_context(
                task_requirements=f"{task.title}\n{task.description}",
                ranked_candidates=ranked_candidates,
                symbol_refs=symbol_refs,
                index=repo_index,
                architecture_decisions=arch_summary,
            )

            # 3. Plan Generation (Fail Closed on Error)
            try:
                plan = plan_engine.generate_plan(
                    task=task,
                    context=plan_code_context,
                    planner=planner_agent,
                    reviewer=rev_agent,
                    budget=budget,
                    state_manager=self.state_manager,
                    emit=emit,
                )
            except Exception as exc:
                has_chk = bool(self.state_manager.get_checkpoints_for_task(task.id))
                if not has_chk:
                    session.teardown(delete_branch=True)
                raise RuntimeError(f"Multi-step planning failed: {exc}")

            self.state_manager.create_plan(plan)
            self.state_manager.update_plan_status(plan.plan_id, PlanStatus.RUNNING)
            plan.status = PlanStatus.RUNNING
            self.state_manager.update_task_stage(task.id, "plan_generation")
            emit("status", {"message": f"Execution plan established: '{plan.title}' ({len(plan.steps)} steps)."})

        # 4. Step Execution Loop
        current_verified_sha = last_verified_sha or session.base_commit
        stage_metrics: List[Dict[str, Any]] = []
        proposals: List[Proposal] = []
        reviews: List[ReviewResult] = []
        completed_steps_info: List[str] = []
        all_modified_files: List[str] = []
        last_verif_result: Optional[VerificationResult] = None
        max_feedback_chars = getattr(self.config.deliberation, "max_feedback_chars", 2000)

        for prev_s in plan.steps[:start_step_index]:
            if prev_s.status == StepStatus.COMPLETED and prev_s.result:
                completed_steps_info.append(f"{prev_s.id} ({prev_s.objective}): {', '.join(prev_s.result.files_modified)}")
                for f in prev_s.result.files_modified:
                    if f not in all_modified_files:
                        all_modified_files.append(f)

        for step_idx in range(start_step_index, len(plan.steps)):
            step = plan.steps[step_idx]
            # Check DAG dependencies
            if not plan.is_step_runnable(step):
                step.status = StepStatus.SKIPPED
                self.state_manager.update_step_status(plan.plan_id, step.id, StepStatus.SKIPPED)
                emit("status", {"message": f"Step {step.id} skipped due to incomplete dependencies."})
                continue

            # Forward-looking budget reservation check
            remaining_count = len(plan.steps) - step_idx
            can_start, b_reason = budget.can_start_step(
                remaining_steps_count=remaining_count,
                requires_final_review=bool(rev_agent),
            )
            if not can_start:
                emit("status", {"message": f"Budget ceiling reached ({b_reason}); halting remaining steps."})
                step.status = StepStatus.SKIPPED
                self.state_manager.update_step_status(plan.plan_id, step.id, StepStatus.SKIPPED)
                self.state_manager.update_plan_status(plan.plan_id, PlanStatus.BUDGET_EXHAUSTED)
                plan.status = PlanStatus.BUDGET_EXHAUSTED
                self.state_manager.update_task_stage(task.id, "budget_exhausted")
                break

            step.status = StepStatus.IN_PROGRESS
            self.state_manager.update_step_status(plan.plan_id, step.id, StepStatus.IN_PROGRESS)
            self.state_manager.update_task_stage(task.id, f"step_{step.id}")
            emit("status", {"message": f"--- Step {step_idx+1}/{len(plan.steps)} ({step.id}): {step.objective} ---"})

            # Post-edit CodeContext built from current worktree
            step_index = indexer.index_project(session.worktree_path)
            step_selector = RelevantFileSelector(step_index)
            step_candidates, step_sym_refs = step_selector.select_relevant_files(
                f"{step.objective}\n{' '.join(step.expected_files)}\n{' '.join(step.expected_symbols)}"
            )

            # Prioritize expected files explicitly mentioned in the step
            for exp_f in step.expected_files:
                norm_exp = exp_f.replace("\\", "/").strip("./")
                if norm_exp in step_index.file_tree and not any(c.rel_path == norm_exp for c in step_candidates):
                    step_candidates.insert(
                        0,
                        RankedCandidate(
                            rel_path=norm_exp,
                            score=100.0,
                            reasons=["Explicitly expected file for current plan step"],
                        ),
                    )

            prior_summary = "\n".join(completed_steps_info) if completed_steps_info else "Initial step."
            if len(prior_summary) > 1000:
                prior_summary = prior_summary[:1000] + "..."

            step_code_context = budgeter.build_code_context(
                task_requirements=f"Step Objective: {step.objective}\nRationale: {step.rationale}",
                ranked_candidates=step_candidates,
                symbol_refs=step_sym_refs,
                index=step_index,
                architecture_decisions=prior_summary,
            )

            step_prompt = (
                f"Active Step: {step.id} - {step.objective}\n"
                f"Rationale: {step.rationale}\n"
                f"Overall Task: {task.title}\n\n"
                "RESPONSE CONTRACT:\n"
                "Format file additions and modifications strictly as:\n"
                "### File: relative/path/to/file.ext\n"
                "```language\n"
                "full file content here\n"
                "```\n"
                "Provide complete, valid code without placeholder comments."
            )

            step_resp, step_code_context, step_metrics = self._invoke_provider_stage_with_tools(
                provider=impl_agent,
                prompt=step_prompt,
                code_context=step_code_context,
                task_id=task.id,
                stage_name=f"Step {step.id} Implementation",
                db_stage="step_implementation",
                plan_id=plan.plan_id,
                step_id=step.id,
                budget=budget,
                emit=emit,
                role="implementer",
            )
            stage_metrics.extend(step_metrics)
            t_dur = step_metrics[-1]["duration_ms"] if step_metrics else 0.0

            proposals.append(
                Proposal(
                    agent_name=impl_agent.name,
                    summary=f"Implementation for {step.id}",
                    content=step_resp.content,
                    duration_ms=step_resp.duration_ms or t_dur,
                    input_tokens=step_resp.input_tokens,
                    output_tokens=step_resp.output_tokens,
                )
            )

            # Apply edits strictly via ExecutionBroker
            step_modified_files = WorkspaceEditor.apply_edits(step_resp.content, broker)
            for mf in step_modified_files:
                if mf not in all_modified_files:
                    all_modified_files.append(mf)

            # Step Verification with Sanitized Expectations as Data
            test_cmd = self.config.verification_command
            if step.verification_expectations:
                val_ok, val_err = plan_engine.validate_verification_expectation(
                    step.verification_expectations,
                    worktree_path=session.worktree_path,
                )
                if not val_ok:
                    if "does not exist in worktree" in (val_err or ""):
                        emit("status", {"message": f"Notice: Step test expectation '{step.verification_expectations}' not yet present in worktree; verifying syntax of modified files."})
                        py_files = [f for f in step_modified_files if f.endswith(".py")]
                        syntax_passed = True
                        syntax_err = ""
                        for pf in py_files:
                            retcode, out, err = broker.run_command(f"python -m py_compile {pf}")
                            if retcode != 0:
                                syntax_passed = False
                                syntax_err = err or out
                                break
                        step_verif = VerificationResult(
                            passed=syntax_passed,
                            exit_code=0 if syntax_passed else 1,
                            stdout="Step syntax verification passed." if syntax_passed else "",
                            stderr=syntax_err,
                            duration_seconds=0.1,
                            command="py_compile",
                        )
                    else:
                        emit("status", {"message": f"Security Notice: Rejected unsafe verification expectation: {val_err}"})
                        step_verif = VerificationResult(
                            passed=False,
                            exit_code=-1,
                            stdout="",
                            stderr=f"Security Policy: Rejected verification expectation: {val_err}",
                            duration_seconds=0.0,
                            command="",
                        )
                else:
                    trusted_runner = verifier.detect_trusted_test_command(session.repo_root)
                    if trusted_runner:
                        test_cmd = f"{trusted_runner} {step.verification_expectations.strip()}"
                    elif self.config.verification_command:
                        test_cmd = f"{self.config.verification_command} {step.verification_expectations.strip()}"
                    else:
                        test_cmd = self.config.verification_command
                    step_verif = verifier.run_tests(session, test_command=test_cmd, broker=broker)
            elif self.config.verification_command:
                test_cmd = self.config.verification_command
                step_verif = verifier.run_tests(session, test_command=test_cmd, broker=broker)
            else:
                trusted_runner = verifier.detect_trusted_test_command(session.repo_root)
                py_files = [f for f in step_modified_files if f.endswith(".py")]
                test_files_modified = [f for f in step_modified_files if f.startswith("tests/") and f.endswith(".py")]
                if test_files_modified and trusted_runner:
                    test_cmd = f"{trusted_runner} {' '.join(test_files_modified)}"
                    step_verif = verifier.run_tests(session, test_command=test_cmd, broker=broker)
                elif py_files:
                    syntax_passed = True
                    syntax_err = ""
                    for pf in py_files:
                        retcode, out, err = broker.run_command(f"python -m py_compile {pf}")
                        if retcode != 0:
                            syntax_passed = False
                            syntax_err = err or out
                            break
                    step_verif = VerificationResult(
                        passed=syntax_passed,
                        exit_code=0 if syntax_passed else 1,
                        stdout="Step syntax verification passed." if syntax_passed else "",
                        stderr=syntax_err,
                        duration_seconds=0.1,
                        command="py_compile",
                    )
                else:
                    test_cmd = None
                    step_verif = verifier.run_tests(session, test_command=None, broker=broker)

            last_verif_result = step_verif
            last_verif_id = self.state_manager.record_verification(
                task_id=task.id,
                verification_type=VerificationType.STEP,
                command=step_verif.command or (test_cmd or ""),
                exit_code=step_verif.exit_code,
                passed=step_verif.passed,
                duration_seconds=step_verif.duration_seconds,
                plan_id=plan.plan_id,
                step_id=step.id,
                stdout=step_verif.stdout,
                stderr=step_verif.stderr,
            )

            # Bounded Step Repair Loop
            step_repair_rounds = 0
            while not step_verif.passed and budget.can_attempt_repair(step_repair_rounds)[0]:
                can_call, call_reason = budget.can_call_provider()
                if not can_call:
                    emit("status", {"message": f"Budget ceiling reached ({call_reason}); halting step repair."})
                    break

                step_repair_rounds += 1
                budget.record_repair_round()
                emit("status", {"message": f"{step.id} tests failed. {impl_agent.name} is attempting targeted repair (Round {step_repair_rounds})..."})

                repair_prompt = (
                    f"Plan Step: {step.id} - {step.objective}\n\n"
                    f"Verification failed with command: {step_verif.command}\n"
                )
                if step_verif.stderr:
                    repair_prompt += f"Stderr:\n{step_verif.stderr[:800]}\n"
                if step_verif.stdout:
                    repair_prompt += f"Stdout:\n{step_verif.stdout[:800]}\n"
                repair_prompt += (
                    "\nRESPONSE CONTRACT:\n"
                    "Format updated files strictly as:\n"
                    "### File: relative/path/to/file.ext\n"
                    "```language\nfull updated file content\n```"
                )

                rep_run_id = self.state_manager.record_provider_stage_started(
                    task_id=task.id,
                    stage="step_repair",
                    provider=impl_agent.name,
                    role="implementer",
                    plan_id=plan.plan_id,
                    step_id=step.id,
                    round_number=step_repair_rounds,
                    prompt_summary=repair_prompt[:200],
                )
                t_start = time.perf_counter()
                try:
                    rep_resp = impl_agent.invoke(repair_prompt, context=step_code_context)
                except Exception as exc:
                    self.state_manager.fail_provider_stage(rep_run_id, error_message=str(exc))
                    raise exc
                t_dur = (time.perf_counter() - t_start) * 1000.0
                budget.record_call(
                    provider_name=impl_agent.name,
                    duration_ms=rep_resp.duration_ms or t_dur,
                    input_tokens=rep_resp.input_tokens,
                    output_tokens=rep_resp.output_tokens,
                    fusion_context_tokens=rep_resp.fusion_context_tokens,
                    stage=f"Step {step.id} Repair Round {step_repair_rounds}",
                )
                self.state_manager.complete_provider_stage(
                    run_id=rep_run_id,
                    response=rep_resp,
                    context=step_code_context,
                    duration_ms=rep_resp.duration_ms or t_dur,
                    status="success",
                )
                repaired = WorkspaceEditor.apply_edits(rep_resp.content, broker)
                for rf in repaired:
                    if rf not in step_modified_files:
                        step_modified_files.append(rf)
                    if rf not in all_modified_files:
                        all_modified_files.append(rf)

                step_verif = verifier.run_tests(session, test_command=test_cmd, broker=broker)
                last_verif_result = step_verif
                last_verif_id = self.state_manager.record_verification(
                    task_id=task.id,
                    verification_type=VerificationType.STEP,
                    command=step_verif.command or (test_cmd or ""),
                    exit_code=step_verif.exit_code,
                    passed=step_verif.passed,
                    duration_seconds=step_verif.duration_seconds,
                    plan_id=plan.plan_id,
                    step_id=step.id,
                    stdout=step_verif.stdout,
                    stderr=step_verif.stderr,
                )

            # Handle Step Outcome
            if step_verif.passed:
                try:
                    checkpoint = checkpoint_mgr.create_checkpoint(
                        session=session,
                        plan_id=plan.plan_id,
                        step_id=step.id,
                        objective_summary=step.objective,
                        approved_files=step_modified_files,
                        verification_passed=True,
                        provider_name=impl_agent.name,
                        token_metrics={
                            "input_tokens": step_resp.input_tokens,
                            "output_tokens": step_resp.output_tokens,
                            "fusion_context_tokens": step_resp.fusion_context_tokens,
                        },
                        base_commit_sha=current_verified_sha,
                        verification_id=last_verif_id,
                        state_manager=self.state_manager,
                    )
                    current_verified_sha = checkpoint.commit_sha
                    self.state_manager.record_checkpoint(checkpoint)
                    self.state_manager.update_task_stage(task.id, f"step_{step.id}", last_checkpoint_sha=checkpoint.commit_sha)
                    emit("status", {"message": f"Step {step.id} verified and checkpointed ({checkpoint.commit_sha[:8]})."})
                except UnexpectedFilesError as u_exc:
                    emit("status", {"message": f"Step {step.id} checkpoint failed: {u_exc}"})
                    step_verif = VerificationResult(
                        passed=False,
                        exit_code=-1,
                        stdout="",
                        stderr=str(u_exc),
                        duration_seconds=0.0,
                        command="",
                    )

            if step_verif.passed:
                res = StepResult(
                    step_id=step.id,
                    status=StepStatus.COMPLETED,
                    files_modified=step_modified_files,
                    checkpoint_sha=current_verified_sha,
                    verification_passed=True,
                    provider=impl_agent.name,
                    repair_rounds=step_repair_rounds,
                )
                step.status = StepStatus.COMPLETED
                step.result = res
                self.state_manager.update_step_status(plan.plan_id, step.id, StepStatus.COMPLETED, result=res)
                completed_steps_info.append(f"{step.id} ({step.objective}): {', '.join(step_modified_files)}")
            else:
                # STEP FAILED: Execute exact rollback and halt dependent steps
                res = StepResult(
                    step_id=step.id,
                    status=StepStatus.FAILED,
                    files_modified=step_modified_files,
                    checkpoint_sha=current_verified_sha,
                    verification_passed=False,
                    verification_output=step_verif.stderr or step_verif.stdout,
                    provider=impl_agent.name,
                    repair_rounds=step_repair_rounds,
                )
                step.status = StepStatus.FAILED
                step.result = res
                self.state_manager.update_step_status(plan.plan_id, step.id, StepStatus.FAILED, result=res)
                emit("status", {"message": f"Step {step.id} failed after repairs. Performing exact rollback to {current_verified_sha[:8]}..."})

                checkpoint_mgr.rollback_to_checkpoint(
                    session=session,
                    checkpoint_sha=current_verified_sha,
                    created_files=step_modified_files,
                )

                # Skip all remaining dependent steps
                for remaining_step in plan.steps[step_idx + 1 :]:
                    remaining_step.status = StepStatus.SKIPPED
                    self.state_manager.update_step_status(plan.plan_id, remaining_step.id, StepStatus.SKIPPED)

                emit("status", {"message": f"Halted plan execution after failure in {step.id}."})
                break

        # 5. Full Repository Verification
        has_failed_steps = any(s.status == StepStatus.FAILED for s in plan.steps)
        any_completed = any(s.status == StepStatus.COMPLETED for s in plan.steps)

        if (any_completed and not has_failed_steps) or rerun_final_verification:
            emit("status", {"message": "All plan steps completed. Executing full repository verification suite..."})
            self.state_manager.update_task_stage(task.id, "final_verification")
            final_verif = verifier.run_tests(session, test_command=self.config.verification_command, broker=broker)
            self.state_manager.record_verification(
                task_id=task.id,
                verification_type=VerificationType.FINAL,
                command=final_verif.command or (self.config.verification_command or ""),
                exit_code=final_verif.exit_code,
                passed=final_verif.passed,
                duration_seconds=final_verif.duration_seconds,
                plan_id=plan.plan_id,
                stdout=final_verif.stdout,
                stderr=final_verif.stderr,
            )
        else:
            final_verif = last_verif_result or VerificationResult(
                passed=False,
                exit_code=-1,
                stdout="",
                stderr="One or more plan steps failed or were not completed.",
                duration_seconds=0.0,
                command="",
            )
            self.state_manager.record_verification(
                task_id=task.id,
                verification_type=VerificationType.FINAL,
                command=final_verif.command or "",
                exit_code=final_verif.exit_code,
                passed=final_verif.passed,
                duration_seconds=final_verif.duration_seconds,
                plan_id=plan.plan_id,
                stdout=final_verif.stdout,
                stderr=final_verif.stderr,
            )

        # 6. Extract Unified Diff against original base commit
        diff = verifier.get_diff(session)

        # 7. Final Peer Review & Bounded Final Repair
        review_result = None
        if rev_agent and final_verif.passed and budget.can_call_provider()[0]:
            self.state_manager.update_task_stage(task.id, "final_review")
            emit("status", {"message": f"{rev_agent.name} is conducting final peer review on consolidated plan diff..."})
            plan_summary_text = "\n".join([
                f"- {s.id} ({s.status.value}): {s.objective}" for s in plan.steps
            ])
            rev_content = (
                f"### EXECUTION PLAN SUMMARY\n{plan_summary_text}\n\n"
                f"### CONSOLIDATED UNIFIED DIFF\n{diff if diff else 'No modifications detected.'}\n\n"
                f"### FINAL VERIFICATION RESULT\n"
                f"Status: {'PASSED' if final_verif.passed else 'FAILED'}\n"
                f"Exit Code: {final_verif.exit_code}\n"
            )
            rev_run_id = self.state_manager.record_provider_stage_started(
                task_id=task.id,
                stage="final_review",
                provider=rev_agent.name,
                role="reviewer",
                plan_id=plan.plan_id,
                round_number=1,
                prompt_summary=rev_content[:200],
            )
            t_start = time.perf_counter()
            try:
                review_resp = rev_agent.review(
                    content=rev_content,
                    criteria=(
                        "Evaluate complete unified diff across all plan steps for correctness, security, syntax, and regressions. "
                        "If the plan implementation is sound and handles all edge cases, return [APPROVED]. "
                        "If issues remain, return [NEEDS_REVISION] with specific critique."
                    ),
                    context=context,
                )
            except Exception as exc:
                self.state_manager.fail_provider_stage(rev_run_id, error_message=str(exc))
                raise exc
            t_rev = (time.perf_counter() - t_start) * 1000.0
            budget.record_call(
                provider_name=rev_agent.name,
                duration_ms=review_resp.duration_ms or t_rev,
                input_tokens=review_resp.input_tokens,
                output_tokens=review_resp.output_tokens,
                fusion_context_tokens=review_resp.fusion_context_tokens or (len(rev_content) // 4),
                stage="Final Plan Peer Review",
            )
            self.state_manager.complete_provider_stage(
                run_id=rev_run_id,
                response=review_resp,
                context=context,
                duration_ms=review_resp.duration_ms or t_rev,
                status="approved" if review_resp.status == ReviewStatus.APPROVED else "needs_revision",
            )
            review_result = ReviewResult(
                reviewer_agent=rev_agent.name,
                subject_agent=impl_agent.name,
                status=review_resp.status,
                comments=review_resp.comments,
                suggested_fixes=review_resp.suggested_fixes,
                duration_ms=review_resp.duration_ms or t_rev,
                input_tokens=review_resp.input_tokens,
                output_tokens=review_resp.output_tokens,
            )
            reviews.append(review_result)

            # Bounded Final Repair Cycle if reviewer requested revision
            if review_result.status == ReviewStatus.NEEDS_REVISION and budget.can_attempt_repair(0)[0]:
                self.state_manager.update_task_stage(task.id, "final_repair")
                emit("status", {"message": f"Final review requested revision. {impl_agent.name} is attempting final consolidated repair..."})
                bounded_critique = review_result.comments[:max_feedback_chars]
                final_repair_prompt = (
                    f"Task: {task.title}\n\n"
                    f"Final peer review critique:\n{bounded_critique}\n\n"
                    f"### CURRENT UNIFIED DIFF:\n{diff}\n\n"
                    "RESPONSE CONTRACT:\n"
                    "Address all critiques. Format updated files strictly as:\n"
                    "### File: relative/path/to/file.ext\n```language\nfull file content\n```"
                )
                rep_run_id = self.state_manager.record_provider_stage_started(
                    task_id=task.id,
                    stage="final_repair",
                    provider=impl_agent.name,
                    role="implementer",
                    plan_id=plan.plan_id,
                    round_number=1,
                    prompt_summary=final_repair_prompt[:200],
                )
                t_start = time.perf_counter()
                try:
                    rep_resp = impl_agent.invoke(final_repair_prompt, context=plan_code_context)
                except Exception as exc:
                    self.state_manager.fail_provider_stage(rep_run_id, error_message=str(exc))
                    raise exc
                t_dur = (time.perf_counter() - t_start) * 1000.0
                budget.record_call(
                    provider_name=impl_agent.name,
                    duration_ms=rep_resp.duration_ms or t_dur,
                    input_tokens=rep_resp.input_tokens,
                    output_tokens=rep_resp.output_tokens,
                    stage="Final Consolidated Repair",
                )
                self.state_manager.complete_provider_stage(
                    run_id=rep_run_id,
                    response=rep_resp,
                    context=plan_code_context,
                    duration_ms=rep_resp.duration_ms or t_dur,
                    status="success",
                )
                WorkspaceEditor.apply_edits(rep_resp.content, broker)
                final_verif = verifier.run_tests(session, test_command=self.config.verification_command, broker=broker)
                self.state_manager.record_verification(
                    task_id=task.id,
                    verification_type=VerificationType.POST_REPAIR,
                    command=final_verif.command or (self.config.verification_command or ""),
                    exit_code=final_verif.exit_code,
                    passed=final_verif.passed,
                    duration_seconds=final_verif.duration_seconds,
                    plan_id=plan.plan_id,
                    stdout=final_verif.stdout,
                    stderr=final_verif.stderr,
                )
                diff = verifier.get_diff(session)

                if budget.can_call_provider()[0]:
                    self.state_manager.update_task_stage(task.id, "final_rereview")
                    emit("status", {"message": f"{rev_agent.name} is re-evaluating updated diff..."})
                    rev_content_2 = f"### UNIFIED DIFF (After Final Repair)\n{diff}\n\n### VERIFICATION\nPassed: {final_verif.passed}\n"
                    rerev_run_id = self.state_manager.record_provider_stage_started(
                        task_id=task.id,
                        stage="final_rereview",
                        provider=rev_agent.name,
                        role="reviewer",
                        plan_id=plan.plan_id,
                        round_number=2,
                        prompt_summary=rev_content_2[:200],
                    )
                    t_start2 = time.perf_counter()
                    try:
                        review_resp_2 = rev_agent.review(
                            content=rev_content_2,
                            criteria="Re-evaluate updated unified diff after repair for correctness and resolution of previous critique.",
                            context=context,
                        )
                    except Exception as exc:
                        self.state_manager.fail_provider_stage(rerev_run_id, error_message=str(exc))
                        raise exc
                    t_rev2 = (time.perf_counter() - t_start2) * 1000.0
                    budget.record_call(
                        provider_name=rev_agent.name,
                        duration_ms=review_resp_2.duration_ms or t_rev2,
                        input_tokens=review_resp_2.input_tokens,
                        output_tokens=review_resp_2.output_tokens,
                        stage="Final Plan Peer Re-Review",
                    )
                    self.state_manager.complete_provider_stage(
                        run_id=rerev_run_id,
                        response=review_resp_2,
                        context=context,
                        duration_ms=review_resp_2.duration_ms or t_rev2,
                        status="approved" if review_resp_2.status == ReviewStatus.APPROVED else "needs_revision",
                    )
                    review_result = ReviewResult(
                        reviewer_agent=rev_agent.name,
                        subject_agent=impl_agent.name,
                        status=review_resp_2.status,
                        comments=review_resp_2.comments,
                    )
                    reviews.append(review_result)

        # Update final plan status and task stage
        if plan.status != PlanStatus.BUDGET_EXHAUSTED:
            if has_failed_steps or not final_verif.passed:
                plan.status = PlanStatus.FAILED
                self.state_manager.update_plan_status(plan.plan_id, PlanStatus.FAILED)
                self.state_manager.update_task_stage(task.id, "failed")
            elif all(s.status == StepStatus.COMPLETED for s in plan.steps) and final_verif.passed:
                plan.status = PlanStatus.COMPLETED
                self.state_manager.update_plan_status(plan.plan_id, PlanStatus.COMPLETED)
                self.state_manager.update_task_stage(task.id, "completed")
            else:
                plan.status = PlanStatus.FAILED
                self.state_manager.update_plan_status(plan.plan_id, PlanStatus.FAILED)
                self.state_manager.update_task_stage(task.id, "failed")

        plan_summary_msg = (
            f"Checkpointed execution plan completed in isolated worktree ({session.task_branch}).\n"
            f"Plan: '{plan.title}'\n"
            f"Steps completed: {len([s for s in plan.steps if s.status == StepStatus.COMPLETED])}/{len(plan.steps)}\n"
            f"Files modified: {', '.join(all_modified_files) if all_modified_files else 'None'}\n"
            f"Automated verification: {'PASSED' if final_verif.passed else 'FAILED'}\n"
            f"Final peer review: {review_result.status.value if review_result else 'SKIPPED'}"
        )

        has_any_tok = any(sm.get("input_tokens") is not None for sm in stage_metrics)
        tot_in = sum(sm["input_tokens"] for sm in stage_metrics if sm.get("input_tokens") is not None) if has_any_tok else None
        tot_out = sum(sm["output_tokens"] for sm in stage_metrics if sm.get("output_tokens") is not None) if has_any_tok else None
        tot_duration = sum(sm["duration_ms"] for sm in stage_metrics)

        deliberation = DeliberationResult(
            strategy_used=StrategyType.CHECKPOINTED_PLAN.value,
            synthesized_output=plan_summary_msg,
            proposals=proposals,
            reviews=reviews,
            participating_providers=[planner_agent.name, impl_agent.name] + ([rev_agent.name] if rev_agent else []),
            rounds_executed=len([s for s in plan.steps if s.status == StepStatus.COMPLETED]),
            total_input_tokens=tot_in,
            total_output_tokens=tot_out,
            duration_ms=tot_duration,
            stage_metrics=stage_metrics,
        )

        return deliberation, session, final_verif, diff, review_result

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
        stats_tracker = ProviderStatsTracker(self.db)
        provider_stats = stats_tracker.get_all_provider_stats(list(self.providers.keys()))
        routing = self.router.route(
            task_prompt=user_prompt,
            available_providers=self.providers,
            optimization_mode=self.config.optimization_mode,
            provider_stats=provider_stats,
            allow_multi_step_planning=getattr(self.config.deliberation, "allow_multi_step_planning", True),
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

        repo_path = Path(self.config.project_root).resolve()
        task_lock = TaskExecutionLock(task.id, repo_path)
        if not task_lock.acquire():
            raise TaskLockError(f"Task '{task.id}' is currently locked by another process.")
        self.state_manager.record_task_lock(
            task_id=task.id,
            owner_id=task_lock.owner_id,
            pid=os.getpid(),
            hostname=socket.gethostname(),
        )

        recovery_mgr = CheckpointRecoveryManager()
        repo_fingerprint = recovery_mgr.get_repo_fingerprint(repo_path)
        config_snapshot = json.dumps({
            "verification_command": self.config.verification_command or "",
            "optimization_mode": self.config.optimization_mode.value,
            "strategy": routing.strategy.value,
        })
        self.state_manager.update_task_recovery_fields(
            task.id,
            repo_fingerprint=repo_fingerprint,
            execution_config_snapshot=config_snapshot,
        )

        try:
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

            # 5. Execute Multi-Agent Deliberation, Autonomous Edit, or Checkpointed Plan
            workspace_session = None
            verification_result = None
            diff = None
            review_result = None

            if active_strategy == StrategyType.CHECKPOINTED_PLAN:
                emit("status", {"message": f"Executing strategy: {active_strategy.value}..."})
                try:
                    deliberation, workspace_session, verification_result, diff, review_result = (
                        self._run_checkpointed_plan(
                            task=task,
                            lead=lead_agent,
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
                    has_chk = bool(self.state_manager.get_checkpoints_for_task(task.id))
                    if not has_chk:
                        try:
                            WorkspaceSession(task_id=task.id, repo_root=Path(self.config.project_root).resolve()).teardown(delete_branch=True)
                        except Exception:
                            pass
                    self.state_manager.update_task_status(task.id, TaskStatus.FAILED)
                    task.status = TaskStatus.FAILED
                    err_msg = f"Checkpointed plan execution failed: {exc}"
                    emit("status", {"message": f"Error: {err_msg}"})
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
            elif active_strategy == StrategyType.AUTONOMOUS_EDIT:
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
            if active_strategy not in (StrategyType.CHECKPOINTED_PLAN, StrategyType.AUTONOMOUS_EDIT):
                for prop in deliberation.proposals:
                    self.state_manager.record_provider_stage(
                        task_id=task.id,
                        stage="deliberation_proposal",
                        provider=prop.agent_name,
                        role="proposer",
                        response_content=prop.content,
                        prompt_summary=prop.summary,
                        input_tokens=prop.input_tokens,
                        output_tokens=prop.output_tokens,
                        duration_ms=prop.duration_ms,
                        status="success",
                    )

                for crit in deliberation.critiques:
                    self.state_manager.record_provider_stage(
                        task_id=task.id,
                        stage="deliberation_critique",
                        provider=crit.reviewer_agent,
                        role="critic",
                        response_content=crit.content,
                        prompt_summary=f"Critique for {crit.target_agent}",
                        input_tokens=crit.input_tokens,
                        output_tokens=crit.output_tokens,
                        duration_ms=crit.duration_ms,
                        status="success",
                    )

                for sm in getattr(deliberation, "stage_metrics", []):
                    if sm.get("role") == "synthesizer":
                        self.state_manager.record_provider_stage(
                            task_id=task.id,
                            stage="deliberation_synthesis",
                            provider=sm["provider"],
                            role="synthesizer",
                            response_content=deliberation.synthesized_output,
                            prompt_summary=f"Consolidated synthesis by {sm['provider']}",
                            input_tokens=sm.get("input_tokens"),
                            output_tokens=sm.get("output_tokens"),
                            duration_ms=sm.get("duration_ms", 0.0),
                            status="success",
                        )

            for rev in deliberation.reviews:
                self.state_manager.record_review(
                    task_id=task.id,
                    reviewer_provider=rev.reviewer_agent,
                    subject_agent=rev.subject_agent,
                    status=rev.status,
                    comments=rev.comments,
                )

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
        finally:
            task_lock.release()
            try:
                self.state_manager.release_task_lock(task.id, task_lock.owner_id)
            except Exception:
                pass

    def resume_task(
        self,
        task_id: str,
        on_status: Optional[Callable] = None,
    ) -> OrchestratorResult:
        """Resume an interrupted or crashed task from its latest verified state."""
        def emit(event_type: str, data: Dict[str, Any]):
            if on_status:
                try:
                    on_status(event_type, data)
                except TypeError:
                    on_status({"event": event_type, **data})

        emit("status", {"message": f"Locating task '{task_id}' for resume..."})
        task = self.state_manager.get_task(task_id)
        if not task:
            raise RecoveryError(f"Task '{task_id}' not found in database.")

        if task.status == TaskStatus.COMPLETED:
            emit("status", {"message": f"Task '{task_id}' is already COMPLETED."})
            strat_val = (task.selected_strategy or "CHECKPOINTED_PLAN").upper()
            try:
                strat_enum = StrategyType(strat_val)
            except ValueError:
                strat_enum = StrategyType.CHECKPOINTED_PLAN
            t_type = task.task_type if isinstance(task.task_type, TaskType) else TaskType(task.task_type or "GENERAL")
            t_comp = task.complexity if isinstance(task.complexity, Complexity) else Complexity(task.complexity or "MEDIUM")
            return OrchestratorResult(
                task=task,
                routing=RoutingDecision(
                    task_type=t_type,
                    complexity=t_comp,
                    strategy=strat_enum,
                    primary_provider="system",
                    secondary_provider=None,
                    rationale="Task already completed",
                ),
                deliberation=DeliberationResult(
                    strategy_used=task.selected_strategy or "CHECKPOINTED_PLAN",
                    synthesized_output=f"Task '{task_id}' was previously completed successfully.",
                ),
                final_answer=f"Task '{task_id}' is already COMPLETED.",
            )

        repo_path = Path(self.config.project_root).resolve()
        task_lock = TaskExecutionLock(task_id, repo_path)
        if not task_lock.acquire():
            raise TaskLockError(f"Task '{task_id}' is currently locked by another process.")

        self.state_manager.record_task_lock(
            task_id=task_id,
            owner_id=task_lock.owner_id,
            pid=os.getpid(),
            hostname=socket.gethostname(),
        )

        try:
            # Reconcile stale provider runs left behind in STARTED state to INTERRUPTED
            stale_count = self.state_manager.reconcile_stale_provider_runs(task.id)
            if stale_count > 0:
                emit("status", {"message": f"Reconciled {stale_count} interrupted provider run(s) from previous session."})

            new_recovery_attempts = (task.recovery_attempts or 0) + 1
            task.recovery_attempts = new_recovery_attempts
            now_iso = datetime.now(timezone.utc).isoformat()
            self.state_manager.update_task_status(
                task.id,
                TaskStatus.RESUMING,
                recovery_attempts=new_recovery_attempts,
                resumed_at=now_iso,
            )
            task.status = TaskStatus.RESUMING

            # 1. Validate repository identity & config drift
            recovery_mgr = CheckpointRecoveryManager()
            if not recovery_mgr.validate_repository_identity(
                repo_root=repo_path,
                expected_fingerprint=task.repo_fingerprint,
                expected_base_commit=task.base_commit,
            ):
                raise RecoveryError(
                    f"Repository identity validation failed: repository at '{repo_path}' cannot be proven "
                    f"to match original task repository (expected base: {task.base_commit}, fingerprint: {task.repo_fingerprint})."
                )

            curr_cfg = {"verification_command": self.config.verification_command or ""}
            drift_ok, drift_err = recovery_mgr.validate_config_drift(curr_cfg, task.execution_config_snapshot)
            if not drift_ok:
                raise RecoveryError(drift_err)

            # 2. Check for pending or interrupted promotion transaction
            promo_engine = PromotionEngine()
            latest_promo = self.state_manager.get_active_promotion_transaction(task.id)
            if latest_promo:
                emit("status", {"message": "Detected promotion transaction from prior session. Reconciling..."})
                temp_session = WorkspaceSession(task_id=task.id, repo_root=repo_path)
                promo_res = promo_engine.reconcile_interrupted_promotion(temp_session, state_manager=self.state_manager)
                if promo_res.success:
                    self.state_manager.update_task_status(task.id, TaskStatus.COMPLETED)
                    task.status = TaskStatus.COMPLETED
                    emit("status", {"message": f"Task was already promoted: {promo_res.message}"})
                    strat_val = (task.selected_strategy or "CHECKPOINTED_PLAN").upper()
                    try:
                        strat_enum = StrategyType(strat_val)
                    except ValueError:
                        strat_enum = StrategyType.CHECKPOINTED_PLAN
                    t_type = task.task_type if isinstance(task.task_type, TaskType) else TaskType(task.task_type or "GENERAL")
                    t_comp = task.complexity if isinstance(task.complexity, Complexity) else Complexity(task.complexity or "MEDIUM")
                    return OrchestratorResult(
                        task=task,
                        routing=RoutingDecision(
                            task_type=t_type,
                            complexity=t_comp,
                            strategy=strat_enum,
                            primary_provider="system",
                            secondary_provider=None,
                            rationale="Reconciled completed promotion",
                        ),
                        deliberation=DeliberationResult(
                            strategy_used=task.selected_strategy or "CHECKPOINTED_PLAN",
                            synthesized_output=promo_res.message,
                        ),
                        final_answer=promo_res.message,
                    )
                elif promo_res.requires_manual_reconciliation:
                    self.state_manager.update_task_status(task.id, TaskStatus.FAILED)
                    task.status = TaskStatus.FAILED
                    raise RecoveryError(f"Ambiguous promotion state: {promo_res.message} Manual reconciliation required.")

            # 3. Attach or reconstruct workspace session
            session = WorkspaceSession(task_id=task.id, repo_root=repo_path)
            session.attach_or_reconstruct(
                expected_checkpoint_sha=task.last_checkpoint_sha,
                original_base_commit=task.base_commit,
            )

            # 4. Checkpoint Reconciliation
            plan = self.state_manager.get_plan_by_task_id(task.id)
            reconcile_res = recovery_mgr.reconcile_checkpoints(session, task, plan, self.state_manager)
            emit("status", {"message": reconcile_res.message})
            task.last_checkpoint_sha = reconcile_res.verified_checkpoint_sha
            self.state_manager.update_task_stage(task.id, "resuming", last_checkpoint_sha=task.last_checkpoint_sha)

            # 5. Restore Budget from History
            budget = TaskBudgetController.restore_from_history(task.id, self.state_manager, self.config.deliberation)
            emit("status", {"message": f"Restored budget: {budget.calls_made} call(s), {budget.repair_rounds_attempted} repair round(s) consumed."})

            # 6. Resolve Providers & Health Verification
            stats_tracker = ProviderStatsTracker(self.db)
            provider_stats = stats_tracker.get_all_provider_stats(list(self.providers.keys()))
            routing = self.router.route(
                task_prompt=task.description or task.title,
                available_providers=self.providers,
                optimization_mode=self.config.optimization_mode,
                provider_stats=provider_stats,
                allow_multi_step_planning=getattr(self.config.deliberation, "allow_multi_step_planning", True),
            )
            impl_key = routing.role_assignments.get("implementer") or routing.primary_provider
            rev_key = routing.role_assignments.get("reviewer") or routing.secondary_provider
            lead_key = routing.role_assignments.get("lead") or routing.primary_provider

            implementer_agent = self.providers.get("implementer") or self.providers.get(impl_key)
            reviewer_agent = self.providers.get("reviewer") or (self.providers.get(rev_key) if rev_key else None)
            lead_agent = self.providers.get("planner") or self.providers.get("lead") or self.providers.get(lead_key)
            primary_agent = implementer_agent if routing.strategy == StrategyType.AUTONOMOUS_EDIT else lead_agent

            if not primary_agent or not primary_agent.health_check().healthy:
                raise RuntimeError(f"Primary provider for resume '{impl_key}' is unavailable or unhealthy.")

            # 7. Construct 3-Tier Context Snapshot
            context = self.state_manager.build_context_snapshot(self.project["id"], current_task=task)

            # 8. Continue Plan Execution
            rerun_verif = (reconcile_res.resumed_step_index >= len(plan.steps)) if plan else False
            deliberation, workspace_session, verification_result, diff, review_result = (
                self._run_checkpointed_plan(
                    task=task,
                    lead=lead_agent,
                    implementer=implementer_agent,
                    reviewer=reviewer_agent,
                    context=context,
                    emit=emit,
                    budget=budget,
                    session=session,
                    existing_plan=plan,
                    start_step_index=reconcile_res.resumed_step_index,
                    last_verified_sha=reconcile_res.verified_checkpoint_sha,
                    rerun_final_verification=rerun_verif,
                )
            )

            # Update final task status
            task.status = TaskStatus.COMPLETED if (verification_result and verification_result.passed) else TaskStatus.FAILED
            self.state_manager.update_task_status(
                task.id,
                task.status,
                verification_passed=(verification_result.passed if verification_result else True),
                repair_rounds=getattr(deliberation, "rounds_executed", 1) - 1 if deliberation else 0,
            )

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

        finally:
            task_lock.release()
            try:
                self.state_manager.release_task_lock(task.id, task_lock.owner_id)
            except Exception:
                pass
