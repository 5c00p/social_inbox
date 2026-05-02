-- Migration 002: Message log and raw event log.
-- Implements §8.3, §8.7 of CLAUDE.md.

-- Forward declaration for scenarios FK
-- (scenarios table is created in 003; we use deferred FK there)

CREATE TABLE IF NOT EXISTS messages (
    id                  BIGSERIAL PRIMARY KEY,
    conversation_id     BIGINT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    direction           TEXT NOT NULL CHECK (direction IN ('in', 'out')),
    text                TEXT,
    media_url           TEXT,
    media_type          TEXT,
    source              TEXT,                    -- dm, comment, comment_private_reply
    scenario_id         BIGINT,                  -- FK added in migration 003
    claude_used         BOOLEAN NOT NULL DEFAULT FALSE,
    claude_model        TEXT,
    claude_tokens_in    INTEGER,
    claude_tokens_out   INTEGER,
    safety_blocked      BOOLEAN NOT NULL DEFAULT FALSE,
    safety_reason       TEXT,
    external_message_id TEXT UNIQUE,             -- platform message id, idempotency key
    raw_payload         JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages(conversation_id, created_at);

CREATE INDEX IF NOT EXISTS idx_messages_created
    ON messages(created_at DESC);

-- Raw webhook payloads — for debugging and audit.
-- Retention: 30 days (cron in Task 14 will purge).
CREATE TABLE IF NOT EXISTS events_log (
    id                  BIGSERIAL PRIMARY KEY,
    provider_name       TEXT NOT NULL,
    platform            TEXT,
    event_type          TEXT NOT NULL,
    external_event_id   TEXT,
    payload             JSONB NOT NULL,
    signature_valid     BOOLEAN NOT NULL,
    processed_at        TIMESTAMPTZ,
    error               TEXT,
    received_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_events_received
    ON events_log(received_at DESC);

CREATE INDEX IF NOT EXISTS idx_events_unprocessed
    ON events_log(received_at) WHERE processed_at IS NULL;

-- Idempotency: same external_event_id from same provider should not be reprocessed.
CREATE UNIQUE INDEX IF NOT EXISTS uq_events_provider_external
    ON events_log(provider_name, external_event_id)
    WHERE external_event_id IS NOT NULL;
