BEGIN IMMEDIATE;

CREATE TABLE material_blobs (
    sha256 TEXT PRIMARY KEY CHECK (length(sha256) = 64),
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    original_path TEXT NOT NULL UNIQUE,
    detected_mime_type TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE materials (
    material_id TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL REFERENCES material_blobs(sha256) ON DELETE RESTRICT,
    original_name TEXT NOT NULL CHECK (length(original_name) > 0),
    declared_mime_type TEXT NOT NULL DEFAULT '',
    detected_mime_type TEXT NOT NULL,
    extension TEXT NOT NULL DEFAULT '',
    ingest_status TEXT NOT NULL CHECK (ingest_status IN ('ready', 'needs_review')),
    review_reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE (sha256, original_name)
);

CREATE INDEX idx_materials_created
    ON materials (created_at DESC, material_id DESC);

CREATE INDEX idx_materials_status
    ON materials (ingest_status, created_at DESC);

CREATE TABLE material_artifacts (
    artifact_id INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id TEXT NOT NULL REFERENCES materials(material_id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    parser_version TEXT NOT NULL DEFAULT '',
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    size_bytes INTEGER NOT NULL CHECK (size_bytes >= 0),
    created_at TEXT NOT NULL,
    UNIQUE (material_id, kind, path)
);

PRAGMA user_version = 2;
COMMIT;
