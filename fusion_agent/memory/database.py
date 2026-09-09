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

            cursor = conn.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1;")
            row = cursor.fetchone()
            if not row:
                conn.execute("INSERT INTO schema_version (version) VALUES (?);", (SCHEMA_VERSION,))

    def close(self) -> None:
        """Close active connection."""
        if self._connection:
            self._connection.close()
            self._connection = None
