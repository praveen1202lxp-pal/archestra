"""Persistent project state manager and context snapshot generator."""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

from fusion_agent.memory.database import Database
from fusion_agent.models.assessment import ReviewRisk
from fusion_agent.models.deliberation import ReviewStatus
from fusion_agent.models.plan import (
    Checkpoint,
    CheckpointTransaction,
    CheckpointTransactionStatus,
    ExecutionPlan,
    PlanStep,
    PlanStatus,
    PromotionTransaction,
    PromotionTransactionStatus,
    StepResult,
    StepStatus,
    VerificationType,
)
from fusion_agent.models.task import Complexity, PromotionDisposition, Task, TaskStatus, TaskType
from fusion_agent.providers.base import ContextSnapshot


class ProjectStateManager:
    """Manages the shared persistent brain of Fusion Agent stored in SQLite."""

    def __init__(self, database: Database):
        self.db = database

    # --- Project Operations ---

    def get_or_create_project(
        self,
        project_id: str,
        name: str,
        root_path: str = ".",
        goal: str = "",
        description: str = "",
        architecture: str = "",
        requirements: str = "",
        coding_conventions: str = "",
        current_milestone: str = "Milestone 1 — Foundation",
    ) -> Dict[str, Any]:
        """Fetch existing project or create new entry."""
        conn = self.db.connect()
        cursor = conn.execute("SELECT * FROM projects WHERE id = ?;", (project_id,))
        row = cursor.fetchone()
        if row:
            return dict(row)

        with conn:
            conn.execute(
                """
                INSERT INTO projects (
                    id, name, root_path, goal, description,
                    architecture, requirements, coding_conventions, current_milestone
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    project_id,
                    name,
                    root_path,
                    goal,
                    description,
                    architecture,
                    requirements,
                    coding_conventions,
                    current_milestone,
                ),
            )
        cursor = conn.execute("SELECT * FROM projects WHERE id = ?;", (project_id,))
        return dict(cursor.fetchone())

    def update_project(self, project_id: str, **kwargs) -> None:
        """Update fields of a project."""
        conn = self.db.connect()
        allowed_fields = {
            "name", "root_path", "goal", "description", "architecture",
            "requirements", "coding_conventions", "current_milestone"
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields}
        if not updates:
            return

        set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
        params = list(updates.values()) + [datetime.now(timezone.utc).isoformat(), project_id]
        
        with conn:
            conn.execute(
                f"UPDATE projects SET {set_clause}, updated_at = ? WHERE id = ?;",
                params,
            )

    # --- Task Operations ---

    def create_task(
        self,
        project_id: str,
        title: str,
        description: str = "",
        task_type: TaskType = TaskType.GENERAL,
        complexity: Complexity = Complexity.MEDIUM,
        selected_strategy: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> Task:
        """Create and record a new task."""
        t_id = task_id or str(uuid.uuid4())[:8]
        conn = self.db.connect()
        now = datetime.now(timezone.utc).isoformat()
        
        with conn:
            conn.execute(
                """
                INSERT INTO tasks (
                    id, project_id, title, description, status,
                    task_type, complexity, selected_strategy, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    t_id,
                    project_id,
                    title,
                    description,
                    TaskStatus.PENDING.value,
                    task_type.value,
                    complexity.value,
                    selected_strategy,
                    now,
                ),
            )
        return Task(
            id=t_id,
            project_id=project_id,
            title=title,
            description=description,
            task_type=task_type,
            complexity=complexity,
            status=TaskStatus.PENDING,
            selected_strategy=selected_strategy,
            created_at=now,
        )

    def update_task_status(
        self,
        task_id: str,
        status: TaskStatus,
        selected_strategy: Optional[str] = None,
        verification_passed: Optional[bool] = None,
        repair_rounds: Optional[int] = None,
        interruption_reason: Optional[str] = None,
        interrupted_at: Optional[str] = None,
        recovery_attempts: Optional[int] = None,
        resumed_at: Optional[str] = None,
        execution_config_snapshot: Optional[str] = None,
        repo_fingerprint: Optional[str] = None,
        base_commit: Optional[str] = None,
        scope_expansions_json: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        """Update task status and optional completion timestamp, metrics, and recovery attributes."""
        conn = self.db.connect()
        now = datetime.now(timezone.utc).isoformat()
        completed_at = now if status in (TaskStatus.COMPLETED, TaskStatus.FAILED) else None
        
        updates = ["status = ?", "completed_at = ?"]
        params: List[Any] = [status.value, completed_at]
        
        if selected_strategy:
            updates.append("selected_strategy = ?")
            params.append(selected_strategy)
        if verification_passed is not None:
            updates.append("verification_passed = ?")
            params.append(1 if verification_passed else 0)
        if repair_rounds is not None:
            updates.append("repair_rounds = ?")
            params.append(repair_rounds)
        if interruption_reason is not None:
            updates.append("interruption_reason = ?")
            params.append(interruption_reason)
        if interrupted_at is not None:
            updates.append("interrupted_at = ?")
            params.append(interrupted_at)
        if recovery_attempts is not None:
            updates.append("recovery_attempts = ?")
            params.append(recovery_attempts)
        if resumed_at is not None:
            updates.append("resumed_at = ?")
            params.append(resumed_at)
        if execution_config_snapshot is not None:
            updates.append("execution_config_snapshot = ?")
            params.append(execution_config_snapshot)
        if repo_fingerprint is not None:
            updates.append("repo_fingerprint = ?")
            params.append(repo_fingerprint)
        if base_commit is not None:
            updates.append("base_commit = ?")
            params.append(base_commit)
        if kwargs.get("scope_expansions_json") is not None:
            updates.append("scope_expansions_json = ?")
            params.append(kwargs["scope_expansions_json"])
            
        params.append(task_id)
        sql = f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?;"
        with conn:
            conn.execute(sql, params)

    def get_task(self, task_id: str) -> Optional[Task]:
        """Fetch a task by ID."""
        conn = self.db.connect()
        row = conn.execute("SELECT * FROM tasks WHERE id = ?;", (task_id,)).fetchone()
        if not row:
            return None
        row_keys = row.keys()
        return Task(
            id=row["id"],
            project_id=row["project_id"],
            title=row["title"],
            description=row["description"],
            task_type=TaskType(row["task_type"]),
            complexity=Complexity(row["complexity"]),
            status=TaskStatus(row["status"]),
            selected_strategy=row["selected_strategy"],
            verification_passed=bool(row["verification_passed"]) if "verification_passed" in row_keys else False,
            repair_rounds=row["repair_rounds"] if "repair_rounds" in row_keys else 0,
            promotion_disposition=PromotionDisposition(row["promotion_disposition"]) if ("promotion_disposition" in row_keys and row["promotion_disposition"]) else PromotionDisposition.NOT_OFFERED,
            active_stage=row["active_stage"] if "active_stage" in row_keys else None,
            last_checkpoint_sha=row["last_checkpoint_sha"] if "last_checkpoint_sha" in row_keys else None,
            interruption_reason=row["interruption_reason"] if "interruption_reason" in row_keys else None,
            interrupted_at=row["interrupted_at"] if "interrupted_at" in row_keys else None,
            recovery_attempts=row["recovery_attempts"] if "recovery_attempts" in row_keys else 0,
            resumed_at=row["resumed_at"] if "resumed_at" in row_keys else None,
            execution_config_snapshot=row["execution_config_snapshot"] if "execution_config_snapshot" in row_keys else None,
            repo_fingerprint=row["repo_fingerprint"] if "repo_fingerprint" in row_keys else None,
            base_commit=row["base_commit"] if "base_commit" in row_keys else None,
            scope_expansions_json=row["scope_expansions_json"] if "scope_expansions_json" in row_keys else None,
            created_at=row["created_at"],
            completed_at=row["completed_at"],
        )

    def record_scope_expansion(
        self,
        task_id: str,
        file_path: str,
        category: str,
        reason: str,
        decision: str = "approved",
    ) -> None:
        """Record an approved or audited scope expansion decision in durable memory."""
        conn = self.db.connect()
        now_iso = datetime.now(timezone.utc).isoformat()
        exp_id = f"scope_exp_{uuid.uuid4().hex[:12]}"
        with conn:
            conn.execute(
                """
                INSERT INTO scope_expansions (id, task_id, file_path, category, reason, decision, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (exp_id, task_id, file_path, category, reason, decision, now_iso),
            )
            # Update tasks.scope_expansions_json for atomic task-level restore
            row = conn.execute("SELECT scope_expansions_json FROM tasks WHERE id = ?;", (task_id,)).fetchone()
            curr_map = {}
            if row and "scope_expansions_json" in row.keys() and row["scope_expansions_json"]:
                try:
                    curr_map = json.loads(row["scope_expansions_json"])
                except Exception:
                    curr_map = {}
            if decision == "approved":
                curr_map[file_path] = reason
            conn.execute(
                "UPDATE tasks SET scope_expansions_json = ? WHERE id = ?;",
                (json.dumps(curr_map), task_id),
            )

    def get_scope_expansions_for_task(self, task_id: str) -> Dict[str, str]:
        """Fetch all approved scope expansion decisions for a task."""
        conn = self.db.connect()
        # Prefer direct query from scope_expansions table
        try:
            rows = conn.execute(
                "SELECT file_path, reason FROM scope_expansions WHERE task_id = ? AND decision = 'approved' ORDER BY created_at ASC;",
                (task_id,),
            ).fetchall()
            result = {row["file_path"]: row["reason"] for row in rows}
            if result:
                return result
        except sqlite3.OperationalError:
            pass

        # Fallback to tasks.scope_expansions_json if present
        try:
            task_row = conn.execute("SELECT scope_expansions_json FROM tasks WHERE id = ?;", (task_id,)).fetchone()
            if task_row and "scope_expansions_json" in task_row.keys() and task_row["scope_expansions_json"]:
                return json.loads(task_row["scope_expansions_json"])
        except Exception:
            pass

        return {}

    def update_task_promotion(self, task_id: str, disposition: Any) -> None:
        """Update promotion disposition on a task."""
        conn = self.db.connect()
        val = disposition.value if isinstance(disposition, PromotionDisposition) else str(disposition)
        with conn:
            conn.execute(
                "UPDATE tasks SET promotion_disposition = ? WHERE id = ?;",
                (val, task_id),
            )

    def update_task_stage(
        self,
        task_id: str,
        stage: str,
        last_checkpoint_sha: Optional[str] = None,
    ) -> None:
        """Update the currently active execution stage and latest checkpoint."""
        conn = self.db.connect()
        with conn:
            if last_checkpoint_sha is not None:
                conn.execute(
                    "UPDATE tasks SET active_stage = ?, last_checkpoint_sha = ? WHERE id = ?;",
                    (stage, last_checkpoint_sha, task_id),
                )
            else:
                conn.execute(
                    "UPDATE tasks SET active_stage = ? WHERE id = ?;",
                    (stage, task_id),
                )

    def update_task_recovery_fields(
        self,
        task_id: str,
        repo_fingerprint: Optional[str] = None,
        execution_config_snapshot: Optional[str] = None,
        base_commit: Optional[str] = None,
    ) -> None:
        """Update repository fingerprint, execution config snapshot, and base commit for task recovery."""
        conn = self.db.connect()
        updates = []
        params = []
        if repo_fingerprint is not None:
            updates.append("repo_fingerprint = ?")
            params.append(repo_fingerprint)
        if execution_config_snapshot is not None:
            updates.append("execution_config_snapshot = ?")
            params.append(execution_config_snapshot)
        if base_commit is not None:
            updates.append("base_commit = ?")
            params.append(base_commit)
        if not updates:
            return
        params.append(task_id)
        with conn:
            conn.execute(f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?;", params)

    def get_recoverable_tasks(self) -> List[Dict[str, Any]]:
        """Fetch tasks that are interrupted, running, or recoverable."""
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT id, title, status, last_checkpoint_sha, created_at FROM tasks WHERE status IN ('RUNNING', 'INTERRUPTED', 'RECOVERABLE', 'RESUMING') ORDER BY created_at DESC;"
        ).fetchall()
        return [dict(r) for r in rows]

    def list_tasks(self, project_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """List recent tasks for project."""
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT * FROM tasks WHERE project_id = ? ORDER BY created_at DESC LIMIT ?;",
            (project_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # --- Architectural Decisions ---

    def record_decision(
        self,
        project_id: str,
        title: str,
        decision: str,
        rationale: str = "",
        agent_source: str = "Fusion Agent",
        task_id: Optional[str] = None,
    ) -> str:
        """Persist an architectural or design decision."""
        conn = self.db.connect()
        d_id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()
        with conn:
            conn.execute(
                """
                INSERT INTO decisions (id, project_id, task_id, title, decision, rationale, agent_source, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (d_id, project_id, task_id, title, decision, rationale, agent_source, now),
            )
        return d_id

    def list_decisions(self, project_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Retrieve recent architectural decisions."""
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT * FROM decisions WHERE project_id = ? ORDER BY timestamp DESC LIMIT ?;",
            (project_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # --- Agent Runs & Reviews ---

    def record_provider_stage(
        self,
        task_id: str,
        stage: str,
        provider_name: str = "",
        role: str = "",
        response_content: str = "",
        prompt_summary: str = "",
        duration_ms: float = 0.0,
        status: str = "SUCCESS",
        plan_id: Optional[str] = None,
        step_id: Optional[str] = None,
        round_number: int = 0,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        reasoning_tokens: Optional[int] = None,
        cached_tokens: Optional[int] = None,
        visible_output_tokens: Optional[int] = None,
        raw_usage: Optional[Any] = None,
        context: Optional[Any] = None,
        fusion_context_chars: Optional[int] = None,
        fusion_context_tokens: Optional[int] = None,
        selected_file_count: Optional[int] = None,
        selected_files: Optional[Union[List[str], str]] = None,
        selected_symbols: Optional[Union[List[str], str]] = None,
        context_expansion_round: int = 0,
        response: Optional[Any] = None,
        provider: Optional[str] = None,
    ) -> str:
        """Record an individual model invocation into SQLite as an authoritative execution ledger."""
        conn = self.db.connect()
        run_id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()
        provider_final = provider or provider_name

        # If a response object (AgentResponse, ReviewResponse) is provided, extract values
        if response is not None:
            if not response_content:
                response_content = getattr(response, "content", None) or getattr(response, "comments", "")
            if not duration_ms and getattr(response, "duration_ms", 0.0):
                duration_ms = response.duration_ms
            # Native tokens
            usage = response.metadata.get("usage") if hasattr(response, "metadata") and isinstance(response.metadata, dict) else None
            if raw_usage is None and usage:
                raw_usage = usage

            # Input tokens
            if input_tokens is None:
                if usage and "input_tokens" in usage:
                    input_tokens = int(usage["input_tokens"])
                elif getattr(response, "input_tokens", None) not in (None, 0):
                    input_tokens = response.input_tokens
                elif usage is not None and "input_tokens" not in usage:
                    input_tokens = None

            # Output tokens
            if output_tokens is None:
                if usage and "output_tokens" in usage:
                    output_tokens = int(usage["output_tokens"])
                elif getattr(response, "output_tokens", None) not in (None, 0):
                    output_tokens = response.output_tokens
                elif usage is not None and "output_tokens" not in usage:
                    output_tokens = None

            # Reasoning, cached, visible tokens
            if reasoning_tokens is None:
                reasoning_tokens = getattr(response, "reasoning_tokens", None)
            if cached_tokens is None:
                cached_tokens = getattr(response, "cached_tokens", None)
            if visible_output_tokens is None:
                visible_output_tokens = getattr(response, "visible_output_tokens", None)

        # Context telemetry extraction
        if context is not None:
            if hasattr(context, "metrics") and isinstance(context.metrics, dict):
                cm = context.metrics
                if fusion_context_chars is None:
                    fusion_context_chars = cm.get("fusion_context_chars")
                if fusion_context_tokens is None:
                    fusion_context_tokens = cm.get("fusion_context_tokens")
                if context_expansion_round == 0 and "context_expansion_round" in cm:
                    context_expansion_round = cm.get("context_expansion_round", 0)

            if hasattr(context, "selected_files"):
                s_files = [getattr(s, "path", getattr(s, "rel_path", str(s))) for s in getattr(context, "selected_files", [])]
                if selected_files is None:
                    selected_files = s_files
                if selected_file_count is None:
                    selected_file_count = len(s_files)

            if hasattr(context, "relevant_symbols") and selected_symbols is None:
                syms = getattr(context, "relevant_symbols", [])
                selected_symbols = [getattr(s, "name", str(s)) for s in syms]

            if fusion_context_chars is None:
                if hasattr(context, "to_prompt_context"):
                    txt = context.to_prompt_context()
                    fusion_context_chars = len(txt)
                    fusion_context_tokens = max(1, len(txt) // 4)
                elif isinstance(context, str):
                    fusion_context_chars = len(context)
                    fusion_context_tokens = max(1, len(context) // 4)

        # Ensure elements in selected_files and selected_symbols are primitives
        if isinstance(selected_files, list):
            selected_files = [getattr(s, "path", getattr(s, "rel_path", str(s))) for s in selected_files]
        if isinstance(selected_symbols, list):
            selected_symbols = [getattr(s, "name", str(s)) for s in selected_symbols]

        # Normalize serialized fields
        raw_usage_str = json.dumps(raw_usage) if isinstance(raw_usage, (dict, list)) else (raw_usage if isinstance(raw_usage, str) else None)
        sel_files_str = json.dumps(selected_files) if isinstance(selected_files, list) else (selected_files if isinstance(selected_files, str) else None)
        sel_syms_str = json.dumps(selected_symbols) if isinstance(selected_symbols, list) else (selected_symbols if isinstance(selected_symbols, str) else None)

        logical_invocation_id = f"{task_id}:{plan_id or 'none'}:{step_id or 'none'}:{stage}:{round_number}"

        with conn:
            task_row = conn.execute("SELECT 1 FROM tasks WHERE id = ?;", (task_id,)).fetchone()
            if not task_row:
                conn.execute(
                    "INSERT OR IGNORE INTO projects (id, name, root_path) VALUES (?, ?, ?);",
                    ("default", "Default Project", "."),
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO tasks (
                        id, project_id, title, description, status, task_type, complexity, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (task_id, "default", task_id, "", "IN_PROGRESS", "FEATURE", "COMPLEX", now),
                )

            # Unaccept prior attempts for the same logical stage/round
            conn.execute(
                "UPDATE agent_runs SET is_accepted = 0 WHERE logical_invocation_id = ?;",
                (logical_invocation_id,),
            )
            att_row = conn.execute(
                "SELECT MAX(attempt_number) FROM agent_runs WHERE logical_invocation_id = ?;",
                (logical_invocation_id,),
            ).fetchone()
            attempt_number = (att_row[0] or 0) + 1 if att_row else 1

            conn.execute(
                """
                INSERT INTO agent_runs (
                    id, task_id, plan_id, step_id, stage, round_number, attempt_number,
                    logical_invocation_id, is_accepted, provider_name, role, prompt_summary, response_content,
                    input_tokens, output_tokens, duration_ms, status,
                    reasoning_tokens, cached_tokens, visible_output_tokens, raw_usage,
                    fusion_context_chars, fusion_context_tokens, selected_file_count,
                    selected_files, selected_symbols, context_expansion_round, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    run_id,
                    task_id,
                    plan_id,
                    step_id,
                    stage,
                    round_number,
                    attempt_number,
                    logical_invocation_id,
                    provider_final,
                    role,
                    prompt_summary,
                    response_content,
                    input_tokens,
                    output_tokens,
                    duration_ms,
                    status.upper() if isinstance(status, str) else "SUCCESS",
                    reasoning_tokens,
                    cached_tokens,
                    visible_output_tokens,
                    raw_usage_str,
                    fusion_context_chars,
                    fusion_context_tokens,
                    selected_file_count or 0,
                    sel_files_str,
                    sel_syms_str,
                    context_expansion_round,
                    now,
                ),
            )
        return run_id

    def record_provider_stage_started(
        self,
        task_id: str,
        stage: str,
        provider_name: str = "",
        role: str = "",
        prompt_summary: str = "",
        plan_id: Optional[str] = None,
        step_id: Optional[str] = None,
        round_number: int = 0,
        context: Optional[Any] = None,
        fusion_context_chars: Optional[int] = None,
        fusion_context_tokens: Optional[int] = None,
        selected_file_count: Optional[int] = None,
        selected_files: Optional[Union[List[str], str]] = None,
        selected_symbols: Optional[Union[List[str], str]] = None,
        context_expansion_round: int = 0,
        provider: Optional[str] = None,
        logical_invocation_id: Optional[str] = None,
    ) -> Tuple[str, int]:
        """Write-ahead persistence of a provider call record with STARTED status.

        Returns (run_id, attempt_number).
        """
        conn = self.db.connect()
        run_id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()
        provider_final = provider or provider_name
        if not logical_invocation_id:
            logical_invocation_id = f"{task_id}:{plan_id or 'none'}:{step_id or 'none'}:{stage}:{round_number}"

        # Context telemetry extraction
        if context is not None:
            if hasattr(context, "metrics") and isinstance(context.metrics, dict):
                cm = context.metrics
                if fusion_context_chars is None:
                    fusion_context_chars = cm.get("fusion_context_chars")
                if fusion_context_tokens is None:
                    fusion_context_tokens = cm.get("fusion_context_tokens")
                if context_expansion_round == 0 and "context_expansion_round" in cm:
                    context_expansion_round = cm.get("context_expansion_round", 0)

            if hasattr(context, "selected_files"):
                s_files = [getattr(s, "path", getattr(s, "rel_path", str(s))) for s in getattr(context, "selected_files", [])]
                if selected_files is None:
                    selected_files = s_files
                if selected_file_count is None:
                    selected_file_count = len(s_files)

            if hasattr(context, "relevant_symbols") and selected_symbols is None:
                syms = getattr(context, "relevant_symbols", [])
                selected_symbols = [getattr(s, "name", str(s)) for s in syms]

            if fusion_context_chars is None:
                if hasattr(context, "to_prompt_context"):
                    txt = context.to_prompt_context()
                    fusion_context_chars = len(txt)
                    fusion_context_tokens = max(1, len(txt) // 4)
                elif isinstance(context, str):
                    fusion_context_chars = len(context)
                    fusion_context_tokens = max(1, len(context) // 4)

        if isinstance(selected_files, list):
            selected_files = [getattr(s, "path", getattr(s, "rel_path", str(s))) for s in selected_files]
        if isinstance(selected_symbols, list):
            selected_symbols = [getattr(s, "name", str(s)) for s in selected_symbols]

        sel_files_str = json.dumps(selected_files) if isinstance(selected_files, list) else (selected_files if isinstance(selected_files, str) else None)
        sel_syms_str = json.dumps(selected_symbols) if isinstance(selected_symbols, list) else (selected_symbols if isinstance(selected_symbols, str) else None)

        with conn:
            self._ensure_task_exists(conn, task_id)
            att_row = conn.execute(
                "SELECT MAX(attempt_number) FROM agent_runs WHERE logical_invocation_id = ?;",
                (logical_invocation_id,),
            ).fetchone()
            attempt_number = (att_row[0] or 0) + 1 if att_row else 1

            conn.execute(
                """
                INSERT INTO agent_runs (
                    id, task_id, plan_id, step_id, stage, round_number, attempt_number,
                    logical_invocation_id, is_accepted, provider_name, role, prompt_summary, response_content,
                    input_tokens, output_tokens, duration_ms, status,
                    fusion_context_chars, fusion_context_tokens, selected_file_count,
                    selected_files, selected_symbols, context_expansion_round, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, '', NULL, NULL, 0.0, 'STARTED', ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    run_id,
                    task_id,
                    plan_id,
                    step_id,
                    stage,
                    round_number,
                    attempt_number,
                    logical_invocation_id,
                    provider_final,
                    role,
                    prompt_summary,
                    fusion_context_chars,
                    fusion_context_tokens,
                    selected_file_count or 0,
                    sel_files_str,
                    sel_syms_str,
                    context_expansion_round,
                    now,
                ),
            )
        return run_id, attempt_number

    def complete_provider_stage(
        self,
        run_id: str,
        response_content: str = "",
        duration_ms: float = 0.0,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        reasoning_tokens: Optional[int] = None,
        cached_tokens: Optional[int] = None,
        visible_output_tokens: Optional[int] = None,
        raw_usage: Optional[Any] = None,
        response: Optional[Any] = None,
        is_accepted: bool = True,
        status: str = "SUCCESS",
        context: Optional[Any] = None,
        **kwargs: Any,
    ) -> None:
        """Mark a STARTED provider stage record as COMPLETED with normalized usage and content."""
        if isinstance(run_id, tuple):
            run_id = run_id[0]
        conn = self.db.connect()
        status_final = status.upper() if isinstance(status, str) else "SUCCESS"

        if response is not None:
            if not response_content:
                response_content = getattr(response, "content", None) or getattr(response, "comments", "")
            if not duration_ms and getattr(response, "duration_ms", 0.0):
                duration_ms = response.duration_ms
            usage = response.metadata.get("usage") if hasattr(response, "metadata") and isinstance(response.metadata, dict) else None
            if raw_usage is None and usage:
                raw_usage = usage
            if input_tokens is None:
                if usage and "input_tokens" in usage:
                    input_tokens = int(usage["input_tokens"])
                elif getattr(response, "input_tokens", None) not in (None, 0):
                    input_tokens = response.input_tokens
                elif usage is not None and "input_tokens" not in usage:
                    input_tokens = None
            if output_tokens is None:
                if usage and "output_tokens" in usage:
                    output_tokens = int(usage["output_tokens"])
                elif getattr(response, "output_tokens", None) not in (None, 0):
                    output_tokens = response.output_tokens
                elif usage is not None and "output_tokens" not in usage:
                    output_tokens = None
            if reasoning_tokens is None:
                reasoning_tokens = getattr(response, "reasoning_tokens", None)
            if cached_tokens is None:
                cached_tokens = getattr(response, "cached_tokens", None)
            if visible_output_tokens is None:
                visible_output_tokens = getattr(response, "visible_output_tokens", None)

        raw_usage_str = json.dumps(raw_usage) if isinstance(raw_usage, (dict, list)) else (raw_usage if isinstance(raw_usage, str) else None)

        fusion_context_chars = None
        fusion_context_tokens = None
        selected_file_count = None
        sel_files_str = None
        sel_syms_str = None
        context_expansion_round = None

        if context is not None:
            if hasattr(context, "metrics") and isinstance(context.metrics, dict):
                cm = context.metrics
                fusion_context_chars = cm.get("fusion_context_chars")
                fusion_context_tokens = cm.get("fusion_context_tokens")
                if "context_expansion_round" in cm:
                    context_expansion_round = cm.get("context_expansion_round", 0)

            if hasattr(context, "selected_files"):
                s_files = [getattr(s, "path", getattr(s, "rel_path", str(s))) for s in getattr(context, "selected_files", [])]
                selected_file_count = len(s_files)
                sel_files_str = json.dumps(s_files)

            if hasattr(context, "relevant_symbols"):
                syms = getattr(context, "relevant_symbols", [])
                sel_syms_str = json.dumps([getattr(s, "name", str(s)) for s in syms])

            if fusion_context_chars is None:
                if hasattr(context, "to_prompt_context"):
                    txt = context.to_prompt_context()
                    fusion_context_chars = len(txt)
                    fusion_context_tokens = max(1, len(txt) // 4)
                elif isinstance(context, str):
                    fusion_context_chars = len(context)
                    fusion_context_tokens = max(1, len(context) // 4)

        with conn:
            if is_accepted:
                curr = conn.execute("SELECT logical_invocation_id FROM agent_runs WHERE id = ?;", (run_id,)).fetchone()
                if curr and curr["logical_invocation_id"]:
                    conn.execute(
                        "UPDATE agent_runs SET is_accepted = 0 WHERE logical_invocation_id = ? AND id != ?;",
                        (curr["logical_invocation_id"], run_id),
                    )

            if context is not None:
                conn.execute(
                    """
                    UPDATE agent_runs
                    SET status = ?,
                        response_content = ?,
                        duration_ms = ?,
                        input_tokens = ?,
                        output_tokens = ?,
                        reasoning_tokens = ?,
                        cached_tokens = ?,
                        visible_output_tokens = ?,
                        raw_usage = ?,
                        is_accepted = ?,
                        fusion_context_chars = COALESCE(?, fusion_context_chars),
                        fusion_context_tokens = COALESCE(?, fusion_context_tokens),
                        selected_file_count = COALESCE(?, selected_file_count),
                        selected_files = COALESCE(?, selected_files),
                        selected_symbols = COALESCE(?, selected_symbols),
                        context_expansion_round = COALESCE(?, context_expansion_round)
                    WHERE id = ?;
                    """,
                    (
                        status_final,
                        response_content,
                        duration_ms,
                        input_tokens,
                        output_tokens,
                        reasoning_tokens,
                        cached_tokens,
                        visible_output_tokens,
                        raw_usage_str,
                        1 if is_accepted else 0,
                        fusion_context_chars,
                        fusion_context_tokens,
                        selected_file_count,
                        sel_files_str,
                        sel_syms_str,
                        context_expansion_round,
                        run_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE agent_runs
                    SET status = ?,
                        response_content = ?,
                        duration_ms = ?,
                        input_tokens = ?,
                        output_tokens = ?,
                        reasoning_tokens = ?,
                        cached_tokens = ?,
                        visible_output_tokens = ?,
                        raw_usage = ?,
                        is_accepted = ?
                    WHERE id = ?;
                    """,
                    (
                        status_final,
                        response_content,
                        duration_ms,
                        input_tokens,
                        output_tokens,
                        reasoning_tokens,
                        cached_tokens,
                        visible_output_tokens,
                        raw_usage_str,
                        1 if is_accepted else 0,
                        run_id,
                    ),
                )

    def fail_provider_stage(self, run_id: str, error_message: str, duration_ms: float = 0.0) -> None:
        """Mark a STARTED provider stage record as FAILED."""
        if isinstance(run_id, tuple):
            run_id = run_id[0]
        conn = self.db.connect()
        with conn:
            conn.execute(
                "UPDATE agent_runs SET status = 'FAILED', response_content = ?, duration_ms = ?, is_accepted = 0 WHERE id = ?;",
                (f"Error: {error_message}", duration_ms, run_id),
            )

    def reconcile_stale_provider_runs(self, task_id: str) -> int:
        """Reconcile orphaned STARTED provider runs left behind by a crash to INTERRUPTED."""
        conn = self.db.connect()
        with conn:
            cursor = conn.execute(
                "UPDATE agent_runs SET status = 'INTERRUPTED', is_accepted = 0 WHERE task_id = ? AND status = 'STARTED';",
                (task_id,),
            )
            return cursor.rowcount

    def record_agent_run(
        self,
        task_id: str,
        provider_name: str,
        role: str,
        response_content: str,
        prompt_summary: str = "",
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        duration_ms: float = 0.0,
        status: str = "SUCCESS",
        plan_id: Optional[str] = None,
        step_id: Optional[str] = None,
        stage: Optional[str] = None,
        round_number: int = 0,
        **kwargs,
    ) -> str:
        """Legacy compatibility wrapper around record_provider_stage."""
        return self.record_provider_stage(
            task_id=task_id,
            stage=stage or "legacy_agent_run",
            provider_name=provider_name,
            role=role,
            response_content=response_content,
            prompt_summary=prompt_summary,
            duration_ms=duration_ms,
            status=status,
            plan_id=plan_id,
            step_id=step_id,
            round_number=round_number,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            **kwargs,
        )

    def record_verification(
        self,
        task_id: str,
        verification_type: Any,
        command: str,
        exit_code: int,
        passed: bool,
        duration_seconds: float = 0.0,
        stdout: str = "",
        stderr: str = "",
        plan_id: Optional[str] = None,
        step_id: Optional[str] = None,
    ) -> str:
        """Persist a normalized verification run into SQLite."""
        conn = self.db.connect()
        verif_id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()
        v_type = verification_type.value if isinstance(verification_type, VerificationType) else str(verification_type)

        # Bounded summaries (max 2000 chars)
        out_summary = stdout[:2000] if stdout else ""
        err_summary = stderr[:2000] if stderr else ""

        with conn:
            task_row = conn.execute("SELECT 1 FROM tasks WHERE id = ?;", (task_id,)).fetchone()
            if not task_row:
                conn.execute(
                    "INSERT OR IGNORE INTO projects (id, name, root_path) VALUES (?, ?, ?);",
                    ("default", "Default Project", "."),
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO tasks (
                        id, project_id, title, description, status, task_type, complexity, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (task_id, "default", task_id, "", "IN_PROGRESS", "FEATURE", "COMPLEX", now),
                )

            conn.execute(
                """
                INSERT INTO verifications (
                    id, task_id, plan_id, step_id, verification_type, command,
                    exit_code, passed, duration_seconds, stdout_summary, stderr_summary, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    verif_id,
                    task_id,
                    plan_id,
                    step_id,
                    v_type,
                    str(command),
                    exit_code,
                    1 if passed else 0,
                    duration_seconds,
                    out_summary,
                    err_summary,
                    now,
                ),
            )
        return verif_id

    def get_verifications_for_task(self, task_id: str) -> List[Dict[str, Any]]:
        """Retrieve all recorded verifications for a given task."""
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT * FROM verifications WHERE task_id = ? ORDER BY timestamp ASC;",
            (task_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_task_lifecycle_summary(self, task_id: str) -> Dict[str, Any]:
        """Reconstruct authoritative execution state for a task."""
        conn = self.db.connect()
        task_row = conn.execute("SELECT * FROM tasks WHERE id = ?;", (task_id,)).fetchone()
        if not task_row:
            return {}
        task_dict = dict(task_row)

        plan_row = conn.execute("SELECT * FROM plans WHERE task_id = ? ORDER BY created_at DESC LIMIT 1;", (task_id,)).fetchone()
        steps = []
        if plan_row:
            step_rows = conn.execute(
                "SELECT * FROM plan_steps WHERE plan_id = ? ORDER BY step_index ASC;",
                (plan_row["id"],),
            ).fetchall()
            steps = [dict(s) for s in step_rows]

        checkpoints = conn.execute(
            "SELECT * FROM checkpoints WHERE task_id = ? ORDER BY created_at ASC;",
            (task_id,),
        ).fetchall()

        runs = conn.execute(
            "SELECT * FROM agent_runs WHERE task_id = ? ORDER BY timestamp ASC;",
            (task_id,),
        ).fetchall()

        verifications = conn.execute(
            "SELECT * FROM verifications WHERE task_id = ? ORDER BY timestamp ASC;",
            (task_id,),
        ).fetchall()

        reviews = conn.execute(
            "SELECT * FROM reviews WHERE task_id = ? ORDER BY timestamp ASC;",
            (task_id,),
        ).fetchall()

        last_checkpoint = dict(checkpoints[-1]) if checkpoints else None
        last_run = dict(runs[-1]) if runs else None
        last_review = dict(reviews[-1]) if reviews else None
        last_final_verif = next((dict(v) for v in reversed(verifications) if v["verification_type"] == "FINAL"), None)

        return {
            "task_id": task_id,
            "status": task_dict["status"],
            "active_stage": task_dict.get("active_stage"),
            "plan_status": plan_row["status"] if plan_row else None,
            "finished_steps": [s["id"] for s in steps if s["status"] == "COMPLETED"],
            "total_steps": len(steps),
            "last_verified_checkpoint": last_checkpoint["commit_sha"] if last_checkpoint else task_dict.get("last_checkpoint_sha"),
            "last_completed_provider_call": {
                "stage": last_run.get("stage"),
                "provider": last_run.get("provider_name"),
                "status": last_run.get("status"),
            } if last_run else None,
            "full_verification_passed": bool(last_final_verif["passed"]) if last_final_verif else bool(task_dict.get("verification_passed")),
            "peer_review_approved": (last_review.get("status") == "APPROVED") if last_review else False,
            "promotion_disposition": task_dict.get("promotion_disposition", "NOT_OFFERED"),
        }

    def record_review(
        self,
        task_id: str,
        reviewer_provider: str,
        subject_agent: str,
        status: ReviewStatus,
        comments: str,
    ) -> str:
        """Record a cross-model review outcome."""
        conn = self.db.connect()
        rev_id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()
        with conn:
            conn.execute(
                """
                INSERT INTO reviews (id, task_id, reviewer_provider, subject_agent, status, comments, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (rev_id, task_id, reviewer_provider, subject_agent, status.value, comments, now),
            )
        return rev_id

    # --- 3-Tier Context Snapshot Generation ---

    def build_context_snapshot(
        self,
        project_id: str,
        current_task: Optional[Task] = None,
        max_context_chars: int = 3500,
        max_recent_items: int = 3,
        peer_context: str = "",
        max_peer_message_chars: int = 1800,
    ) -> ContextSnapshot:
        """Construct a bounded, task-relevant 3-tier context snapshot."""
        conn = self.db.connect()
        project = conn.execute("SELECT * FROM projects WHERE id = ?;", (project_id,)).fetchone()
        if not project:
            return ContextSnapshot()

        # 1. Permanent Context (Concise mission + top key decisions)
        decisions = self.list_decisions(project_id, limit=3)
        decisions_summary = "\n".join([
            f"- {d['title']}: {d['decision'][:120]}" + ("..." if len(d['decision']) > 120 else "")
            for d in decisions
        ]) or "None yet."

        permanent = (
            f"PROJECT: {project['name']}\n"
            f"GOAL: {project['goal'] or 'Not specified'}\n"
            f"MILESTONE: {project['current_milestone'] or 'In progress'}\n"
            f"KEY DECISIONS:\n{decisions_summary}"
        )

        # 2. Current Context
        if current_task:
            desc = current_task.description or "No extra description"
            if len(desc) > 600:
                desc = desc[:600] + " ... [trimmed]"
            current = (
                f"ACTIVE TASK ID: {current_task.id}\n"
                f"TITLE: {current_task.title}\n"
                f"TYPE: {current_task.task_type.value}\n"
                f"COMPLEXITY: {current_task.complexity.value}\n"
                f"DETAILS: {desc}"
            )
        else:
            current = "No active task in progress."

        # 3. Recent Context (bounded to max_recent_items)
        recent_tasks = conn.execute(
            "SELECT title, status FROM tasks WHERE project_id = ? AND status = 'COMPLETED' ORDER BY completed_at DESC LIMIT ?;",
            (project_id, max_recent_items),
        ).fetchall()
        completed_summary = "\n".join([f"- {r['title']} [COMPLETED]" for r in recent_tasks]) or "None."

        recent_runs = conn.execute(
            """
            SELECT provider_name, role, prompt_summary 
            FROM agent_runs ar
            JOIN tasks t ON ar.task_id = t.id
            WHERE t.project_id = ?
            ORDER BY ar.timestamp DESC LIMIT ?;
            """,
            (project_id, max_recent_items),
        ).fetchall()
        runs_summary = "\n".join([
            f"- [{r['provider_name']}] {r['prompt_summary'][:80]}" + ("..." if len(r['prompt_summary']) > 80 else "")
            for r in recent_runs
        ]) or "No prior runs."

        recent = f"RECENT WORK:\n{completed_summary}\n\nRECENT ACTIVITY:\n{runs_summary}"

        # 4. Peer Context (bounded to max_peer_message_chars)
        bounded_peer = peer_context.strip()
        if len(bounded_peer) > max_peer_message_chars:
            bounded_peer = bounded_peer[:max_peer_message_chars] + "\n... [truncated for context budget]"

        snapshot = ContextSnapshot(
            permanent_context=permanent,
            current_context=current,
            recent_context=recent,
            peer_context=bounded_peer,
        )
        # Compute and record metrics
        snapshot.to_prompt_context()
        return snapshot

    # --- Milestone 8: ExecutionPlan & Checkpoint Operations ---

    def create_plan(self, plan: ExecutionPlan) -> str:
        """Persist a new ExecutionPlan and its steps into SQLite."""
        conn = self.db.connect()
        now = datetime.now(timezone.utc).isoformat()
        p_status = plan.status.value if isinstance(plan.status, PlanStatus) else str(plan.status)
        with conn:
            task_row = conn.execute("SELECT 1 FROM tasks WHERE id = ?;", (plan.task_id,)).fetchone()
            if not task_row:
                conn.execute(
                    "INSERT OR IGNORE INTO projects (id, name, root_path) VALUES (?, ?, ?);",
                    ("default", "Default Project", "."),
                )
                conn.execute(
                    """
                    INSERT OR IGNORE INTO tasks (
                        id, project_id, title, description, status, task_type, complexity, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (plan.task_id, "default", plan.task_id, "", "IN_PROGRESS", "FEATURE", "COMPLEX", now),
                )

            conn.execute(
                """
                INSERT INTO plans (id, task_id, title, summary, status, max_steps, amendments_count, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status=excluded.status,
                    summary=excluded.summary,
                    updated_at=excluded.updated_at;
                """,
                (
                    plan.plan_id,
                    plan.task_id,
                    plan.title,
                    plan.summary,
                    p_status,
                    len(plan.steps),
                    plan.amendments_count,
                    plan.created_at or now,
                    plan.updated_at or now,
                ),
            )
            for idx, step in enumerate(plan.steps):
                conn.execute(
                    """
                    INSERT INTO plan_steps (
                        id, plan_id, step_index, objective, rationale, expected_files, expected_symbols,
                        dependencies, verification_expectations, risk_level, estimated_complexity,
                        status, provider_name, repair_rounds, created_at, completed_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(plan_id, id) DO UPDATE SET
                        status=excluded.status,
                        provider_name=excluded.provider_name,
                        repair_rounds=excluded.repair_rounds,
                        completed_at=excluded.completed_at;
                    """,
                    (
                        step.id,
                        plan.plan_id,
                        idx,
                        step.objective,
                        step.rationale,
                        json.dumps(step.expected_files),
                        json.dumps(step.expected_symbols),
                        json.dumps(step.dependencies),
                        step.verification_expectations,
                        step.risk_level.value,
                        step.estimated_complexity.value,
                        step.status.value,
                        step.result.provider if step.result else None,
                        step.result.repair_rounds if step.result else 0,
                        now,
                        now if step.status in (StepStatus.COMPLETED, StepStatus.FAILED, StepStatus.SKIPPED) else None,
                    ),
                )
        return plan.plan_id

    def get_plan(self, plan_id: str) -> Optional[ExecutionPlan]:
        """Load an ExecutionPlan and all its steps from SQLite."""
        conn = self.db.connect()
        plan_row = conn.execute("SELECT * FROM plans WHERE id = ?;", (plan_id,)).fetchone()
        if not plan_row:
            return None

        step_rows = conn.execute(
            "SELECT * FROM plan_steps WHERE plan_id = ? ORDER BY step_index ASC;",
            (plan_id,),
        ).fetchall()

        steps: List[PlanStep] = []
        for r in step_rows:
            step_id = r["id"]
            status = StepStatus(r["status"])
            result = None
            if r["provider_name"] or status in (StepStatus.COMPLETED, StepStatus.FAILED):
                result = StepResult(
                    step_id=step_id,
                    status=status,
                    files_modified=json.loads(r["expected_files"] or "[]"),
                    checkpoint_sha=None,
                    verification_passed=(status == StepStatus.COMPLETED),
                    provider=r["provider_name"] or "",
                    repair_rounds=r["repair_rounds"] or 0,
                )

            step = PlanStep(
                id=step_id,
                objective=r["objective"],
                rationale=r["rationale"] or "",
                expected_files=json.loads(r["expected_files"] or "[]"),
                expected_symbols=json.loads(r["expected_symbols"] or "[]"),
                dependencies=json.loads(r["dependencies"] or "[]"),
                verification_expectations=r["verification_expectations"],
                risk_level=ReviewRisk(r["risk_level"]),
                estimated_complexity=Complexity(r["estimated_complexity"]),
                status=status,
                result=result,
            )
            steps.append(step)

        raw_status = plan_row["status"]
        try:
            p_status = PlanStatus(raw_status)
        except (ValueError, KeyError):
            p_status = PlanStatus.PREPARED

        return ExecutionPlan(
            plan_id=plan_row["id"],
            task_id=plan_row["task_id"],
            title=plan_row["title"],
            summary=plan_row["summary"] or "",
            steps=steps,
            current_step_index=0,
            amendments_count=plan_row["amendments_count"] or 0,
            status=p_status,
            created_at=plan_row["created_at"],
            updated_at=plan_row["updated_at"],
        )

    def get_plan_by_task_id(self, task_id: str) -> Optional[ExecutionPlan]:
        """Fetch the latest ExecutionPlan associated with a given task."""
        conn = self.db.connect()
        row = conn.execute(
            "SELECT id FROM plans WHERE task_id = ? ORDER BY created_at DESC LIMIT 1;",
            (task_id,),
        ).fetchone()
        if not row:
            return None
        return self.get_plan(row["id"])

    def update_plan_status(self, plan_id: str, status: Union[str, PlanStatus], amendments_count: Optional[int] = None) -> None:
        """Update overall plan status and amendment count."""
        conn = self.db.connect()
        now = datetime.now(timezone.utc).isoformat()
        status_val = status.value if isinstance(status, PlanStatus) else str(status)
        with conn:
            if amendments_count is not None:
                conn.execute(
                    "UPDATE plans SET status = ?, amendments_count = ?, updated_at = ? WHERE id = ?;",
                    (status_val, amendments_count, now, plan_id),
                )
            else:
                conn.execute(
                    "UPDATE plans SET status = ?, updated_at = ? WHERE id = ?;",
                    (status_val, now, plan_id),
                )

    def update_step_status(
        self,
        plan_id: str,
        step_id: str,
        status: StepStatus,
        result: Optional[StepResult] = None,
    ) -> None:
        """Update status and result of a single PlanStep in SQLite."""
        conn = self.db.connect()
        now = datetime.now(timezone.utc).isoformat()
        with conn:
            prov = result.provider if result else None
            rep = result.repair_rounds if result else 0
            completed_at = now if status in (StepStatus.COMPLETED, StepStatus.FAILED, StepStatus.SKIPPED) else None
            conn.execute(
                """
                UPDATE plan_steps
                SET status = ?, provider_name = COALESCE(?, provider_name), repair_rounds = ?, completed_at = ?
                WHERE plan_id = ? AND id = ?;
                """,
                (status.value, prov, rep, completed_at, plan_id, step_id),
            )

    def get_step(self, plan_id: str, step_id: str) -> Optional[PlanStep]:
        """Fetch a single PlanStep by plan_id and step_id."""
        plan = self.get_plan(plan_id)
        if not plan:
            return None
        for s in plan.steps:
            if s.id == step_id:
                return s
        return None

    def record_checkpoint(self, checkpoint: Checkpoint) -> str:
        """Persist a verified commit Checkpoint into SQLite."""
        conn = self.db.connect()
        now = datetime.now(timezone.utc).isoformat()
        with conn:
            conn.execute(
                """
                INSERT INTO checkpoints (
                    id, plan_id, task_id, step_id, commit_sha, base_commit_sha, files_changed,
                    diff_summary, verification_passed, provider_name, input_tokens, output_tokens,
                    fusion_context_tokens, duration_ms, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    checkpoint.checkpoint_id,
                    checkpoint.plan_id,
                    checkpoint.task_id,
                    checkpoint.step_id,
                    checkpoint.commit_sha,
                    checkpoint.base_commit_sha,
                    json.dumps(checkpoint.files_changed),
                    checkpoint.diff_summary,
                    1 if checkpoint.verification_passed else 0,
                    checkpoint.provider_name,
                    checkpoint.token_metrics.get("input_tokens", 0) if checkpoint.token_metrics else 0,
                    checkpoint.token_metrics.get("output_tokens", 0) if checkpoint.token_metrics else 0,
                    checkpoint.token_metrics.get("fusion_context_tokens", 0) if checkpoint.token_metrics else 0,
                    checkpoint.token_metrics.get("duration_ms", 0.0) if checkpoint.token_metrics else 0.0,
                    checkpoint.created_at or now,
                ),
            )
        return checkpoint.checkpoint_id

    def get_checkpoints_for_plan(self, plan_id: str) -> List[Checkpoint]:
        """Retrieve all checkpoints recorded for a given plan."""
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT * FROM checkpoints WHERE plan_id = ? ORDER BY created_at ASC;",
            (plan_id,),
        ).fetchall()
        checkpoints: List[Checkpoint] = []
        for r in rows:
            checkpoints.append(
                Checkpoint(
                    checkpoint_id=r["id"],
                    plan_id=r["plan_id"],
                    task_id=r["task_id"],
                    step_id=r["step_id"],
                    commit_sha=r["commit_sha"],
                    base_commit_sha=r["base_commit_sha"],
                    files_changed=json.loads(r["files_changed"] or "[]"),
                    diff_summary=r["diff_summary"] or "",
                    verification_passed=bool(r["verification_passed"]),
                    provider_name=r["provider_name"] or "",
                    token_metrics={
                        "input_tokens": r["input_tokens"],
                        "output_tokens": r["output_tokens"],
                        "fusion_context_tokens": r["fusion_context_tokens"],
                        "duration_ms": r["duration_ms"],
                    },
                    created_at=r["created_at"],
                )
            )
        return checkpoints

    def get_checkpoints_for_task(self, task_id: str) -> List[Dict[str, Any]]:
        """Retrieve all checkpoints for a task ordered by creation time."""
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT * FROM checkpoints WHERE task_id = ? ORDER BY created_at ASC;",
            (task_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def create_checkpoint(self, checkpoint: Checkpoint) -> str:
        """Alias for record_checkpoint."""
        return self.record_checkpoint(checkpoint)

    def get_checkpoint(self, checkpoint_id: str) -> Optional[Checkpoint]:
        """Fetch a checkpoint by its ID."""
        conn = self.db.connect()
        row = conn.execute("SELECT * FROM checkpoints WHERE id = ?;", (checkpoint_id,)).fetchone()
        if not row:
            return None
        r = dict(row)
        return Checkpoint(
            checkpoint_id=r["id"],
            plan_id=r["plan_id"],
            task_id=r["task_id"],
            step_id=r["step_id"],
            commit_sha=r["commit_sha"],
            base_commit_sha=r["base_commit_sha"],
            files_changed=json.loads(r["files_changed"] or "[]"),
            diff_summary=r["diff_summary"] or "",
            verification_passed=bool(r["verification_passed"]),
            provider_name=r["provider_name"] or "",
            token_metrics={
                "input_tokens": r["input_tokens"],
                "output_tokens": r["output_tokens"],
                "fusion_context_tokens": r["fusion_context_tokens"],
                "duration_ms": r["duration_ms"],
            },
            created_at=r["created_at"],
        )

    # --- Write-Ahead Checkpoint Transactions ---

    def record_checkpoint_transaction(self, txn: CheckpointTransaction) -> None:
        """Persist a CheckpointTransaction object into SQLite."""
        conn = self.db.connect()
        now = datetime.now(timezone.utc).isoformat()
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO checkpoint_transactions (
                    id, task_id, plan_id, step_id, expected_parent_sha,
                    verification_id, verified, approved_paths, status, created_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    txn.id, txn.task_id, txn.plan_id, txn.step_id, txn.expected_parent_sha,
                    txn.verification_id, 1 if txn.verified else 0,
                    json.dumps(txn.approved_paths) if isinstance(txn.approved_paths, list) else txn.approved_paths,
                    txn.status.value if hasattr(txn.status, "value") else str(txn.status),
                    txn.created_at or now, txn.completed_at,
                ),
            )

    def record_checkpoint_transaction_intent(
        self,
        checkpoint_id: str,
        task_id: str,
        plan_id: str,
        step_id: str,
        expected_parent_sha: str,
        verification_id: str,
        approved_paths: List[str],
        verified: bool = True,
    ) -> str:
        """Write-ahead persistence of checkpoint creation intent before git commit."""
        conn = self.db.connect()
        now = datetime.now(timezone.utc).isoformat()
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO checkpoint_transactions (
                    id, task_id, plan_id, step_id, expected_parent_sha,
                    verification_id, verified, approved_paths, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PREPARING', ?);
                """,
                (
                    checkpoint_id, task_id, plan_id, step_id, expected_parent_sha,
                    verification_id, 1 if verified else 0, json.dumps(approved_paths), now,
                ),
            )
        return checkpoint_id

    def complete_checkpoint_transaction(self, checkpoint_id: str) -> None:
        """Mark a checkpoint transaction COMPLETED after git commit succeeds."""
        conn = self.db.connect()
        now = datetime.now(timezone.utc).isoformat()
        with conn:
            conn.execute(
                "UPDATE checkpoint_transactions SET status = 'COMPLETED', completed_at = ? WHERE id = ?;",
                (now, checkpoint_id),
            )

    def fail_checkpoint_transaction(self, checkpoint_id: str) -> None:
        """Mark a checkpoint transaction FAILED."""
        conn = self.db.connect()
        now = datetime.now(timezone.utc).isoformat()
        with conn:
            conn.execute(
                "UPDATE checkpoint_transactions SET status = 'FAILED', completed_at = ? WHERE id = ?;",
                (now, checkpoint_id),
            )

    def get_checkpoint_transaction(self, checkpoint_id: str) -> Optional[CheckpointTransaction]:
        """Fetch checkpoint transaction by ID."""
        conn = self.db.connect()
        row = conn.execute("SELECT * FROM checkpoint_transactions WHERE id = ?;", (checkpoint_id,)).fetchone()
        if not row:
            return None
        return CheckpointTransaction.from_dict(dict(row))

    # --- Write-Ahead Promotion Transactions ---

    def record_promotion_transaction(self, txn: PromotionTransaction) -> None:
        """Persist promotion transaction record in initial status."""
        conn = self.db.connect()
        now = datetime.now(timezone.utc).isoformat()
        with conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO promotion_transactions (
                    id, task_id, target_branch, expected_target_sha, task_branch,
                    task_head_sha, diff_hash, status, resulting_target_sha, error_message, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    txn.id, txn.task_id, txn.target_branch, txn.expected_target_sha,
                    txn.task_branch, txn.task_head_sha, txn.diff_hash,
                    txn.status.value if hasattr(txn.status, "value") else str(txn.status),
                    txn.resulting_target_sha, txn.error_message, txn.started_at or now,
                ),
            )

    def update_promotion_transaction(
        self,
        txn_id: str,
        status: PromotionTransactionStatus,
        resulting_target_sha: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """Update promotion transaction outcome."""
        conn = self.db.connect()
        now = datetime.now(timezone.utc).isoformat()
        with conn:
            conn.execute(
                """
                UPDATE promotion_transactions
                SET status = ?, resulting_target_sha = COALESCE(?, resulting_target_sha),
                    error_message = ?, completed_at = ?
                WHERE id = ?;
                """,
                (
                    status.value if hasattr(status, "value") else str(status),
                    resulting_target_sha, error_message, now, txn_id,
                ),
            )

    def get_active_promotion_transaction(self, task_id: str) -> Optional[PromotionTransaction]:
        """Fetch the latest promotion transaction for a task."""
        conn = self.db.connect()
        row = conn.execute(
            "SELECT * FROM promotion_transactions WHERE task_id = ? ORDER BY started_at DESC LIMIT 1;",
            (task_id,),
        ).fetchone()
        if not row:
            return None
        return PromotionTransaction.from_dict(dict(row))

    def get_promotion_transaction(self, txn_id: str) -> Optional[PromotionTransaction]:
        """Fetch a promotion transaction by its ID."""
        conn = self.db.connect()
        row = conn.execute(
            "SELECT * FROM promotion_transactions WHERE id = ?;",
            (txn_id,),
        ).fetchone()
        if not row:
            return None
        return PromotionTransaction.from_dict(dict(row))

    # --- Diagnostic Task Locks ---

    def record_task_lock(self, task_id: str, owner_id: str, pid: int, hostname: str) -> None:
        """Record diagnostic task execution lock in SQLite."""
        conn = self.db.connect()
        now = datetime.now(timezone.utc).isoformat()
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO task_locks (task_id, owner_id, pid, hostname, acquired_at) VALUES (?, ?, ?, ?, ?);",
                (task_id, owner_id, pid, hostname, now),
            )

    def release_task_lock(self, task_id: str, owner_id: str) -> None:
        """Remove diagnostic task execution lock from SQLite."""
        conn = self.db.connect()
        with conn:
            conn.execute(
                "DELETE FROM task_locks WHERE task_id = ? AND owner_id = ?;",
                (task_id, owner_id),
            )

    def get_task_lock(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve diagnostic lock record."""
        conn = self.db.connect()
        row = conn.execute("SELECT * FROM task_locks WHERE task_id = ?;", (task_id,)).fetchone()
        return dict(row) if row else None

    # --- Recovery Scanning ---

    def get_recoverable_tasks(self) -> List[Dict[str, Any]]:
        """List tasks that can be safely resumed.

        Scans tasks in INTERRUPTED, RECOVERABLE, or stale RUNNING/RESUMING (with no active OS lock).
        """
        from fusion_agent.workspace.lock import TaskExecutionLock
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT * FROM tasks WHERE status IN ('INTERRUPTED', 'RECOVERABLE', 'RUNNING', 'RESUMING', 'IN_PROGRESS') ORDER BY created_at DESC;"
        ).fetchall()
        recoverable = []
        for r in rows:
            t_dict = dict(r)
            task_id = t_dict["id"]
            status = t_dict["status"]
            if status in ("RUNNING", "IN_PROGRESS", "RESUMING"):
                if TaskExecutionLock.is_locked(task_id):
                    continue
                else:
                    self.update_task_status(task_id, TaskStatus.RECOVERABLE)
                    t_dict["status"] = TaskStatus.RECOVERABLE.value
            recoverable.append(t_dict)
        return recoverable

    # --- MCP Invocations Ledger ---

    def _ensure_task_exists(self, conn: sqlite3.Connection, task_id: str) -> None:
        """Ensure a task record exists to satisfy foreign key constraints for ad-hoc or test invocations."""
        if not task_id:
            return
        task_row = conn.execute("SELECT id FROM tasks WHERE id = ?;", (task_id,)).fetchone()
        if not task_row:
            proj_row = conn.execute("SELECT id FROM projects LIMIT 1;").fetchone()
            if proj_row:
                p_id = proj_row[0]
            else:
                p_id = "default-mcp-proj"
                conn.execute("INSERT OR IGNORE INTO projects (id, name, root_path) VALUES (?, ?, ?);", (p_id, "Default Project", "/tmp"))
            conn.execute(
                "INSERT OR IGNORE INTO tasks (id, project_id, title, status, task_type, complexity) VALUES (?, ?, ?, 'RUNNING', 'CODE_MODIFICATION', 'LOW');",
                (task_id, p_id, f"Auto-created task {task_id}"),
            )

    def record_mcp_invocation_started(
        self,
        invocation_id: str,
        task_id: str,
        server_id: str,
        tool_name: str,
        arguments_hash: str,
        sanitized_arguments: str,
        policy_decision: str,
        approval_disposition: str,
        plan_id: Optional[str] = None,
        step_id: Optional[str] = None,
        provider_stage: str = "implementation",
        logical_tool_invocation_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Tuple[str, int]:
        """Write-ahead persistence of an MCP tool invocation in STARTED status."""
        conn = self.db.connect()
        now = datetime.now(timezone.utc).isoformat()
        if not logical_tool_invocation_id:
            logical_tool_invocation_id = f"{task_id}:{plan_id or 'none'}:{step_id or 'none'}:{provider_stage}:{server_id}:{tool_name}"

        with conn:
            self._ensure_task_exists(conn, task_id)
            att_row = conn.execute(
                "SELECT MAX(attempt_number) FROM mcp_tool_invocations WHERE logical_tool_invocation_id = ?;",
                (logical_tool_invocation_id,),
            ).fetchone()
            attempt_number = (att_row[0] or 0) + 1 if att_row else 1

            conn.execute(
                """
                INSERT INTO mcp_tool_invocations (
                    id, task_id, plan_id, step_id, provider_stage, server_id, tool_name,
                    arguments_hash, sanitized_arguments, policy_decision, approval_disposition,
                    status, duration_ms, result_chars, result_tokens, is_truncated,
                    error_message, logical_tool_invocation_id, attempt_number, idempotency_key, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'STARTED', 0.0, 0, 0, 0, NULL, ?, ?, ?, ?);
                """,
                (
                    invocation_id,
                    task_id,
                    plan_id,
                    step_id,
                    provider_stage,
                    server_id,
                    tool_name,
                    arguments_hash,
                    sanitized_arguments,
                    policy_decision,
                    approval_disposition,
                    logical_tool_invocation_id,
                    attempt_number,
                    idempotency_key,
                    now,
                ),
            )
        return invocation_id, attempt_number

    def complete_mcp_invocation(
        self,
        invocation_id: str,
        status: str = "COMPLETED",
        duration_ms: float = 0.0,
        result_chars: int = 0,
        result_tokens: int = 0,
        is_truncated: bool = False,
        error_message: Optional[str] = None,
        is_accepted: bool = False,
    ) -> None:
        """Mark an MCP tool invocation as COMPLETED, FAILED, or INTERRUPTED."""
        conn = self.db.connect()
        with conn:
            conn.execute(
                """
                UPDATE mcp_tool_invocations
                SET status = ?, duration_ms = ?, result_chars = ?, result_tokens = ?,
                    is_truncated = ?, error_message = ?, is_accepted = ?
                WHERE id = ?;
                """,
                (
                    status,
                    duration_ms,
                    result_chars,
                    result_tokens,
                    1 if is_truncated else 0,
                    error_message,
                    1 if is_accepted else 0,
                    invocation_id,
                ),
            )

    def record_mcp_invocation(
        self,
        task_id: str,
        server_id: str,
        tool_name: str,
        arguments_hash: str,
        sanitized_arguments: str,
        policy_decision: str,
        approval_disposition: str,
        status: str,
        plan_id: Optional[str] = None,
        step_id: Optional[str] = None,
        provider_stage: str = "implementation",
        error_message: Optional[str] = None,
        is_accepted: bool = False,
    ) -> str:
        """Immediately record a one-shot MCP invocation (e.g. DENIED or DECLINED)."""
        conn = self.db.connect()
        inv_id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()
        logical_id = f"{task_id}:{plan_id or 'none'}:{step_id or 'none'}:{provider_stage}:{server_id}:{tool_name}"

        with conn:
            self._ensure_task_exists(conn, task_id)
            conn.execute(
                """
                INSERT INTO mcp_tool_invocations (
                    id, task_id, plan_id, step_id, provider_stage, server_id, tool_name,
                    arguments_hash, sanitized_arguments, policy_decision, approval_disposition,
                    status, duration_ms, result_chars, result_tokens, is_truncated,
                    error_message, logical_tool_invocation_id, attempt_number, is_accepted, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0.0, 0, 0, 0, ?, ?, 1, ?, ?);
                """,
                (
                    inv_id,
                    task_id,
                    plan_id,
                    step_id,
                    provider_stage,
                    server_id,
                    tool_name,
                    arguments_hash,
                    sanitized_arguments,
                    policy_decision,
                    approval_disposition,
                    status,
                    error_message,
                    logical_id,
                    1 if is_accepted else 0,
                    now,
                ),
            )
        return inv_id

    def get_mcp_invocations_for_task(self, task_id: str) -> List[Dict[str, Any]]:
        """Retrieve all MCP invocations for a task ordered chronologically."""
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT * FROM mcp_tool_invocations WHERE task_id = ? ORDER BY timestamp ASC;",
            (task_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def reconcile_stale_mcp_invocations(self, task_id: str) -> int:
        """Mark uncompleted STARTED MCP invocations as INTERRUPTED on crash recovery."""
        conn = self.db.connect()
        with conn:
            cur = conn.execute(
                "UPDATE mcp_tool_invocations SET status = 'INTERRUPTED' WHERE task_id = ? AND status = 'STARTED';",
                (task_id,),
            )
            return cur.rowcount
