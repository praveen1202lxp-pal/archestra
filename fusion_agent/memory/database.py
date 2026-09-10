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
            # Ensure new columns exist on existing databases
            for col in ("verification_passed INTEGER DEFAULT 0", "repair_rounds INTEGER DEFAULT 0"):
                try:
                    conn.execute(f"ALTER TABLE tasks ADD COLUMN {col};")
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

            cursor = conn.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1;")
            row = cursor.fetchone()
            if not row:
                conn.execute("INSERT INTO schema_version (version) VALUES (?);", (SCHEMA_VERSION,))

    def close(self) -> None:
        """Close active connection."""
        if self._connection:
            self._connection.close()
            self._connection = None
