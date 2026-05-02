-- Migration 008: per-(user, post) idempotency for comment-to-DM.
--
-- Existing comment_replies_dedup (migration 004) is keyed by comment_id alone,
-- which protects against the same comment being processed twice (rare —
-- arq + events_log unique idx already help). The harder problem is:
-- one user posts MULTIPLE comments under the same Reels (e.g. spamming
-- "ОЧИЩЕНИЕ ОЧИЩЕНИЕ ОЧИЩЕНИЕ" in 3 separate comments). We want
-- exactly ONE DM, not three.
--
-- Add a separate composite uniqueness on (social_user_id, platform, post_id, scenario_id)
-- via a new table. Reusing comment_replies_dedup wouldn't fit semantically.

CREATE TABLE IF NOT EXISTS comment_user_dedup (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES social_users(id) ON DELETE CASCADE,
    platform        TEXT NOT NULL CHECK (platform IN ('instagram', 'facebook')),
    post_id         TEXT NOT NULL,
    scenario_id     BIGINT NOT NULL REFERENCES scenarios(id) ON DELETE CASCADE,
    replied_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, platform, post_id, scenario_id)
);

CREATE INDEX IF NOT EXISTS idx_comment_user_dedup_user
    ON comment_user_dedup(user_id);
