-- Migration 003: Scenario templates, keyword triggers, comment-post triggers.
-- Implements §8.4, §8.5, §8.6 of CLAUDE.md.

CREATE TABLE IF NOT EXISTS scenarios (
    id                      BIGSERIAL PRIMARY KEY,
    name                    TEXT NOT NULL UNIQUE,
    type                    TEXT NOT NULL
                            CHECK (type IN ('welcome', 'comment_to_dm', 'faq', 'handover', 'smart')),
    template                TEXT,
    quick_replies           JSONB,                -- [{title, payload}, ...]
    claude_system_prompt    TEXT,
    claude_model            TEXT DEFAULT 'claude-sonnet-4-6',
    next_scenario_id        BIGINT REFERENCES scenarios(id),
    active                  BOOLEAN NOT NULL DEFAULT TRUE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Add deferred FK from messages.scenario_id (declared in 002).
ALTER TABLE messages
    ADD CONSTRAINT fk_messages_scenario
    FOREIGN KEY (scenario_id) REFERENCES scenarios(id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS keywords (
    id                  BIGSERIAL PRIMARY KEY,
    keyword             TEXT NOT NULL,
    match_type          TEXT NOT NULL CHECK (match_type IN ('exact', 'contains', 'regex')),
    context             TEXT NOT NULL CHECK (context IN ('dm', 'comment', 'both')),
    scenario_id         BIGINT NOT NULL REFERENCES scenarios(id) ON DELETE CASCADE,
    priority            INTEGER NOT NULL DEFAULT 100,
    case_sensitive      BOOLEAN NOT NULL DEFAULT FALSE,
    active              BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_keywords_active
    ON keywords(priority, id) WHERE active = TRUE;

CREATE TABLE IF NOT EXISTS comment_triggers (
    id                  BIGSERIAL PRIMARY KEY,
    platform            TEXT NOT NULL CHECK (platform IN ('instagram', 'facebook')),
    post_id             TEXT NOT NULL,
    keyword             TEXT NOT NULL,
    scenario_id         BIGINT NOT NULL REFERENCES scenarios(id) ON DELETE CASCADE,
    active              BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (platform, post_id, keyword)
);
