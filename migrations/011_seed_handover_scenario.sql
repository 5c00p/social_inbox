-- Migration 011: Handover scenario + global keyword for operator request.

INSERT INTO scenarios (name, type, template, metadata, active)
VALUES (
    'default_handover',
    'handover',
    E'Хорошо! 💚 Передаю Юле — она ответит лично в течение нескольких часов.\n\nЕсли вопрос срочный, напиши пожалуйста чем могу помочь дополнительно.',
    '{}'::jsonb,
    TRUE
)
ON CONFLICT (name) DO NOTHING;

-- Global keywords routing to handover scenario.
-- contains-match catches phrases like "хочу с оператором", "позовите человека"
-- priority=5: lower number = higher priority; beats other keywords (default 100)
INSERT INTO keywords (keyword, match_type, context, scenario_id, priority, case_sensitive, active)
SELECT 'оператор', 'contains', 'dm', s.id, 5, FALSE, TRUE
FROM scenarios s WHERE s.name = 'default_handover'
ON CONFLICT DO NOTHING;

INSERT INTO keywords (keyword, match_type, context, scenario_id, priority, case_sensitive, active)
SELECT 'администратор', 'contains', 'dm', s.id, 5, FALSE, TRUE
FROM scenarios s WHERE s.name = 'default_handover'
ON CONFLICT DO NOTHING;
