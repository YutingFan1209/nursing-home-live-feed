-- ============================================================
-- Nursing Home Acquisition Alert System — Database Schema
-- Postgres 15+
-- ============================================================

-- Enable useful extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";  -- for fast fuzzy text search

-- ============================================================
-- SOURCES
-- Registered discovery sources (RSS, EDGAR, Google News)
-- ============================================================
CREATE TABLE sources (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name            TEXT NOT NULL,
    url             TEXT NOT NULL UNIQUE,
    source_type     TEXT NOT NULL CHECK (source_type IN ('rss', 'edgar', 'googlenews', 'manual')),
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    last_fetched_at TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- ARTICLES
-- Raw scraped articles before extraction
-- ============================================================
CREATE TABLE articles (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_id       UUID REFERENCES sources(id) ON DELETE SET NULL,
    url             TEXT NOT NULL UNIQUE,
    title           TEXT,
    published_at    TIMESTAMPTZ,
    scraped_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    raw_text        TEXT,
    s3_key          TEXT,          -- archive location if stored in S3/GCS
    extraction_done BOOLEAN NOT NULL DEFAULT FALSE,
    extraction_error TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_articles_source_id ON articles(source_id);
CREATE INDEX idx_articles_published_at ON articles(published_at DESC);
CREATE INDEX idx_articles_extraction_done ON articles(extraction_done);

-- ============================================================
-- DEALS
-- One article can contain multiple deals (e.g. Dealbook posts)
-- ============================================================
CREATE TABLE deals (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    article_id          UUID NOT NULL REFERENCES articles(id) ON DELETE CASCADE,

    -- Extracted deal fields
    acquiring_entity    TEXT,
    seller_entity       TEXT,
    operator_names      TEXT[],         -- array of operator/mgmt company names
    facility_names      TEXT[],         -- specific facility names if mentioned
    states              TEXT[],         -- 2-letter state codes
    facility_count      INTEGER,
    deal_value_m        NUMERIC(12,2),  -- in millions USD
    acquisition_date    DATE,
    financing_amount_m  NUMERIC(12,2),  -- bridge loan / financing if mentioned
    lender              TEXT,

    -- Pipeline tracking
    stage               TEXT NOT NULL DEFAULT 'detected'
                            CHECK (stage IN ('detected','pending_cms','confirmed','unresolved','verified','dismissed')),
    confidence          TEXT CHECK (confidence IN ('high','partial','low')),
    dedup_hash          TEXT UNIQUE,    -- prevents duplicate deals across sources
    recheck_after       DATE,           -- when to re-query CMS for pending deals
    recheck_count       INTEGER NOT NULL DEFAULT 0,

    -- Metadata
    extraction_model    TEXT,           -- claude model version used
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_deals_article_id ON deals(article_id);
CREATE INDEX idx_deals_stage ON deals(stage);
CREATE INDEX idx_deals_acquisition_date ON deals(acquisition_date DESC NULLS LAST);
CREATE INDEX idx_deals_states ON deals USING gin(states);
CREATE INDEX idx_deals_operator_names ON deals USING gin(operator_names);
CREATE INDEX idx_deals_recheck_after ON deals(recheck_after) WHERE stage = 'pending_cms';

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER deals_updated_at
    BEFORE UPDATE ON deals
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ============================================================
-- CMS_FACILITIES
-- Normalized CMS Care Compare provider records
-- Refreshed periodically from data.cms.gov
-- ============================================================
CREATE TABLE cms_facilities (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ccn                 TEXT NOT NULL UNIQUE,   -- CMS Certification Number
    provider_name       TEXT NOT NULL,
    provider_state      TEXT NOT NULL,
    provider_city       TEXT,
    provider_zip        TEXT,
    ownership_type      TEXT,
    provider_type       TEXT,
    bed_count           INTEGER,
    five_star_rating    INTEGER CHECK (five_star_rating BETWEEN 1 AND 5),
    staffing_rating     INTEGER CHECK (staffing_rating BETWEEN 1 AND 5),
    health_insp_rating  INTEGER CHECK (health_insp_rating BETWEEN 1 AND 5),
    sff_flag            BOOLEAN DEFAULT FALSE,  -- Special Focus Facility
    sff_candidate_flag  BOOLEAN DEFAULT FALSE,
    cms_refreshed_at    TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_cms_facilities_state ON cms_facilities(provider_state);
CREATE INDEX idx_cms_facilities_name_trgm ON cms_facilities USING gin(provider_name gin_trgm_ops);
CREATE INDEX idx_cms_facilities_sff ON cms_facilities(sff_flag) WHERE sff_flag = TRUE;

CREATE TRIGGER cms_facilities_updated_at
    BEFORE UPDATE ON cms_facilities
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ============================================================
-- CMS_OWNERSHIP_RECORDS
-- Raw ownership change records from CMS qhpq-qrm6 dataset
-- ============================================================
CREATE TABLE cms_ownership_records (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    ccn                 TEXT NOT NULL,
    provider_name       TEXT,
    owner_name          TEXT,
    owner_type          TEXT,
    owner_role          TEXT,
    ownership_percentage NUMERIC(5,2),
    provider_state      TEXT,
    ownership_start_date DATE,
    cms_refreshed_at    TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(ccn, owner_name, ownership_start_date)
);

CREATE INDEX idx_cms_ownership_ccn ON cms_ownership_records(ccn);
CREATE INDEX idx_cms_ownership_state ON cms_ownership_records(provider_state);
CREATE INDEX idx_cms_ownership_date ON cms_ownership_records(ownership_start_date DESC);
CREATE INDEX idx_cms_ownership_owner_trgm ON cms_ownership_records USING gin(owner_name gin_trgm_ops);

-- ============================================================
-- CMS_MATCHES
-- Links deals to matched CMS ownership records
-- One deal can match multiple facilities
-- ============================================================
CREATE TABLE cms_matches (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    deal_id             UUID NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
    ccn                 TEXT,
    provider_name       TEXT,
    owner_name          TEXT,
    owner_type          TEXT,
    provider_state      TEXT,
    ownership_start_date DATE,
    match_score         INTEGER CHECK (match_score BETWEEN 0 AND 100),
    match_method        TEXT,   -- 'owner_name_fuzzy', 'facility_name_fuzzy', 'state_date', etc.
    matched_on_field    TEXT,   -- which extracted field triggered the match
    verified            BOOLEAN DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_cms_matches_deal_id ON cms_matches(deal_id);
CREATE INDEX idx_cms_matches_ccn ON cms_matches(ccn);

-- ============================================================
-- ANNOTATIONS
-- Team notes on deals — shared across all researchers
-- ============================================================
CREATE TABLE annotations (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    deal_id         UUID NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
    note            TEXT NOT NULL,
    tag             TEXT,   -- 'regulatory', 'research', 'follow-up', 'flagged', etc.
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_annotations_deal_id ON annotations(deal_id);
CREATE INDEX idx_annotations_tag ON annotations(tag);

-- ============================================================
-- ALERT_LOG
-- Tracks which deals have been included in email digests
-- Prevents duplicate alerts
-- ============================================================
CREATE TABLE alert_log (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    deal_id     UUID NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
    alert_type  TEXT NOT NULL,  -- 'digest', 'immediate', 'recheck_confirmed'
    sent_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_alert_log_deal_id ON alert_log(deal_id);

-- ============================================================
-- VIEWS — useful for the dashboard and API
-- ============================================================

-- Full deal view with article + match counts
CREATE VIEW deal_summary AS
SELECT
    d.id,
    d.stage,
    d.confidence,
    d.acquiring_entity,
    d.seller_entity,
    d.operator_names,
    d.facility_names,
    d.states,
    d.facility_count,
    d.deal_value_m,
    d.acquisition_date,
    d.financing_amount_m,
    d.lender,
    d.recheck_after,
    d.created_at,
    a.url         AS article_url,
    a.title       AS article_title,
    a.published_at,
    s.name        AS source_name,
    s.source_type,
    COUNT(DISTINCT m.id)  AS cms_match_count,
    COUNT(DISTINCT an.id) AS annotation_count
FROM deals d
JOIN articles a ON a.id = d.article_id
LEFT JOIN sources s ON s.id = a.source_id
LEFT JOIN cms_matches m ON m.deal_id = d.id
LEFT JOIN annotations an ON an.deal_id = d.id
GROUP BY d.id, a.url, a.title, a.published_at, s.name, s.source_type;

-- Deals needing CMS re-check today
CREATE VIEW pending_recheck AS
SELECT d.*, a.url AS article_url, a.title AS article_title
FROM deals d
JOIN articles a ON a.id = d.article_id
WHERE d.stage = 'pending_cms'
  AND (d.recheck_after IS NULL OR d.recheck_after <= CURRENT_DATE)
  AND d.recheck_count < 12;  -- give up after ~12 attempts (90 days weekly)

-- Recently confirmed deals for alert digest
CREATE VIEW unalerted_confirmed AS
SELECT d.*
FROM deals d
WHERE d.stage = 'confirmed'
  AND d.id NOT IN (
      SELECT deal_id FROM alert_log WHERE alert_type = 'digest'
  )
ORDER BY d.updated_at DESC;

CREATE UNIQUE INDEX IF NOT EXISTS idx_deals_semantic_dedup
ON deals (acquiring_entity, states, acquisition_date, facility_count)
WHERE acquiring_entity IS NOT NULL
AND acquisition_date IS NOT NULL;
