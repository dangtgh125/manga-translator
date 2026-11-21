-- Idempotency keys scoped per project (PLAN.md §10 P2.3).
-- 0001 declared `idempotency_key TEXT UNIQUE` (a global, implicit autoindex that cannot be dropped
-- on its own), so the table is rebuilt without it. Existing rows are carried over unchanged;
-- request_fingerprint stays NULL for legacy jobs, which means a replay of an old key is treated
-- as a conflict rather than silently returning an unrelated job.
CREATE TABLE jobs_new (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    scope TEXT NOT NULL,
    page_id TEXT,
    step TEXT NOT NULL,
    options_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'queued',
    progress_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    idempotency_key TEXT,
    request_fingerprint TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    locked_by TEXT,
    locked_at TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
INSERT INTO jobs_new (id, project_id, scope, page_id, step, options_json, status, progress_json, error,
                      idempotency_key, attempts, locked_by, locked_at, cancel_requested,
                      created_at, updated_at, started_at, finished_at)
    SELECT id, project_id, scope, page_id, step, options_json, status, progress_json, error,
           idempotency_key, attempts, locked_by, locked_at, cancel_requested,
           created_at, updated_at, started_at, finished_at
    FROM jobs;
DROP TABLE jobs;
ALTER TABLE jobs_new RENAME TO jobs;
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_project_idempotency
    ON jobs(project_id, idempotency_key) WHERE idempotency_key IS NOT NULL;
