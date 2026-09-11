"""SQLite database schema definitions for Fusion Agent shared memory."""

SCHEMA_VERSION = 5

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    root_path TEXT NOT NULL,
    goal TEXT,
    description TEXT,
    architecture TEXT,
    requirements TEXT,
    coding_conventions TEXT,
    current_milestone TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL,
    task_type TEXT NOT NULL,
    complexity TEXT NOT NULL,
    selected_strategy TEXT,
    verification_passed INTEGER DEFAULT 0,
    repair_rounds INTEGER DEFAULT 0,
    promotion_disposition TEXT DEFAULT 'NOT_OFFERED',
    active_stage TEXT,
    last_checkpoint_sha TEXT,
    interruption_reason TEXT,
    interrupted_at TEXT,
    recovery_attempts INTEGER DEFAULT 0,
    resumed_at TEXT,
    execution_config_snapshot TEXT,
    repo_fingerprint TEXT,
    base_commit TEXT,
    scope_expansions_json TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    FOREIGN KEY(project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS scope_expansions (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    file_path TEXT NOT NULL,
    category TEXT NOT NULL,
    reason TEXT NOT NULL,
    decision TEXT NOT NULL DEFAULT 'approved',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(task_id) REFERENCES tasks(id)
);
CREATE INDEX IF NOT EXISTS idx_scope_expansions_task ON scope_expansions(task_id);

CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    task_id TEXT,
    title TEXT NOT NULL,
    decision TEXT NOT NULL,
    rationale TEXT,
    agent_source TEXT,
    timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(project_id) REFERENCES projects(id),
    FOREIGN KEY(task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    plan_id TEXT,
    step_id TEXT,
    stage TEXT,
    round_number INTEGER DEFAULT 0,
    attempt_number INTEGER DEFAULT 1,
    logical_invocation_id TEXT,
    is_accepted INTEGER DEFAULT 1,
    provider_name TEXT NOT NULL,
    role TEXT NOT NULL,
    prompt_summary TEXT,
    response_content TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    duration_ms REAL DEFAULT 0.0,
    status TEXT NOT NULL,
    reasoning_tokens INTEGER,
    cached_tokens INTEGER,
    visible_output_tokens INTEGER,
    raw_usage TEXT,
    fusion_context_chars INTEGER,
    fusion_context_tokens INTEGER,
    selected_file_count INTEGER DEFAULT 0,
    selected_files TEXT,
    selected_symbols TEXT,
    context_expansion_round INTEGER DEFAULT 0,
    timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS reviews (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    reviewer_provider TEXT NOT NULL,
    subject_agent TEXT NOT NULL,
    status TEXT NOT NULL,
    comments TEXT,
    timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS plans (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT,
    status TEXT NOT NULL,
    max_steps INTEGER DEFAULT 5,
    amendments_count INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS plan_steps (
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

CREATE TABLE IF NOT EXISTS checkpoints (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    base_commit_sha TEXT NOT NULL,
    files_changed TEXT,
    diff_summary TEXT,
    verification_passed INTEGER DEFAULT 0,
    provider_name TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    fusion_context_tokens INTEGER DEFAULT 0,
    duration_ms REAL DEFAULT 0.0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(plan_id) REFERENCES plans(id),
    FOREIGN KEY(task_id) REFERENCES tasks(id)
);

CREATE TABLE IF NOT EXISTS verifications (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    plan_id TEXT,
    step_id TEXT,
    verification_type TEXT NOT NULL,
    command TEXT NOT NULL,
    exit_code INTEGER NOT NULL,
    passed INTEGER NOT NULL,
    duration_seconds REAL NOT NULL,
    stdout_summary TEXT,
    stderr_summary TEXT,
    timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(task_id) REFERENCES tasks(id)
);

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

CREATE TABLE IF NOT EXISTS task_locks (
    task_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    pid INTEGER NOT NULL,
    hostname TEXT,
    acquired_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(task_id) REFERENCES tasks(id)
);

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

CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_decisions_project ON decisions(project_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_task ON agent_runs(task_id);
CREATE INDEX IF NOT EXISTS idx_reviews_task ON reviews(task_id);
CREATE INDEX IF NOT EXISTS idx_plans_task ON plans(task_id);
CREATE INDEX IF NOT EXISTS idx_plan_steps_plan ON plan_steps(plan_id);
CREATE INDEX IF NOT EXISTS idx_checkpoints_plan ON checkpoints(plan_id);
CREATE INDEX IF NOT EXISTS idx_checkpoints_task ON checkpoints(task_id);
CREATE INDEX IF NOT EXISTS idx_checkpoint_transactions_task ON checkpoint_transactions(task_id);
CREATE INDEX IF NOT EXISTS idx_verifications_task ON verifications(task_id);
CREATE INDEX IF NOT EXISTS idx_verifications_plan ON verifications(plan_id);
CREATE INDEX IF NOT EXISTS idx_promotion_transactions_task ON promotion_transactions(task_id);
CREATE INDEX IF NOT EXISTS idx_mcp_invocations_task ON mcp_tool_invocations(task_id);
CREATE INDEX IF NOT EXISTS idx_mcp_invocations_logical ON mcp_tool_invocations(logical_tool_invocation_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mcp_invocations_accepted ON mcp_tool_invocations(logical_tool_invocation_id) WHERE is_accepted = 1;
"""
