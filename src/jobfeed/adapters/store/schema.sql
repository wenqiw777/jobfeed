-- Jobfeed SQLite schema (Phase 0 baseline + Phase 1 hardening)

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL,
    canonical_id TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT NOT NULL,
    jd_text TEXT,
    jd_quality TEXT CHECK (
        jd_quality IS NULL
        OR jd_quality IN ('full', 'good', 'partial', 'stub', 'missing', 'abandoned')
    ),
    posted_at TEXT,
    discovered_at TEXT NOT NULL,
    enriched_at TEXT,
    enrich_source TEXT,
    -- Phase 1 columns
    company_norm TEXT,
    title_norm TEXT,
    location_norm TEXT,
    jd_lang TEXT,
    enrich_error TEXT,
    quality_rubric_version INTEGER,
    reapply_notice TEXT,
    hard_filter TEXT,
    seniority_level TEXT,
    degree_required TEXT,
    clearance_required INTEGER,
    school_restricted INTEGER,
    domain_tags TEXT,
    tech_required TEXT,
    role_type TEXT,
    yoe_min INTEGER,
    ml_gate_score REAL,
    ml_gate_result TEXT,
    ml_gate_fail_reason TEXT,
    ml_gate_at TEXT,
    ml_gate_version TEXT,
    is_swe_role INTEGER,
    UNIQUE(platform, canonical_id)
);

CREATE TABLE IF NOT EXISTS evaluations (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL UNIQUE REFERENCES jobs(id),
    stage_a_score INTEGER CHECK (
        stage_a_score IS NULL OR stage_a_score BETWEEN 0 AND 100
    ),
    stage_a_one_line TEXT,
    stage_a_timing_eligible TEXT,
    stage_a_status TEXT CHECK (
        stage_a_status IS NULL OR stage_a_status IN ('completed', 'error')
    ),
    stage_a_error TEXT,
    stage_a_model TEXT,
    stage_a_cost_usd REAL,
    stage_a_prompt_hash TEXT,
    stage_a_resume_hash TEXT,
    stage_b_verdict TEXT CHECK (
        stage_b_verdict IS NULL OR stage_b_verdict IN ('apply', 'consider', 'skip')
    ),
    stage_b_jd_summary TEXT,
    stage_b_verdict_json TEXT,
    stage_b_summary_json TEXT,
    stage_b_fit_json TEXT,
    stage_b_hooks_json TEXT,
    stage_b_status TEXT CHECK (
        stage_b_status IS NULL
        OR stage_b_status IN ('completed', 'error', 'skipped_below_threshold')
    ),
    stage_b_error TEXT,
    stage_b_model TEXT,
    stage_b_cost_usd REAL,
    stage_b_prompt_hash TEXT,
    stage_b_resume_hash TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    -- Phase 1 columns
    stage_a_at TEXT,
    stage_b_at TEXT,
    stage_a_error_count INTEGER NOT NULL DEFAULT 0,
    stage_b_error_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    started_at TEXT NOT NULL,
    source TEXT NOT NULL,
    jobs_discovered INTEGER DEFAULT 0,
    jobs_inserted INTEGER DEFAULT 0,
    jobs_updated INTEGER DEFAULT 0,
    jobs_filtered INTEGER DEFAULT 0,
    jobs_ml_gated INTEGER DEFAULT 0,
    stage_a_scored INTEGER DEFAULT 0,
    stage_b_scored INTEGER DEFAULT 0,
    jobs_scored INTEGER DEFAULT 0,
    total_llm_cost_usd REAL DEFAULT 0.0,
    errors INTEGER DEFAULT 0,
    finished_at TEXT
);

-- Phase 1 tables

CREATE TABLE IF NOT EXISTS job_status (
    job_id INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'new'
        CHECK (status IN (
            'new','scored','shortlisted','archived','ignored',
            'applied','interviewing','rejected','offer','ghosted',
            'oa','hr_call','second_round','final_round'
        )),
    next_followup_at TEXT,
    resume_variant TEXT REFERENCES resume_variants(name),
    notes TEXT,
    last_status_change_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS job_status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    from_status TEXT,
    to_status TEXT NOT NULL,
    changed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    reason TEXT,
    resume_variant_at_change TEXT
);

CREATE TABLE IF NOT EXISTS resume_variants (
    name TEXT PRIMARY KEY,
    description TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS applied (
    job_id INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    notes TEXT,
    master_resume_hash TEXT,
    tailored_resume_hash TEXT,
    cover_letter TEXT,
    application_method TEXT,
    verdict_snapshot TEXT,
    fit_snapshot TEXT,
    hooks_snapshot TEXT
);

CREATE TABLE IF NOT EXISTS resume_snapshots (
    resume_hash TEXT PRIMARY KEY,
    captured_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    source TEXT NOT NULL,
    content TEXT NOT NULL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS companies (
    slug TEXT PRIMARY KEY,
    ats_vendor TEXT,
    ats_override INTEGER NOT NULL DEFAULT 0,
    last_verified_at TEXT,
    last_probe_attempt_at TEXT,
    job_count_last_scan INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    consecutive_discover_failures INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS cost_ledger (
    day TEXT PRIMARY KEY,
    spent_usd REAL NOT NULL DEFAULT 0.0,
    calls INTEGER NOT NULL DEFAULT 0,
    last_updated TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Trigger: auto-seed status on job insert
CREATE TRIGGER IF NOT EXISTS trg_jobs_seed_status
AFTER INSERT ON jobs
FOR EACH ROW
BEGIN
    INSERT OR IGNORE INTO job_status (job_id, status) VALUES (NEW.id, 'new');
    INSERT INTO job_status_history (job_id, from_status, to_status)
        VALUES (NEW.id, NULL, 'new');
END;

-- Indexes
CREATE INDEX IF NOT EXISTS idx_jobs_dedup_softkey
    ON jobs(company_norm, title_norm);
CREATE INDEX IF NOT EXISTS idx_jobs_discovered_at
    ON jobs(discovered_at DESC);
CREATE INDEX IF NOT EXISTS idx_companies_vendor
    ON companies(ats_vendor) WHERE ats_vendor IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_eval_stage_a_score
    ON evaluations(stage_a_score DESC) WHERE stage_a_status = 'completed';
CREATE INDEX IF NOT EXISTS idx_eval_stage_b_queue
    ON evaluations(job_id) WHERE stage_a_status = 'completed' AND stage_b_status IS NULL;
CREATE INDEX IF NOT EXISTS idx_job_status_status
    ON job_status(status);
CREATE INDEX IF NOT EXISTS idx_job_status_followup
    ON job_status(next_followup_at) WHERE next_followup_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_job_status_stale
    ON job_status(last_status_change_at)
    WHERE status IN ('applied', 'interviewing', 'oa', 'hr_call', 'second_round', 'final_round');
CREATE INDEX IF NOT EXISTS idx_job_status_history_job
    ON job_status_history(job_id, changed_at DESC);
