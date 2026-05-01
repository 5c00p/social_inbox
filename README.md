# social_inbox

Automated messaging service for Instagram/Facebook lead capture.
Part of the Yulia Purify ecosystem (alongside bot_purify, purify-marathon).

See `CLAUDE.md` for full architectural specification.

## Quick start

```bash
cp .env.example .env
# edit .env — fill INTERNAL_API_TOKEN at minimum

make install      # uv sync
make up           # docker compose up -d
curl http://localhost:8000/health
```

## Development

```bash
make lint         # ruff + mypy
make format       # auto-format
make test         # pytest
make logs         # tail container logs
```

## Architecture

See `CLAUDE.md` § 4 for architecture diagram.
See `docs/tasks/` for the development roadmap.
