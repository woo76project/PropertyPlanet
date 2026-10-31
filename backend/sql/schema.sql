-- Property Planets V3: complete schema for an EMPTY application database.
-- Run once from backend: python -m scripts.run_sql sql/schema.sql
-- Includes final account states, duplicate prevention, import queues, credits,
-- audit constraints and search indexes. No Stripe tables or incremental ALTERs.
-- Start with an empty application schema. Existing tables must be removed manually.
-- No table drops, data inserts, Admin seeds or changes to Clerk accounts are performed.

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Match Python's strip(): trim surrounding whitespace, keep case and internal spaces.
CREATE OR REPLACE FUNCTION property_duplicate_text(value TEXT) RETURNS TEXT
LANGUAGE SQL IMMUTABLE PARALLEL SAFE AS $$
    SELECT btrim(coalesce(value, ''),
        E' \t\n\r\f\013' ||
        U&'\001c\001d\001e\001f\0085\00a0\1680\2000\2001\2002\2003\2004\2005\2006\2007\2008\2009\200a\2028\2029\202f\205f\3000');
$$;

CREATE TABLE users (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    clerk_user_id TEXT NOT NULL UNIQUE,

    username TEXT UNIQUE,
    email TEXT NOT NULL UNIQUE,
    first_name TEXT,
    last_name TEXT,
    phone TEXT,

    role TEXT NOT NULL DEFAULT 'User'
        CHECK (role IN ('Admin', 'User')),

    status TEXT NOT NULL DEFAULT 'Active'
        CHECK (status IN ('Active', 'Suspended', 'Deleting', 'Deleted')),

    -- Current User balance; new accounts start at zero. Admin searches bypass credits.
    -- Assignment and paid-search history are stored in their own tables below.
    credit_balance INTEGER NOT NULL DEFAULT 0
        CHECK (credit_balance BETWEEN 0 AND 1000000000),

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE properties (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    owner_name TEXT NOT NULL CHECK (BTRIM(owner_name) <> ''),
    phone_01 TEXT NOT NULL CHECK (BTRIM(phone_01) <> ''),
    phone_02 TEXT,
    ic_number TEXT,
    address TEXT,

    remark TEXT,

    created_by BIGINT NOT NULL REFERENCES users(id),
    updated_by BIGINT REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    duplicate_key BYTEA GENERATED ALWAYS AS (
        digest(
            length(property_duplicate_text(owner_name))::text || ':' || property_duplicate_text(owner_name) ||
            length(property_duplicate_text(phone_01))::text || ':' || property_duplicate_text(phone_01) ||
            length(property_duplicate_text(phone_02))::text || ':' || property_duplicate_text(phone_02) ||
            length(property_duplicate_text(ic_number))::text || ':' || property_duplicate_text(ic_number) ||
            length(property_duplicate_text(address))::text || ':' || property_duplicate_text(address) ||
            length(property_duplicate_text(remark))::text || ':' || property_duplicate_text(remark),
            'sha256'
        )
    ) STORED,
    CONSTRAINT properties_duplicate_key_unique UNIQUE (duplicate_key)
);

CREATE TABLE audit_logs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor_user_id BIGINT NOT NULL REFERENCES users(id),
    action TEXT NOT NULL CHECK (action IN (
        'property.created', 'property.updated', 'property.deleted',
        'user.suspended', 'user.reactivated', 'user.credits_assigned', 'user.credits_adjusted',
        'properties.imported', 'properties.exported'
    )),
    entity_type TEXT NOT NULL CHECK (entity_type IN ('property', 'user')),
    -- No foreign key here: history must survive a property being deleted.
    entity_id BIGINT CHECK (entity_id > 0),
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX audit_logs_actor_id_idx ON audit_logs (actor_user_id, id DESC);

CREATE TABLE property_import_jobs (
    id UUID PRIMARY KEY,
    created_by BIGINT NOT NULL REFERENCES users(id),
    filename TEXT NOT NULL,
    file_size BIGINT NOT NULL CHECK (file_size BETWEEN 1 AND 104857600),
    storage_bucket TEXT,
    status TEXT NOT NULL DEFAULT 'uploading' CHECK (status IN (
        'uploading', 'queued', 'processing', 'completed', 'completed_with_errors', 'failed', 'expired'
    )),
    uploaded_bytes BIGINT NOT NULL DEFAULT 0 CHECK (uploaded_bytes >= 0 AND uploaded_bytes <= file_size),
    total_records BIGINT CHECK (total_records BETWEEN 0 AND 500000),
    last_row BIGINT NOT NULL DEFAULT 1 CHECK (last_row >= 1),
    processed_rows BIGINT NOT NULL DEFAULT 0 CHECK (processed_rows BETWEEN 0 AND 500000),
    imported_count BIGINT NOT NULL DEFAULT 0 CHECK (imported_count >= 0),
    duplicate_count BIGINT NOT NULL DEFAULT 0 CHECK (duplicate_count >= 0),
    invalid_count BIGINT NOT NULL DEFAULT 0 CHECK (invalid_count >= 0),
    worker_token UUID,
    lease_until TIMESTAMPTZ,
    error_message TEXT,
    files_deleted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (processed_rows = imported_count + duplicate_count + invalid_count)
);

CREATE INDEX property_import_jobs_queue_idx ON property_import_jobs (status, created_at);
CREATE INDEX property_import_jobs_cleanup_idx ON property_import_jobs (updated_at, id)
    WHERE files_deleted_at IS NULL AND status IN ('uploading', 'failed', 'expired', 'completed', 'completed_with_errors');

CREATE TABLE property_import_chunks (
    job_id UUID NOT NULL REFERENCES property_import_jobs(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
    byte_count INTEGER NOT NULL CHECK (byte_count BETWEEN 1 AND 5242880),
    sha256 TEXT NOT NULL,
    PRIMARY KEY (job_id, chunk_index)
);

CREATE TABLE property_import_errors (
    job_id UUID NOT NULL REFERENCES property_import_jobs(id) ON DELETE CASCADE,
    row_number BIGINT NOT NULL CHECK (row_number > 1),
    errors JSONB NOT NULL,
    PRIMARY KEY (job_id, row_number)
);

-- Top-up history and retry protection; current balances live in users.
CREATE TABLE credit_assignments (
    request_id UUID PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    actor_user_id BIGINT NOT NULL REFERENCES users(id),
    amount INTEGER NOT NULL CHECK (amount BETWEEN 1 AND 1000000),
    reference TEXT NOT NULL CHECK (length(reference) BETWEEN 1 AND 200),
    balance_after INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX credit_assignments_user_idx ON credit_assignments (user_id, created_at DESC);

CREATE TABLE credit_adjustments (
    request_id UUID PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    actor_user_id BIGINT NOT NULL REFERENCES users(id),
    delta INTEGER NOT NULL CHECK (delta <> 0 AND delta BETWEEN -1000000 AND 1000000),
    reason TEXT NOT NULL CHECK (LENGTH(BTRIM(reason)) BETWEEN 1 AND 200),
    balance_before INTEGER NOT NULL CHECK (balance_before BETWEEN 0 AND 1000000000),
    balance_after INTEGER NOT NULL CHECK (balance_after BETWEEN 0 AND 1000000000),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (balance_after = balance_before + delta)
);

CREATE INDEX credit_adjustments_user_idx ON credit_adjustments (user_id, created_at DESC);

-- A paid query's access record: retries/pages reuse it instead of spending again.
CREATE TABLE credit_searches (
    request_id UUID PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id),
    query_hash TEXT NOT NULL,
    max_property_id BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX credit_searches_user_idx ON credit_searches (user_id, created_at DESC);

-- Six individual GIN indexes for normal-search OR branches. Search Builder
-- and free previews also use the name/address indexes.
CREATE INDEX IF NOT EXISTS properties_owner_name_trgm ON properties USING GIN (owner_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS properties_address_trgm ON properties USING GIN (address gin_trgm_ops);
CREATE INDEX IF NOT EXISTS properties_phone_01_trgm ON properties USING GIN (phone_01 gin_trgm_ops);
CREATE INDEX IF NOT EXISTS properties_phone_02_trgm ON properties USING GIN (phone_02 gin_trgm_ops);
CREATE INDEX IF NOT EXISTS properties_ic_number_trgm ON properties USING GIN (ic_number gin_trgm_ops);
CREATE INDEX IF NOT EXISTS properties_remark_trgm ON properties USING GIN (remark gin_trgm_ops);

-- Builder equality/prefix queries use GIN-compatible candidates with a
-- case-insensitive recheck. No extra prefix indexes; all clues require 3+ chars.
ANALYZE properties;
