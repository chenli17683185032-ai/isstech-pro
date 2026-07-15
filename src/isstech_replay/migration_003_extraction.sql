BEGIN IMMEDIATE;

CREATE TABLE extraction_runs (
    extraction_id TEXT PRIMARY KEY,
    material_id TEXT NOT NULL REFERENCES materials(material_id) ON DELETE RESTRICT,
    profile TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('running', 'succeeded', 'needs_review', 'failed')
    ),
    confidence_threshold REAL NOT NULL CHECK (
        confidence_threshold >= 0 AND confidence_threshold <= 1
    ),
    can_advance INTEGER NOT NULL DEFAULT 0 CHECK (can_advance IN (0, 1)),
    document_path TEXT NOT NULL DEFAULT '',
    result_path TEXT NOT NULL DEFAULT '',
    started_at TEXT NOT NULL,
    finished_at TEXT,
    field_count INTEGER NOT NULL DEFAULT 0 CHECK (field_count >= 0),
    issue_count INTEGER NOT NULL DEFAULT 0 CHECK (issue_count >= 0),
    issues_json TEXT NOT NULL DEFAULT '[]',
    error_type TEXT,
    error_message TEXT
);

CREATE INDEX idx_extraction_runs_material
    ON extraction_runs (material_id, started_at DESC);

CREATE TABLE extracted_fields (
    field_id INTEGER PRIMARY KEY AUTOINCREMENT,
    extraction_id TEXT NOT NULL REFERENCES extraction_runs(extraction_id) ON DELETE CASCADE,
    field_name TEXT NOT NULL,
    proposed_value TEXT NOT NULL DEFAULT '',
    confidence REAL NOT NULL,
    required INTEGER NOT NULL CHECK (required IN (0, 1)),
    source_material_id TEXT,
    source_kind TEXT,
    source_index INTEGER,
    source_label TEXT,
    source_text TEXT,
    evidence_valid INTEGER NOT NULL CHECK (evidence_valid IN (0, 1)),
    validation_issues_json TEXT NOT NULL DEFAULT '[]',
    review_status TEXT NOT NULL DEFAULT 'pending' CHECK (
        review_status IN ('pending', 'confirmed', 'rejected')
    ),
    confirmed_value TEXT,
    UNIQUE (extraction_id, field_name)
);

CREATE INDEX idx_extracted_fields_review
    ON extracted_fields (review_status, extraction_id, field_name);

PRAGMA user_version = 3;
COMMIT;
