-- GridRunner private license registry (Neon/PostgreSQL).
-- Apply from a trusted operator environment only:
--   psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f db/license_registry.sql
-- Never put customer license keys or DATABASE_URL values in this repository.

CREATE TABLE IF NOT EXISTS licenses (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    license_key TEXT NOT NULL UNIQUE,
    license_type TEXT NOT NULL DEFAULT 'full'
        CHECK (license_type IN ('full', 'trial')),
    expires_at TIMESTAMPTZ,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    customer_email TEXT,
    phone TEXT,
    stripe_session_id TEXT UNIQUE,
    sol_tx_signature TEXT UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (license_key ~ '^LB-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$')
);

CREATE INDEX IF NOT EXISTS licenses_active_key_idx
    ON licenses (license_key)
    WHERE is_active = TRUE;
