.PHONY: install up down logs lint format test migrate shell help smoke-local smoke-prod

help:
	@echo "Available targets:"
	@echo "  install      - Install dependencies via uv"
	@echo "  up           - Start docker compose stack"
	@echo "  down         - Stop docker compose stack"
	@echo "  logs         - Tail logs from api and worker"
	@echo "  lint         - Run ruff and mypy"
	@echo "  format       - Auto-format code with ruff"
	@echo "  test         - Run pytest"
	@echo "  shell        - Open Python shell in api container"
	@echo "  smoke-local  - Run smoke checks against http://localhost:8000"
	@echo "  smoke-prod   - Run smoke checks against \$$PROD_BASE_URL"

install:
	uv sync

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f api worker

lint:
	uv run ruff check app/ tests/
	uv run mypy app/

format:
	uv run ruff format app/ tests/
	uv run ruff check --fix app/ tests/

test:
	uv run pytest tests/

shell:
	docker compose exec api python

admin-local:
	uv run streamlit run admin/streamlit_app.py --server.port 8501

admin-logs:
	docker compose logs -f admin

smoke-local:
	uv run python scripts/smoke_test.py --base-url http://localhost:8000 --all

smoke-prod:
	@if [ -z "$$PROD_BASE_URL" ]; then \
		echo "Set PROD_BASE_URL, e.g.: PROD_BASE_URL=https://inbox.your-domain.com make smoke-prod"; \
		exit 1; \
	fi
	uv run python scripts/smoke_test.py --base-url $$PROD_BASE_URL --admin-url $$PROD_ADMIN_URL --all
