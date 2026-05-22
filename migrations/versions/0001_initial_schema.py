"""Initial PostgreSQL schema matching SQLite Phase 0 + Phase 1 baseline.

Revision ID: 0001
Revises:
Create Date: 2026-05-21
"""

from __future__ import annotations

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    # ── jobs ──────────────────────────────────────────────────────────────
    op.execute("""
    CREATE TABLE jobs (
        id SERIAL PRIMARY KEY,
        platform TEXT NOT NULL,
        canonical_id TEXT NOT NULL,
        url TEXT NOT NULL,
        title TEXT NOT NULL,
        company TEXT NOT NULL,
        location TEXT NOT NULL,
        jd_text TEXT,
        jd_quality TEXT CHECK (
            jd_quality IS NULL
            OR jd_quality IN (
                'full', 'good', 'partial', 'stub', 'missing', 'abandoned'
            )
        ),
        posted_at TIMESTAMPTZ,
        discovered_at TIMESTAMPTZ NOT NULL,
        enriched_at TIMESTAMPTZ,
        enrich_source TEXT,
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
        ml_gate_at TIMESTAMPTZ,
        ml_gate_version TEXT,
        is_swe_role INTEGER,
        UNIQUE(platform, canonical_id)
    )
    """)

    # ── evaluations ──────────────────────────────────────────────────────
    op.execute("""
    CREATE TABLE evaluations (
        id SERIAL PRIMARY KEY,
        job_id INTEGER NOT NULL UNIQUE REFERENCES jobs(id),
        stage_a_score INTEGER CHECK (
            stage_a_score IS NULL OR stage_a_score BETWEEN 0 AND 100
        ),
        stage_a_one_line TEXT,
        stage_a_timing_eligible TEXT,
        stage_a_status TEXT CHECK (
            stage_a_status IS NULL
            OR stage_a_status IN ('completed', 'error')
        ),
        stage_a_error TEXT,
        stage_a_model TEXT,
        stage_a_cost_usd REAL,
        stage_a_prompt_hash TEXT,
        stage_a_resume_hash TEXT,
        stage_b_verdict TEXT CHECK (
            stage_b_verdict IS NULL
            OR stage_b_verdict IN ('apply', 'consider', 'skip')
        ),
        stage_b_jd_summary TEXT,
        stage_b_verdict_json JSONB,
        stage_b_summary_json JSONB,
        stage_b_fit_json JSONB,
        stage_b_hooks_json JSONB,
        stage_b_status TEXT CHECK (
            stage_b_status IS NULL
            OR stage_b_status IN ('completed', 'error', 'skipped_below_threshold')
        ),
        stage_b_error TEXT,
        stage_b_model TEXT,
        stage_b_cost_usd REAL,
        stage_b_prompt_hash TEXT,
        stage_b_resume_hash TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        stage_a_at TIMESTAMPTZ,
        stage_b_at TIMESTAMPTZ,
        stage_a_error_count INTEGER NOT NULL DEFAULT 0,
        stage_b_error_count INTEGER NOT NULL DEFAULT 0
    )
    """)

    # ── pipeline_runs ────────────────────────────────────────────────────
    op.execute("""
    CREATE TABLE pipeline_runs (
        id SERIAL PRIMARY KEY,
        run_id TEXT NOT NULL UNIQUE,
        started_at TIMESTAMPTZ NOT NULL,
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
        finished_at TIMESTAMPTZ
    )
    """)

    # ── resume_variants (before job_status which references it) ─────────
    op.execute("""
    CREATE TABLE resume_variants (
        name TEXT PRIMARY KEY,
        description TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """)

    # ── job_status ───────────────────────────────────────────────────────
    op.execute("""
    CREATE TABLE job_status (
        job_id INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
        status TEXT NOT NULL DEFAULT 'new'
            CHECK (status IN (
                'new', 'scored', 'shortlisted', 'archived', 'ignored',
                'applied', 'interviewing', 'rejected', 'offer', 'ghosted',
                'oa', 'hr_call', 'second_round', 'final_round'
            )),
        next_followup_at TIMESTAMPTZ,
        resume_variant TEXT REFERENCES resume_variants(name),
        notes TEXT,
        last_status_change_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """)

    # ── job_status_history ───────────────────────────────────────────────
    op.execute("""
    CREATE TABLE job_status_history (
        id SERIAL PRIMARY KEY,
        job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
        from_status TEXT,
        to_status TEXT NOT NULL,
        changed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        reason TEXT,
        resume_variant_at_change TEXT
    )
    """)

    # ── applied ──────────────────────────────────────────────────────────
    op.execute("""
    CREATE TABLE applied (
        job_id INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
        applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        notes TEXT,
        master_resume_hash TEXT,
        tailored_resume_hash TEXT,
        cover_letter TEXT,
        application_method TEXT,
        verdict_snapshot TEXT,
        fit_snapshot TEXT,
        hooks_snapshot TEXT
    )
    """)

    # ── resume_snapshots ─────────────────────────────────────────────────
    op.execute("""
    CREATE TABLE resume_snapshots (
        resume_hash TEXT PRIMARY KEY,
        captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        source TEXT NOT NULL,
        content TEXT NOT NULL,
        notes TEXT
    )
    """)

    # ── companies ────────────────────────────────────────────────────────
    op.execute("""
    CREATE TABLE companies (
        slug TEXT PRIMARY KEY,
        ats_vendor TEXT,
        ats_override INTEGER NOT NULL DEFAULT 0,
        last_verified_at TIMESTAMPTZ,
        last_probe_attempt_at TIMESTAMPTZ,
        job_count_last_scan INTEGER NOT NULL DEFAULT 0,
        notes TEXT,
        consecutive_discover_failures INTEGER NOT NULL DEFAULT 0
    )
    """)

    # ── cost_ledger ──────────────────────────────────────────────────────
    op.execute("""
    CREATE TABLE cost_ledger (
        day TEXT PRIMARY KEY,
        spent_usd REAL NOT NULL DEFAULT 0.0,
        calls INTEGER NOT NULL DEFAULT 0,
        last_updated TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """)

    # ── state ────────────────────────────────────────────────────────────
    op.execute("""
    CREATE TABLE state (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """)

    # ── Trigger function: auto-seed job_status on job insert ─────────────
    op.execute("""
    CREATE FUNCTION trg_jobs_seed_status_fn()
    RETURNS TRIGGER AS $$
    BEGIN
        INSERT INTO job_status (job_id, status)
            VALUES (NEW.id, 'new')
            ON CONFLICT (job_id) DO NOTHING;
        INSERT INTO job_status_history (job_id, from_status, to_status)
            VALUES (NEW.id, NULL, 'new');
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """)

    op.execute("""
    CREATE TRIGGER trg_jobs_seed_status
    AFTER INSERT ON jobs
    FOR EACH ROW
    EXECUTE FUNCTION trg_jobs_seed_status_fn()
    """)

    # ── Indexes ──────────────────────────────────────────────────────────
    op.execute("CREATE INDEX idx_jobs_dedup_softkey ON jobs(company_norm, title_norm)")
    op.execute("CREATE INDEX idx_jobs_discovered_at ON jobs(discovered_at DESC)")
    op.execute(
        "CREATE INDEX idx_companies_vendor "
        "ON companies(ats_vendor) WHERE ats_vendor IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX idx_eval_stage_a_score "
        "ON evaluations(stage_a_score DESC) "
        "WHERE stage_a_status = 'completed'"
    )
    op.execute(
        "CREATE INDEX idx_eval_stage_b_queue "
        "ON evaluations(job_id) "
        "WHERE stage_a_status = 'completed' AND stage_b_status IS NULL"
    )
    op.execute("CREATE INDEX idx_job_status_status ON job_status(status)")
    op.execute(
        "CREATE INDEX idx_job_status_followup "
        "ON job_status(next_followup_at) "
        "WHERE next_followup_at IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX idx_job_status_stale "
        "ON job_status(last_status_change_at) "
        "WHERE status IN ("
        "'applied', 'interviewing', 'oa', "
        "'hr_call', 'second_round', 'final_round')"
    )
    op.execute(
        "CREATE INDEX idx_job_status_history_job "
        "ON job_status_history(job_id, changed_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_jobs_seed_status ON jobs")
    op.execute("DROP FUNCTION IF EXISTS trg_jobs_seed_status_fn()")
    op.execute("DROP INDEX IF EXISTS idx_job_status_history_job")
    op.execute("DROP INDEX IF EXISTS idx_job_status_stale")
    op.execute("DROP INDEX IF EXISTS idx_job_status_followup")
    op.execute("DROP INDEX IF EXISTS idx_job_status_status")
    op.execute("DROP INDEX IF EXISTS idx_eval_stage_b_queue")
    op.execute("DROP INDEX IF EXISTS idx_eval_stage_a_score")
    op.execute("DROP INDEX IF EXISTS idx_companies_vendor")
    op.execute("DROP INDEX IF EXISTS idx_jobs_discovered_at")
    op.execute("DROP INDEX IF EXISTS idx_jobs_dedup_softkey")
    op.execute("DROP TABLE IF EXISTS state")
    op.execute("DROP TABLE IF EXISTS cost_ledger")
    op.execute("DROP TABLE IF EXISTS companies")
    op.execute("DROP TABLE IF EXISTS resume_snapshots")
    op.execute("DROP TABLE IF EXISTS applied")
    op.execute("DROP TABLE IF EXISTS job_status_history")
    op.execute("DROP TABLE IF EXISTS job_status")
    op.execute("DROP TABLE IF EXISTS resume_variants")
    op.execute("DROP TABLE IF EXISTS pipeline_runs")
    op.execute("DROP TABLE IF EXISTS evaluations")
    op.execute("DROP TABLE IF EXISTS jobs")
