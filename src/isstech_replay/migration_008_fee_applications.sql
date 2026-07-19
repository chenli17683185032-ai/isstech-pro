BEGIN IMMEDIATE;

CREATE TABLE readonly_module_runs_v8 (
    run_id TEXT PRIMARY KEY,
    module TEXT NOT NULL CHECK (module IN (
        'payment', 'bizcase', 'travel_application', 'daily_expense',
        'travel_reimbursement', 'travel_subsidy'
    )),
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    started_at TEXT NOT NULL,
    observed_at TEXT,
    finished_at TEXT,
    max_pages INTEGER NOT NULL CHECK (max_pages > 0),
    source_total_count INTEGER CHECK (source_total_count IS NULL OR source_total_count >= 0),
    observed_count INTEGER NOT NULL DEFAULT 0 CHECK (observed_count >= 0),
    snapshot_count INTEGER NOT NULL DEFAULT 0 CHECK (snapshot_count >= 0),
    history_rows_inserted INTEGER NOT NULL DEFAULT 0 CHECK (history_rows_inserted >= 0),
    changed_count INTEGER NOT NULL DEFAULT 0 CHECK (changed_count >= 0),
    error_type TEXT,
    error_message TEXT
);

INSERT INTO readonly_module_runs_v8
SELECT * FROM readonly_module_runs;

CREATE TABLE readonly_module_snapshots_v8 (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES readonly_module_runs_v8(run_id) ON DELETE RESTRICT,
    module TEXT NOT NULL CHECK (module IN (
        'payment', 'bizcase', 'travel_application', 'daily_expense',
        'travel_reimbursement', 'travel_subsidy'
    )),
    external_id TEXT NOT NULL CHECK (length(external_id) > 0),
    observed_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL CHECK (length(payload_hash) = 64),
    UNIQUE (module, external_id, observed_at)
);

INSERT INTO readonly_module_snapshots_v8
SELECT * FROM readonly_module_snapshots;

CREATE TABLE readonly_module_current_v8 (
    module TEXT NOT NULL CHECK (module IN (
        'payment', 'bizcase', 'travel_application', 'daily_expense',
        'travel_reimbursement', 'travel_subsidy'
    )),
    external_id TEXT NOT NULL CHECK (length(external_id) > 0),
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_run_id TEXT NOT NULL REFERENCES readonly_module_runs_v8(run_id) ON DELETE RESTRICT,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL CHECK (length(payload_hash) = 64),
    PRIMARY KEY (module, external_id)
);

INSERT INTO readonly_module_current_v8
SELECT * FROM readonly_module_current;

CREATE TABLE readonly_scope_assertions_v8 (
    module TEXT NOT NULL CHECK (module IN (
        'payment', 'bizcase', 'travel_application', 'daily_expense',
        'travel_reimbursement', 'travel_subsidy'
    )),
    external_id TEXT NOT NULL CHECK (length(external_id) > 0),
    scope_reason TEXT NOT NULL CHECK (
        scope_reason IN ('my_project', 'submitted_by_me', 'managed_by_me')
    ),
    evidence_kind TEXT NOT NULL CHECK (evidence_kind = 'account_holder_confirmation'),
    confirmed_at TEXT NOT NULL,
    PRIMARY KEY (module, external_id, scope_reason)
);

INSERT INTO readonly_scope_assertions_v8
SELECT * FROM readonly_scope_assertions;

DROP TABLE readonly_module_current;
DROP TABLE readonly_module_snapshots;
DROP TABLE readonly_module_runs;
DROP TABLE readonly_scope_assertions;

ALTER TABLE readonly_module_runs_v8 RENAME TO readonly_module_runs;
ALTER TABLE readonly_module_snapshots_v8 RENAME TO readonly_module_snapshots;
ALTER TABLE readonly_module_current_v8 RENAME TO readonly_module_current;
ALTER TABLE readonly_scope_assertions_v8 RENAME TO readonly_scope_assertions;

CREATE INDEX idx_readonly_module_runs_latest
    ON readonly_module_runs (module, started_at DESC, run_id DESC);

CREATE INDEX idx_readonly_module_snapshots_history
    ON readonly_module_snapshots (module, external_id, observed_at DESC);

CREATE INDEX idx_readonly_module_current_latest
    ON readonly_module_current (module, last_seen_at DESC, external_id);

CREATE INDEX idx_readonly_scope_assertions_module
    ON readonly_scope_assertions (module, external_id, scope_reason);

PRAGMA user_version = 8;
COMMIT;
