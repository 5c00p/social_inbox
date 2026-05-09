-- Migration 010: Seed default smart scenario (Claude-powered fallback).
--
-- Replaces echo_scenario as the engine fallback. Echo remains in DB
-- for backward compatibility and explicit testing scenarios.

INSERT INTO scenarios (name, type, template, metadata, active)
VALUES (
    'default_smart',
    'smart',
    NULL,                                   -- smart doesn't use templates; Claude composes
    '{"claude_model": null}'::jsonb,        -- null → use settings.claude_default_model
    TRUE
)
ON CONFLICT (name) DO NOTHING;
