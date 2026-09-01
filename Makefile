.PHONY: sync lint format typecheck test check

sync:
	uv sync --all-groups

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .
	uv run ruff check --fix .

typecheck:
	uv run mypy

# pytest exits 5 when it collects no tests; tolerate that until the first test lands.
test:
	uv run pytest || [ $$? -eq 5 ]

check: lint typecheck test
