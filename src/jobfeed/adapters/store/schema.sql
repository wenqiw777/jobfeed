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
    block_a_json TEXT,
    block_b_json TEXT,
    block_c_json TEXT,
    block_e_json TEXT,
    stage_b_status TEXT CHECK (
        stage_b_status IS NULL OR stage_b_status IN ('completed', 'error')
    ),
    stage_b_error TEXT,
    stage_b_model TEXT,
    stage_b_cost_usd REAL,
    stage_b_prompt_hash TEXT,
    stage_b_resume_hash TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
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
