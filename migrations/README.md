# Migrations

Raw SQL migrations applied at app startup by `app.repos.pool.run_migrations()`.

## Naming convention

`NNN_<short_description>.sql` where `NNN` is a zero-padded 3-digit number.
Migrations are applied in lexicographic order.

## Rules

- Each file MUST be idempotent (use `IF NOT EXISTS`, `IF EXISTS`).
- Each file is wrapped in a transaction by the runner — no `BEGIN`/`COMMIT` inside.
- Once committed to a deployed environment, a migration MUST NOT be edited;
  create a new one instead.
- A migration that fails will be retried on next startup —
  it must therefore be safe to re-apply partially.

## Current migrations

| File | Description |
|------|-------------|
| 001_users_conversations.sql | Core: social_users, conversations |
| 002_messages_events.sql     | Message log + raw webhook event log |
| 003_scenarios_keywords.sql  | Scenario templates, keyword triggers, comment triggers |
| 004_dedup.sql               | Deduplication for comment-to-DM |

## Manual operations

To inspect applied migrations:

```sql
SELECT * FROM _migrations ORDER BY applied_at;
```

To force re-run a migration (DESTRUCTIVE — only on dev):

```sql
DELETE FROM _migrations WHERE filename = '003_scenarios_keywords.sql';
-- then drop affected tables manually, then restart app
```
