BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS sync_runs (
    run_id TEXT PRIMARY KEY,
    adapter TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    started_at TEXT NOT NULL,
    observed_at TEXT,
    finished_at TEXT,
    max_pages INTEGER NOT NULL CHECK (max_pages > 0),
    source_total_count INTEGER CHECK (source_total_count IS NULL OR source_total_count >= 0),
    observed_count INTEGER NOT NULL DEFAULT 0 CHECK (observed_count >= 0),
    actionable_count INTEGER NOT NULL DEFAULT 0 CHECK (actionable_count >= 0),
    snapshot_count INTEGER NOT NULL DEFAULT 0 CHECK (snapshot_count >= 0),
    history_rows_inserted INTEGER NOT NULL DEFAULT 0 CHECK (history_rows_inserted >= 0),
    event_count INTEGER NOT NULL DEFAULT 0 CHECK (event_count >= 0),
    error_type TEXT,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS workflow_snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES sync_runs(run_id) ON DELETE RESTRICT,
    adapter TEXT NOT NULL,
    external_id TEXT NOT NULL CHECK (length(external_id) > 0),
    observed_at TEXT NOT NULL,
    reference_no TEXT NOT NULL DEFAULT '',
    project_no TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    applicant TEXT NOT NULL DEFAULT '',
    submitted_at TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    current_node TEXT NOT NULL DEFAULT '',
    current_approver TEXT NOT NULL DEFAULT '',
    waiting_days INTEGER CHECK (waiting_days IS NULL OR waiting_days >= 0),
    source_url TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL CHECK (active IN (0, 1)),
    actionable INTEGER NOT NULL CHECK (actionable IN (0, 1)),
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL CHECK (length(payload_hash) = 64),
    UNIQUE (adapter, external_id, observed_at)
);

CREATE INDEX IF NOT EXISTS idx_workflow_snapshots_run
    ON workflow_snapshots (run_id, adapter, external_id);

CREATE INDEX IF NOT EXISTS idx_workflow_snapshots_history
    ON workflow_snapshots (adapter, external_id, observed_at DESC);

CREATE TABLE IF NOT EXISTS workflow_current (
    adapter TEXT NOT NULL,
    external_id TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_run_id TEXT NOT NULL REFERENCES sync_runs(run_id) ON DELETE RESTRICT,
    reference_no TEXT NOT NULL DEFAULT '',
    project_no TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    applicant TEXT NOT NULL DEFAULT '',
    submitted_at TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    current_node TEXT NOT NULL DEFAULT '',
    current_approver TEXT NOT NULL DEFAULT '',
    waiting_days INTEGER CHECK (waiting_days IS NULL OR waiting_days >= 0),
    source_url TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL CHECK (active IN (0, 1)),
    actionable INTEGER NOT NULL CHECK (actionable IN (0, 1)),
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL CHECK (length(payload_hash) = 64),
    PRIMARY KEY (adapter, external_id)
);

CREATE INDEX IF NOT EXISTS idx_workflow_current_actionable
    ON workflow_current (actionable, waiting_days DESC, adapter, external_id);

CREATE TABLE IF NOT EXISTS workflow_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES sync_runs(run_id) ON DELETE RESTRICT,
    adapter TEXT NOT NULL,
    external_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (
        event_type IN ('new', 'node_changed', 'completed', 'assignee_changed')
    ),
    observed_at TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    details_json TEXT NOT NULL DEFAULT '{}',
    old_payload_hash TEXT,
    new_payload_hash TEXT NOT NULL CHECK (length(new_payload_hash) = 64),
    UNIQUE (run_id, adapter, external_id, event_type)
);

CREATE INDEX IF NOT EXISTS idx_workflow_events_record
    ON workflow_events (adapter, external_id, observed_at DESC);

PRAGMA user_version = 1;
COMMIT;
