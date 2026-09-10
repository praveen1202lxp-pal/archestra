"""Database connection manager and migration runner."""

import sqlite3
from pathlib import Path
from typing import Optional, Union

from fusion_agent.memory.schema import CREATE_TABLES_SQL, SCHEMA_VERSION


class Database:
    """Manages SQLite database connections and schema lifecycle."""

    def __init__(self, db_path: Union[str, Path] = ":memory:"):
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._connection: Optional[sqlite3.Connection] = None

    def connect(self) -> sqlite3.Connection:
        """Get or create connection with Row factory enabled."""
        if self._connection is None:
            self._connection = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
            )
            self._connection.row_factory = sqlite3.Row
            # Enable foreign keys and WAL mode for files
            self._connection.execute("PRAGMA foreign_keys = ON;")
            if self.db_path != ":memory:":
                self._connection.execute("PRAGMA journal_mode = WAL;")
            self.init_schema()
        return self._connection

    def init_schema(self) -> None:
        """Apply tables and indexes if not already initialized."""
        conn = self._connection or sqlite3.connect(self.db_path)
        with conn:
            conn.executescript(CREATE_TABLES_SQL)
            # Ensure new columns exist on existing databases (Tasks)
            for col in (
                "verification_passed INTEGER DEFAULT 0",
                "repair_rounds INTEGER DEFAULT 0",
                "promotion_disposition TEXT DEFAULT 'NOT_OFFERED'",
                "active_stage TEXT",
                "last_checkpoint_sha TEXT",
            ):
                try:
                    conn.execute(f"ALTER TABLE tasks ADD COLUMN {col};")
                except sqlite3.OperationalError:
                    pass

            # Ensure new columns exist on existing databases (Agent Runs)
            for col in (
                "plan_id TEXT",
                "step_id TEXT",
                "stage TEXT",
                "round_number INTEGER DEFAULT 0",
                "reasoning_tokens INTEGER",
                "cached_tokens INTEGER",
                "visible_output_tokens INTEGER",
                "raw_usage TEXT",
                "fusion_context_chars INTEGER",
                "fusion_context_tokens INTEGER",
                "selected_file_count INTEGER DEFAULT 0",
                "selected_files TEXT",
                "selected_symbols TEXT",
                "context_expansion_round INTEGER DEFAULT 0",
            ):
                try:
                    conn.execute(f"ALTER TABLE agent_runs ADD COLUMN {col};")
                except sqlite3.OperationalError:
                    pass

            try:
                conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_runs_stage ON agent_runs(stage);")
            except sqlite3.OperationalError:
                pass

            # Migrate plan_steps to composite primary key (plan_id, id) if needed
            try:
                pk_cols = [r[1] for r in conn.execute("PRAGMA table_info(plan_steps)") if r[5] > 0]
                if pk_cols == ["id"]:
                    conn.execute("""
                        CREATE TABLE plan_steps_mig (
                            id TEXT NOT NULL,
                            plan_id TEXT NOT NULL,
                            step_index INTEGER NOT NULL,
                            objective TEXT NOT NULL,
                            rationale TEXT,
                            expected_files TEXT,
                            expected_symbols TEXT,
                            dependencies TEXT,
                            verification_expectations TEXT,
                            risk_level TEXT NOT NULL,
                            estimated_complexity TEXT NOT NULL,
                            status TEXT NOT NULL,
                            provider_name TEXT,
                            repair_rounds INTEGER DEFAULT 0,
                            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                            completed_at TEXT,
                            PRIMARY KEY (plan_id, id),
                            FOREIGN KEY(plan_id) REFERENCES plans(id)
                        );
                    """)
                    conn.execute("INSERT OR IGNORE INTO plan_steps_mig SELECT * FROM plan_steps;")
                    conn.execute("DROP TABLE plan_steps;")
                    conn.execute("ALTER TABLE plan_steps_mig RENAME TO plan_steps;")
            except sqlite3.OperationalError:
                pass

            # Ensure v4 recovery columns exist on tasks
            for col in (
                "interruption_reason TEXT",
                "interrupted_at TEXT",
                "recovery_attempts INTEGER DEFAULT 0",
                "resumed_at TEXT",
                "execution_config_snapshot TEXT",
                "repo_fingerprint TEXT",
                "base_commit TEXT",
            ):
                try:
                    conn.execute(f"ALTER TABLE tasks ADD COLUMN {col};")
                except sqlite3.OperationalError:
                    pass

            # Ensure v4 columns exist on agent_runs
            for col in (
                "attempt_number INTEGER DEFAULT 1",
                "logical_invocation_id TEXT",
                "is_accepted INTEGER DEFAULT 1",
            ):
                try:
                    conn.execute(f"ALTER TABLE agent_runs ADD COLUMN {col};")
                except sqlite3.OperationalError:
                    pass

            try:
                conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_runs_accepted_invocation ON agent_runs(logical_invocation_id) WHERE is_accepted = 1;")
            except sqlite3.OperationalError:
                pass

            # Ensure v4 checkpoint_transactions, promotion_transactions, and task_locks tables exist
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS checkpoint_transactions (
                        id TEXT PRIMARY KEY,
                        task_id TEXT NOT NULL,
                        plan_id TEXT NOT NULL,
                        step_id TEXT NOT NULL,
                        expected_parent_sha TEXT NOT NULL,
                        verification_id TEXT NOT NULL,
                        verified INTEGER NOT NULL DEFAULT 1,
                        approved_paths TEXT NOT NULL,
                        status TEXT NOT NULL,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        completed_at TEXT,
                        FOREIGN KEY(plan_id) REFERENCES plans(id),
                        FOREIGN KEY(task_id) REFERENCES tasks(id)
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_checkpoint_transactions_task ON checkpoint_transactions(task_id);")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS promotion_transactions (
                        id TEXT PRIMARY KEY,
                        task_id TEXT NOT NULL,
                        target_branch TEXT NOT NULL,
                        expected_target_sha TEXT NOT NULL,
                        task_branch TEXT NOT NULL,
                        task_head_sha TEXT NOT NULL,
                        diff_hash TEXT,
                        status TEXT NOT NULL,
                        resulting_target_sha TEXT,
                        error_message TEXT,
                        started_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        completed_at TEXT,
                        FOREIGN KEY(task_id) REFERENCES tasks(id)
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_promotion_transactions_task ON promotion_transactions(task_id);")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS task_locks (
                        task_id TEXT PRIMARY KEY,
                        owner_id TEXT NOT NULL,
                        pid INTEGER NOT NULL,
                        hostname TEXT,
                        acquired_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY(task_id) REFERENCES tasks(id)
                    );
                """)
            except sqlite3.OperationalError:
                pass

            # Ensure v5 mcp_tool_invocations table exists
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS mcp_tool_invocations (
                        id TEXT PRIMARY KEY,
                        task_id TEXT NOT NULL,
                        plan_id TEXT,
                        step_id TEXT,
                        provider_stage TEXT,
                        server_id TEXT NOT NULL,
                        tool_name TEXT NOT NULL,
                        arguments_hash TEXT NOT NULL,
                        sanitized_arguments TEXT,
                        policy_decision TEXT NOT NULL,
                        approval_disposition TEXT NOT NULL,
                        status TEXT NOT NULL,
                        duration_ms REAL DEFAULT 0.0,
                        result_chars INTEGER DEFAULT 0,
                        result_tokens INTEGER DEFAULT 0,
                        is_truncated INTEGER DEFAULT 0,
                        error_message TEXT,
                        logical_tool_invocation_id TEXT,
                        attempt_number INTEGER DEFAULT 1,
                        is_accepted INTEGER DEFAULT 0,
                        idempotency_key TEXT,
                        timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY(task_id) REFERENCES tasks(id)
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_mcp_invocations_task ON mcp_tool_invocations(task_id);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_mcp_invocations_logical ON mcp_tool_invocations(logical_tool_invocation_id);")
            except sqlite3.OperationalError:
                pass

            try:
                conn.execute("ALTER TABLE mcp_tool_invocations ADD COLUMN is_accepted INTEGER DEFAULT 0;")
            except sqlite3.OperationalError:
                pass

            try:
                conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_mcp_invocations_accepted ON mcp_tool_invocations(logical_tool_invocation_id) WHERE is_accepted = 1;")
            except sqlite3.OperationalError:
                pass

            cursor = conn.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1;")
            row = cursor.fetchone()
            if not row or row[0] < SCHEMA_VERSION:
                conn.execute("INSERT INTO schema_version (version) VALUES (?);", (SCHEMA_VERSION,))

    def close(self) -> None:
        """Close active connection."""
        if self._connection:
            self._connection.close()
            self._connection = None
