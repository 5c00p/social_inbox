-- Migration 009: Seed default comment-to-DM scenario + global keyword for "ОЧИЩЕНИЕ".

-- Scenario for comment-to-DM with default 'purify' slug.
INSERT INTO scenarios (name, type, template, metadata, active)
VALUES (
    'default_purify_comment',
    'comment_to_dm',
    E'🌿 Привет, {first_name}! Спасибо за интерес к программе «Очищение» 💚\n\nСейчас расскажу подробнее в Telegram — там удобнее и быстрее:\n\n👉 {tg_link}\n\n{disclaimer}',
    '{"tg_scenario_slug": "purify", "public_reply_text": "Отправила в личку 💌", "quick_replies": [{"title": "Перейти в Telegram", "type": "url", "payload": "{tg_link}"}]}'::jsonb,
    TRUE
)
ON CONFLICT (name) DO NOTHING;

-- Global keyword: "ОЧИЩЕНИЕ" anywhere in a comment → trigger this scenario.
INSERT INTO keywords (keyword, match_type, context, scenario_id, priority, case_sensitive, active)
SELECT 'очищение', 'contains', 'comment', s.id, 50, FALSE, TRUE
FROM scenarios s
WHERE s.name = 'default_purify_comment'
ON CONFLICT DO NOTHING;
