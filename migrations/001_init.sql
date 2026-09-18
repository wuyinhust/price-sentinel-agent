-- PriceSentinel schema, Postgres edition.
--
-- Mirrors the SQLite reference schema in src/pricesentinel/store.py, with the
-- changes a multi-tenant deployment needs. Read the notes on CASES below: the
-- SQLite store's read-then-write case de-duplication is not safe under
-- concurrent ingestion and this schema fixes that.
--
-- Apply with:  psql -d pricesentinel -f migrations/001_init.sql

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid()

-- Prices are money. REAL/float64 cannot represent 0.1 exactly, and the breach
-- decision compares against a floor, so rounding error decides cases.
-- numeric(14,2) is exact. The Python layer still uses float/Decimal; a
-- deployment should move PricePolicy.floor_price to Decimal before cutting over.
CREATE DOMAIN money_amount AS numeric(14, 2);

CREATE TABLE products (
    product_id      text PRIMARY KEY,
    brand           text NOT NULL,
    name            text NOT NULL,
    sku             text,
    vintage         text,
    unit_volume_ml  integer CHECK (unit_volume_ml IS NULL OR unit_volume_ml > 0),
    units_per_pack  integer NOT NULL DEFAULT 1 CHECK (units_per_pack > 0),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE policies (
    product_id      text NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
    package_type    text NOT NULL,
    floor_price     money_amount NOT NULL,
    effective_from  date NOT NULL,
    effective_to    date,
    currency        char(3) NOT NULL DEFAULT 'CNY',
    channel_scope   text NOT NULL DEFAULT '*',
    PRIMARY KEY (product_id, package_type, effective_from, channel_scope),
    CONSTRAINT policies_window_ordered
        CHECK (effective_to IS NULL OR effective_to >= effective_from)
);

-- Observations are append-only evidence. Nothing here should ever be UPDATEd.
CREATE TABLE observations (
    observation_id  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id      text NOT NULL REFERENCES products(product_id) ON DELETE RESTRICT,
    package_type    text NOT NULL,
    channel         text NOT NULL,
    merchant        text NOT NULL,
    observed_at     timestamptz NOT NULL,
    listed_price    money_amount,
    net_price       money_amount,
    currency        char(3) NOT NULL DEFAULT 'CNY',
    listing_url     text,
    evidence_url    text,
    source          text,
    metadata        jsonb,
    ingested_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT observations_has_a_price CHECK (listed_price IS NOT NULL OR net_price IS NOT NULL)
);

CREATE INDEX observations_lookup_idx
    ON observations (product_id, package_type, channel, merchant, observed_at DESC);

CREATE TYPE case_status AS ENUM
    ('open', 'acknowledged', 'in_remediation', 'resolved', 'closed', 'dismissed');
CREATE TYPE case_severity AS ENUM ('low', 'medium', 'high', 'critical');

CREATE TABLE cases (
    case_id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id              text NOT NULL REFERENCES products(product_id) ON DELETE RESTRICT,
    package_type            text NOT NULL,
    channel                 text NOT NULL,
    merchant                text NOT NULL,
    first_detected_at       timestamptz NOT NULL,
    last_detected_at        timestamptz NOT NULL,
    latest_observation_id   uuid NOT NULL REFERENCES observations(observation_id) ON DELETE RESTRICT,
    severity                case_severity NOT NULL,
    status                  case_status NOT NULL DEFAULT 'open',
    assigned_to             text,
    sla_due_at              timestamptz,
    resolution_reason       text,
    CONSTRAINT cases_detection_order CHECK (last_detected_at >= first_detected_at),
    CONSTRAINT cases_resolution_consistent CHECK (
        (status IN ('resolved', 'closed', 'dismissed')) = (resolution_reason IS NOT NULL)
    )
);

-- The open-case key. The SQLite store reads for an active case then writes,
-- which races: two concurrent ingests can both miss and both insert. A partial
-- unique index lets the writer use INSERT ... ON CONFLICT and let the database
-- arbitrate instead.
CREATE UNIQUE INDEX cases_one_active_per_key
    ON cases (product_id, package_type, channel, merchant)
    WHERE status IN ('open', 'acknowledged', 'in_remediation');

CREATE INDEX cases_triage_idx ON cases (status, severity, last_detected_at DESC);

CREATE TABLE case_events (
    event_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    case_id     uuid NOT NULL REFERENCES cases(case_id) ON DELETE CASCADE,
    event_type  text NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    actor       text,
    payload     jsonb
);

CREATE INDEX case_events_trail_idx ON case_events (case_id, occurred_at);

-- The audit trail must be append-only. Enforce it in the database rather than
-- trusting every future caller.
CREATE RULE case_events_no_update AS ON UPDATE TO case_events DO INSTEAD NOTHING;
CREATE RULE case_events_no_delete AS ON DELETE TO case_events DO INSTEAD NOTHING;

COMMIT;
