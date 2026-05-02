-- Migration 006: scenarios.metadata for flexible per-scenario configuration.
--
-- Used by:
-- - welcome scenario: stores tg_scenario_slug for deep-link payload
-- - future scenarios: any non-schema config (e.g. claude tool list, A/B variant)
-- Avoids ALTER TABLE for each new optional config field.

ALTER TABLE scenarios
    ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;
