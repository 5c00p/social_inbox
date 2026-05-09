.PHONY: install up down logs lint format test migrate shell help

help:
	@echo "Available targets:"
	@echo "  install   - Install dependencies via uv"
	@echo "  up        - Start docker compose stack"
	@echo "  down      - Stop docker compose stack"
	@echo "  logs      - Tail logs from api and worker"
	@echo "  lint      - Run ruff and mypy"
	@echo "  format    - Auto-format code with ruff"
	@echo "  test      - Run pytest"
	@echo "  shell     - Open Python shell in api container"

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
