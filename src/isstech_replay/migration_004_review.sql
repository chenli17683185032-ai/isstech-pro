BEGIN IMMEDIATE;

CREATE TABLE workflow_drafts (
    draft_id TEXT PRIMARY KEY,
    extraction_id TEXT NOT NULL UNIQUE
        REFERENCES extraction_runs(extraction_id) ON DELETE RESTRICT,
    workflow TEXT NOT NULL,
    profile TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN (
            'extracted', 'needs_review', 'validated', 'ready', 'previewed',
            'submitted', 'reconciling', 'completed', 'failed'
        )
    ),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    validation_issues_json TEXT NOT NULL DEFAULT '[]',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    validated_at TEXT,
    ready_at TEXT
);

CREATE INDEX idx_workflow_drafts_state
    ON workflow_drafts (state, updated_at DESC);

CREATE TABLE draft_fields (
    draft_id TEXT NOT NULL
        REFERENCES workflow_drafts(draft_id) ON DELETE RESTRICT,
    field_name TEXT NOT NULL,
    label TEXT NOT NULL,
    required INTEGER NOT NULL CHECK (required IN (0, 1)),
    source_field_id INTEGER UNIQUE
        REFERENCES extracted_fields(field_id) ON DELETE RESTRICT,
    review_decision TEXT NOT NULL CHECK (
        review_decision IN ('not_proposed', 'pending', 'confirmed', 'rejected')
    ),
    confirmed_value TEXT,
    human_source_kind TEXT,
    human_source_index INTEGER,
    human_source_label TEXT,
    human_source_text TEXT,
    reviewed_by TEXT,
    reviewed_at TEXT,
    PRIMARY KEY (draft_id, field_name),
    CHECK (
        (human_source_kind IS NULL AND human_source_index IS NULL
            AND human_source_label IS NULL AND human_source_text IS NULL)
        OR
        (human_source_kind IN ('page', 'document', 'sheet', 'slide')
            AND human_source_index >= 1
            AND human_source_label IS NOT NULL AND human_source_text IS NOT NULL)
    )
);

CREATE INDEX idx_draft_fields_review
    ON draft_fields (draft_id, review_decision, required);

CREATE TABLE draft_audit_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id TEXT NOT NULL
        REFERENCES workflow_drafts(draft_id) ON DELETE RESTRICT,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    field_name TEXT,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE (draft_id, sequence)
);

CREATE INDEX idx_draft_audit_events_draft
    ON draft_audit_events (draft_id, sequence);

CREATE TRIGGER draft_audit_events_no_update
BEFORE UPDATE ON draft_audit_events
BEGIN
    SELECT RAISE(ABORT, 'draft audit events are append-only');
END;

CREATE TRIGGER draft_audit_events_no_delete
BEFORE DELETE ON draft_audit_events
BEGIN
    SELECT RAISE(ABORT, 'draft audit events are append-only');
END;

PRAGMA user_version = 4;
COMMIT;
