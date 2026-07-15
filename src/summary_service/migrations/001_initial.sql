CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash TEXT NOT NULL UNIQUE,
    client_id TEXT NOT NULL,
    idempotency_key TEXT,
    status TEXT NOT NULL CHECK (status IN ('queued','running','succeeded','failed','expired')),
    input_text TEXT,
    text_bytes INTEGER NOT NULL,
    summary TEXT,
    error_code TEXT,
    error_message TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    prompt_version TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL,
    started_at_ms INTEGER,
    completed_at_ms INTEGER,
    expires_at_ms INTEGER,
    delete_at_ms INTEGER,
    lease_owner TEXT,
    lease_expires_at_ms INTEGER,
    heartbeat_at_ms INTEGER,
    UNIQUE (client_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_jobs_claim ON jobs(status, created_at_ms, id);
CREATE INDEX IF NOT EXISTS idx_jobs_lease ON jobs(status, lease_expires_at_ms);
CREATE INDEX IF NOT EXISTS idx_jobs_expiry ON jobs(status, expires_at_ms, delete_at_ms);
PRAGMA user_version = 1;
