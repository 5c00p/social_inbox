-- Migration 001: Core entities — social_users and conversations.
-- Implements §8.1, §8.2 of CLAUDE.md.

-- A user on a social platform. One physical person on one platform = one row.
-- (Same physical person on Instagram and Facebook = two rows.)
CREATE TABLE IF NOT EXISTS social_users (
    id                  BIGSERIAL PRIMARY KEY,
    platform            TEXT NOT NULL CHECK (platform IN ('instagram', 'facebook')),
    external_id         TEXT NOT NULL,
    provider_name       TEXT NOT NULL CHECK (provider_name IN ('sendpulse', 'manychat', 'meta')),
    username            TEXT,
    full_name           TEXT,
    profile_pic_url     TEXT,
    short_id            TEXT NOT NULL UNIQUE,
    first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_message_at     TIMESTAMPTZ,
    smart_mode_enabled  BOOLEAN NOT NULL DEFAULT TRUE,
    tg_handover_at      TIMESTAMPTZ,
    tg_user_id          BIGINT,
    deleted_at          TIMESTAMPTZ,
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (provider_name, platform, external_id)
);

CREATE INDEX IF NOT EXISTS idx_social_users_short_id
    ON social_users(short_id);

CREATE INDEX IF NOT EXISTS idx_social_users_last_message
    ON social_users(last_message_at DESC) WHERE deleted_at IS NULL;

-- A logical conversation. One per (user, platform). Can be closed and reopened.
CREATE TABLE IF NOT EXISTS conversations (
    id                  BIGSERIAL PRIMARY KEY,
    user_id             BIGINT NOT NULL REFERENCES social_users(id) ON DELETE CASCADE,
    platform            TEXT NOT NULL CHECK (platform IN ('instagram', 'facebook')),
    status              TEXT NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'closed', 'handover_pending', 'handover_done')),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_message_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at           TIMESTAMPTZ,
    handover_reason     TEXT
);

CREATE INDEX IF NOT EXISTS idx_conversations_user
    ON conversations(user_id);

CREATE INDEX IF NOT EXISTS idx_conversations_status
    ON conversations(status) WHERE status != 'closed';
