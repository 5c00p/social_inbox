-- Migration 004: Deduplication for comment-to-DM (one reply per comment).
-- Implements §8.8 of CLAUDE.md.

CREATE TABLE IF NOT EXISTS comment_replies_dedup (
    comment_id          TEXT PRIMARY KEY,
    replied_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Auto-cleanup: keep dedup entries for 90 days.
-- Cron job in Task 14 will purge older entries; for now just an index hint.
CREATE INDEX IF NOT EXISTS idx_dedup_replied_at
    ON comment_replies_dedup(replied_at);
