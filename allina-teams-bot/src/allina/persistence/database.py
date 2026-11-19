"""SQLite database manager and schema initialization."""

import os
import sqlite3
from typing import Optional


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS requests (
    request_id TEXT PRIMARY KEY,
    tenant_id TEXT,
    requester_aad_id TEXT NOT NULL,
    requester_name TEXT NOT NULL,
    original_message TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plans (
    request_id TEXT PRIMARY KEY REFERENCES requests(request_id),
    target_instance_id TEXT NOT NULL,
    target_dir TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    plan_hash TEXT NOT NULL,
    estimated_recovery_bytes INTEGER NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL REFERENCES requests(request_id),
    approver_aad_id TEXT NOT NULL,
    approver_name TEXT NOT NULL,
    decision TEXT NOT NULL,
    plan_hash TEXT NOT NULL,
    reason TEXT,
    decided_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL REFERENCES requests(request_id),
    command_id TEXT NOT NULL,
    status TEXT NOT NULL,
    dry_run INTEGER NOT NULL,
    output_summary TEXT,
    before_metrics_json TEXT,
    after_metrics_json TEXT,
    executed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    request_id TEXT NOT NULL,
    actor_aad_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    event_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    user_aad_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    service_url TEXT,
    tenant_id TEXT,
    last_seen_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_requests_status ON requests(status);
CREATE INDEX IF NOT EXISTS idx_audit_request ON audit_events(request_id);
"""


class DatabaseManager:
    """Manages SQLite connections and schema creation."""

    def __init__(self, db_path: str = "data/allina_poc.db"):
        self.db_path = db_path
        self._shared_conn: Optional[sqlite3.Connection] = None
        self._ensure_dir()
        if self.db_path == ":memory:":
            self._shared_conn = sqlite3.connect(":memory:", timeout=10.0, check_same_thread=False)
            self._shared_conn.row_factory = sqlite3.Row
            self._shared_conn.execute("PRAGMA foreign_keys = ON;")
        self.init_schema()

    def _ensure_dir(self):
        """Ensure the parent directory for SQLite DB exists."""
        if self.db_path != ":memory:":
            dirname = os.path.dirname(self.db_path)
            if dirname:
                os.makedirs(dirname, exist_ok=True)

    def get_connection(self) -> sqlite3.Connection:
        """Create and configure a SQLite connection."""
        if self._shared_conn is not None:
            return self._shared_conn
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        return conn

    def init_schema(self):
        """Execute table creation scripts."""
        conn = self.get_connection()
        conn.executescript(SCHEMA_SQL)
        conn.commit()
        if self._shared_conn is None:
            conn.close()
