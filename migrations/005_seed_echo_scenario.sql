-- Migration 005: Widen scenarios.type constraint and seed echo_scenario.
-- 'echo' type is the ScenarioEngine catch-all fallback used in testing.
-- Replaced by smart/FAQ in Task 13, but the type must remain valid in schema.

ALTER TABLE scenarios DROP CONSTRAINT scenarios_type_check;
ALTER TABLE scenarios ADD CONSTRAINT scenarios_type_check
    CHECK (type IN ('welcome', 'comment_to_dm', 'faq', 'handover', 'smart', 'echo'));

INSERT INTO scenarios (name, type, template, active)
VALUES ('echo_scenario', 'echo', 'Received: {text}', TRUE)
ON CONFLICT (name) DO NOTHING;
