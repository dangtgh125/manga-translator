CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    target_lang TEXT NOT NULL DEFAULT 'vi',
    reading_direction TEXT NOT NULL DEFAULT 'rtl',
    glossary_json TEXT NOT NULL DEFAULT '[]',
    settings_json TEXT NOT NULL DEFAULT '{}',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pages (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    display_name TEXT NOT NULL,
    original_path TEXT NOT NULL,
    work_path TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    format TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'uploaded',
    steps_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    source_lang_override TEXT,
    detected_lang TEXT,
    lang_uncertain INTEGER NOT NULL DEFAULT 0,
    render_path TEXT,
    render_report_json TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pages_project ON pages(project_id, position);

CREATE TABLE IF NOT EXISTS regions (
    id TEXT PRIMARY KEY,
    page_id TEXT NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL,
    polygon_json TEXT NOT NULL,
    direction TEXT NOT NULL DEFAULT 'unknown',
    reading_order INTEGER NOT NULL DEFAULT 0,
    source_lang TEXT NOT NULL DEFAULT 'unknown',
    lang_confidence REAL NOT NULL DEFAULT 0,
    lang_uncertain INTEGER NOT NULL DEFAULT 1,
    ocr_text TEXT NOT NULL DEFAULT '',
    ocr_confidence REAL NOT NULL DEFAULT 0,
    ocr_engine TEXT NOT NULL DEFAULT '',
    source_text TEXT NOT NULL DEFAULT '',
    source_edited INTEGER NOT NULL DEFAULT 0,
    translated_text TEXT NOT NULL DEFAULT '',
    final_text TEXT NOT NULL DEFAULT '',
    final_edited INTEGER NOT NULL DEFAULT 0,
    translation_provider TEXT NOT NULL DEFAULT '',
    translation_stale INTEGER NOT NULL DEFAULT 0,
    translation_flags_json TEXT NOT NULL DEFAULT '[]',
    review_status TEXT NOT NULL DEFAULT 'pending',
    mask_mode TEXT NOT NULL DEFAULT 'auto',
    mask_box_json TEXT,
    text_box_json TEXT,
    font_size REAL,
    lines_json TEXT NOT NULL DEFAULT '[]',
    origin TEXT NOT NULL DEFAULT 'auto',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_regions_page ON regions(page_id, reading_order);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    scope TEXT NOT NULL,
    page_id TEXT,
    step TEXT NOT NULL,
    options_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'queued',
    progress_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    idempotency_key TEXT UNIQUE,
    attempts INTEGER NOT NULL DEFAULT 0,
    locked_by TEXT,
    locked_at TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, created_at);

CREATE TABLE IF NOT EXISTS provider_profiles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    provider TEXT NOT NULL,
    model_id TEXT NOT NULL DEFAULT '',
    endpoint TEXT,
    options_json TEXT NOT NULL DEFAULT '{}',
    secret_enc BLOB,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
