"""Persistent project state manager and context snapshot generator."""

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fusion_agent.memory.database import Database
from fusion_agent.models.deliberation import ReviewStatus
from fusion_agent.models.task import Complexity, Task, TaskStatus, TaskType
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
    ) -> None:
        """Update task status and optional completion timestamp."""
        conn = self.db.connect()
        now = datetime.now(timezone.utc).isoformat()
        completed_at = now if status in (TaskStatus.COMPLETED, TaskStatus.FAILED) else None
        
        with conn:
            if selected_strategy:
                conn.execute(
                    "UPDATE tasks SET status = ?, selected_strategy = ?, completed_at = ? WHERE id = ?;",
                    (status.value, selected_strategy, completed_at, task_id),
                )
            else:
                conn.execute(
                    "UPDATE tasks SET status = ?, completed_at = ? WHERE id = ?;",
                    (status.value, completed_at, task_id),
                )

    def get_task(self, task_id: str) -> Optional[Task]:
        """Fetch a task by ID."""
        conn = self.db.connect()
        row = conn.execute("SELECT * FROM tasks WHERE id = ?;", (task_id,)).fetchone()
        if not row:
            return None
        return Task(
            id=row["id"],
            project_id=row["project_id"],
            title=row["title"],
            description=row["description"],
            task_type=TaskType(row["task_type"]),
            complexity=Complexity(row["complexity"]),
            status=TaskStatus(row["status"]),
            selected_strategy=row["selected_strategy"],
            created_at=row["created_at"],
            completed_at=row["completed_at"],
        )

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

    def record_agent_run(
        self,
        task_id: str,
        provider_name: str,
        role: str,
        response_content: str,
        prompt_summary: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        duration_ms: float = 0.0,
        status: str = "SUCCESS",
    ) -> str:
        """Record an individual agent invocation."""
        conn = self.db.connect()
        run_id = str(uuid.uuid4())[:8]
        now = datetime.now(timezone.utc).isoformat()
        with conn:
            conn.execute(
                """
                INSERT INTO agent_runs (
                    id, task_id, provider_name, role, prompt_summary,
                    response_content, input_tokens, output_tokens, duration_ms, status, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    run_id,
                    task_id,
                    provider_name,
                    role,
                    prompt_summary,
                    response_content,
                    input_tokens,
                    output_tokens,
                    duration_ms,
                    status,
                    now,
                ),
            )
        return run_id

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
