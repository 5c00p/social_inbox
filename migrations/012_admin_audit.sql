-- Migration 012: Audit log of admin actions in dashboard.
--
-- Records every mutation done by Yulia (or any admin) through the dashboard.
-- Used for post-incident review ("кто закрыл handover пользователю X?")
-- and for general accountability.
--
-- Read-only access from app code (only admin/ writes here).

CREATE TABLE IF NOT EXISTS admin_audit_log (
    id              BIGSERIAL PRIMARY KEY,
    actor           TEXT NOT NULL,                  -- basic_auth username
    action          TEXT NOT NULL,                  -- e.g. 'reply', 'close_handover', 'keyword_create'
    target_type     TEXT,                           -- 'conversation', 'keyword', 'scenario', 'user'
    target_id       BIGINT,                         -- the affected row id
    details         JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_admin_audit_created
    ON admin_audit_log(created_at DESC);
