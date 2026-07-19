BEGIN IMMEDIATE;

CREATE TABLE assistant_preferences (
    preference_id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL CHECK (length(created_at) > 0),
    text TEXT NOT NULL CHECK (length(text) <= 500),
    active INTEGER NOT NULL CHECK (active IN (0, 1))
);

CREATE INDEX idx_assistant_preferences_latest
    ON assistant_preferences (preference_id DESC);

CREATE TABLE assistant_briefs (
    brief_id TEXT PRIMARY KEY CHECK (length(brief_id) > 0),
    business_date TEXT NOT NULL CHECK (length(business_date) = 10),
    snapshot_hash TEXT NOT NULL CHECK (length(snapshot_hash) = 64),
    preference_version INTEGER NOT NULL CHECK (preference_version >= 0),
    generated_at TEXT NOT NULL CHECK (length(generated_at) > 0),
    source TEXT NOT NULL CHECK (source IN ('model', 'fallback')),
    provider TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    provider_configured INTEGER NOT NULL CHECK (provider_configured IN (0, 1)),
    fallback_code TEXT,
    summary TEXT NOT NULL CHECK (length(summary) BETWEEN 1 AND 500),
    candidate_count INTEGER NOT NULL CHECK (candidate_count >= 0),
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL CHECK (length(payload_hash) = 64),
    UNIQUE (business_date, snapshot_hash, preference_version)
);

CREATE INDEX idx_assistant_briefs_latest
    ON assistant_briefs (generated_at DESC, brief_id DESC);

PRAGMA user_version = 9;
COMMIT;
