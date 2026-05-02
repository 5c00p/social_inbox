-- Migration 007: Seed default welcome scenario.
--
-- This scenario is triggered by ScenarioEngine when a brand-new user sends
-- their first DM and there's no keyword match.
--
-- Template uses placeholders resolved at runtime by welcome.py:
--   {first_name}  — user's first name (or 'дорогая' if missing)
--   {tg_link}     — full deep-link URL to @yuliya_purify_bot
--   {disclaimer}  — AI-assistant disclaimer (Meta + legal compliance)

INSERT INTO scenarios (name, type, template, metadata, active)
VALUES (
    'default_welcome',
    'welcome',
    E'🌿 Привет, {first_name}!\n\nРада видеть тебя здесь 💚 Я — Юлия, консультант doTERRA. Помогаю женщинам перейти на здоровый образ жизни через эфирные масла и программу «Очищение».\n\nЧтобы я могла рассказать тебе подробнее и подобрать что подойдёт именно тебе, переходи в Telegram — там удобнее общаться:\n\n👉 {tg_link}\n\n{disclaimer}',
    '{"tg_scenario_slug": "purify", "quick_replies": [{"title": "Перейти в Telegram", "type": "url", "payload": "{tg_link}"}, {"title": "Узнать больше", "type": "postback", "payload": "more_info"}]}'::jsonb,
    TRUE
)
ON CONFLICT (name) DO NOTHING;
