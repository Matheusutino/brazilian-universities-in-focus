"""Versioned PostgreSQL schema for durable incremental operation.

Each migration runs once, in order, inside a transaction. Never edit an applied
migration; append a new one instead.
"""

BASE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sources (
    site text PRIMARY KEY,
    base_url text NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    last_successful_discovery_at timestamptz,
    consecutive_empty_pages integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id uuid PRIMARY KEY,
    kind text NOT NULL,
    status text NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    stats jsonb NOT NULL DEFAULT '{}'::jsonb,
    error text
);

CREATE TABLE IF NOT EXISTS discovered_urls (
    id uuid PRIMARY KEY,
    site text NOT NULL REFERENCES sources(site),
    url text NOT NULL,
    canonical_url text NOT NULL UNIQUE,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    discovery_run_id uuid REFERENCES pipeline_runs(id),
    discovery_page integer
);

CREATE TABLE IF NOT EXISTS raw_documents (
    article_id uuid PRIMARY KEY REFERENCES discovered_urls(id) ON DELETE CASCADE,
    path text NOT NULL,
    sha256 text NOT NULL,
    status_code integer,
    fetched_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS articles (
    id uuid PRIMARY KEY REFERENCES discovered_urls(id) ON DELETE CASCADE,
    site text NOT NULL REFERENCES sources(site),
    url text NOT NULL UNIQUE,
    status text NOT NULL CHECK (status IN ('extracted', 'accepted', 'rejected')),
    payload jsonb NOT NULL,
    content_hash text,
    rejection_reason text,
    extracted_at timestamptz,
    processed_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS processing_jobs (
    id bigserial PRIMARY KEY,
    article_id uuid NOT NULL REFERENCES discovered_urls(id) ON DELETE CASCADE,
    site text NOT NULL REFERENCES sources(site),
    stage text NOT NULL CHECK (stage IN ('fetch','extract','preprocess','enrich','embed')),
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','running','completed','failed')),
    priority integer NOT NULL DEFAULT 100,
    attempts integer NOT NULL DEFAULT 0,
    available_at timestamptz NOT NULL DEFAULT now(),
    lease_expires_at timestamptz,
    worker_id text,
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    UNIQUE (article_id, stage)
);

CREATE INDEX IF NOT EXISTS processing_jobs_claim_idx
ON processing_jobs (status, stage, priority DESC, available_at, id);

CREATE TABLE IF NOT EXISTS enrichments (
    article_id uuid PRIMARY KEY REFERENCES articles(id) ON DELETE CASCADE,
    provider text NOT NULL,
    model text NOT NULL,
    schema_version text NOT NULL,
    payload jsonb NOT NULL,
    metrics jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS embedding_state (
    article_id uuid PRIMARY KEY REFERENCES articles(id) ON DELETE CASCADE,
    content_hash text NOT NULL,
    model text NOT NULL,
    indexed_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pipeline_errors (
    id bigserial PRIMARY KEY,
    run_id uuid REFERENCES pipeline_runs(id),
    job_id bigint REFERENCES processing_jobs(id),
    article_id uuid REFERENCES discovered_urls(id),
    site text,
    stage text,
    transient boolean NOT NULL DEFAULT false,
    error_type text NOT NULL,
    message text NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now()
);
"""

# Annotations become versioned by enrichment profile (provider, model, schema,
# prompt, and generation settings), so several profiles can coexist per
# article. Runs keep a configuration snapshot, and errors keep the metrics of
# the requests they consumed.
ENRICHMENT_PROFILES_SQL = """
CREATE TABLE enrichment_profiles (
    id text PRIMARY KEY,
    provider text NOT NULL,
    model text NOT NULL,
    schema_version text NOT NULL,
    prompt_sha256 text NOT NULL,
    settings jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO enrichment_profiles (id, provider, model, schema_version, prompt_sha256, settings)
SELECT DISTINCT 'legacy-' || md5(provider || '|' || model || '|' || schema_version),
       provider, model, schema_version, 'unknown', '{}'::jsonb
FROM enrichments;

ALTER TABLE enrichments
    ADD COLUMN profile_id text REFERENCES enrichment_profiles(id),
    ADD COLUMN content_hash text,
    ADD COLUMN run_id uuid REFERENCES pipeline_runs(id);
UPDATE enrichments
SET profile_id = 'legacy-' || md5(provider || '|' || model || '|' || schema_version);
ALTER TABLE enrichments
    ALTER COLUMN profile_id SET NOT NULL,
    DROP CONSTRAINT enrichments_pkey,
    ADD PRIMARY KEY (article_id, profile_id),
    DROP COLUMN provider,
    DROP COLUMN model,
    DROP COLUMN schema_version;
CREATE INDEX enrichments_profile_created_idx ON enrichments (profile_id, created_at);

ALTER TABLE pipeline_runs
    ADD COLUMN config jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN worker_id text,
    DROP CONSTRAINT pipeline_runs_status_check,
    ADD CONSTRAINT pipeline_runs_status_check
        CHECK (status IN ('running', 'completed', 'failed', 'interrupted'));

ALTER TABLE pipeline_errors ADD COLUMN details jsonb NOT NULL DEFAULT '{}'::jsonb;
CREATE INDEX pipeline_errors_stage_time_idx ON pipeline_errors (stage, occurred_at);
"""

# Profiles keep the exact prompts and contract, not only their hash, so that
# every annotation can be reproduced from the database alone.
PROFILE_PROMPTS_SQL = """
ALTER TABLE enrichment_profiles
    ADD COLUMN prompts jsonb,
    ADD COLUMN contract jsonb;
"""

MIGRATIONS: tuple[tuple[int, str, str], ...] = (
    (1, "base schema", BASE_SCHEMA_SQL),
    (2, "enrichment profiles and run transparency", ENRICHMENT_PROFILES_SQL),
    (3, "profile prompts and contract", PROFILE_PROMPTS_SQL),
)
