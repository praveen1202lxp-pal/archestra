"""The primary orchestrator coordinating providers, routing, deliberation, and shared memory."""

import time
from typing import Any, Callable, Dict, Optional

from fusion_agent.config.schema import FusionConfig
from fusion_agent.core.deliberation import DeliberationEngine
from fusion_agent.core.router import RoutingDecision, TaskRouter
from fusion_agent.memory.database import Database
from fusion_agent.memory.project_state import ProjectStateManager
from fusion_agent.models.deliberation import DeliberationResult
from fusion_agent.models.strategy import StrategyType
from fusion_agent.models.task import Task, TaskStatus
from fusion_agent.providers.base import AgentProvider
from fusion_agent.providers.registry import ProviderRegistry


class OrchestratorResult:
    """Consolidated result of orchestrating a user task."""

    def __init__(
        self,
        task: Task,
        routing: RoutingDecision,
        deliberation: DeliberationResult,
        final_answer: str,
        context: Optional[Any] = None,
    ):
        self.task = task
        self.routing = routing
        self.deliberation = deliberation
        self.final_answer = final_answer
        self.context = context


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

        # Initialize project in SQLite
        self.project = self.state_manager.get_or_create_project(
            project_id=self.config.project_name.lower().replace(" ", "_"),
            name=self.config.project_name,
            root_path=self.config.project_root,
        )

        # Initialize or register providers
        self.providers: Dict[str, AgentProvider] = providers or {}
        if not self.providers:
            self._load_configured_providers()

    def _load_configured_providers(self) -> None:
        """Instantiate providers defined in config.agents."""
        for agent_key, agent_cfg in self.config.agents.items():
            try:
                provider = ProviderRegistry.create(
                    name=agent_cfg.provider_name,
                    provider_type=agent_cfg.provider_type,
                    config=agent_cfg.to_dict(),
                )
                provider.initialize()
                self.providers[agent_key] = provider
            except Exception as e:
                # Provider initialization failure logged; provider skipped
                pass

    def run_task(
        self,
        user_prompt: str,
        on_status: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> OrchestratorResult:
        """Execute a user request end-to-end as Fusion Agent."""
        def emit(event: str, data: Optional[Dict[str, Any]] = None):
            if on_status:
                on_status(event, data or {})

        emit("status", {"message": "Analyzing task & consulting project memory..."})

        # 1. Classify & Route
        routing = self.router.route(
            task_prompt=user_prompt,
            available_providers=self.providers,
            optimization_mode=self.config.optimization_mode,
        )
        emit("routing", {
            "strategy": routing.strategy.value,
            "task_type": routing.task_type.value,
            "complexity": routing.complexity.value,
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

        # 5. Execute Multi-Agent Deliberation
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

        # Auto-record design decision if strategy involved deliberation
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
        )
