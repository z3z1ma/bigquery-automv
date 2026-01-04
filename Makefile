.PHONY: style lint typecheck check test help

# ensure this is in sync with .pre-commit-config.yaml
RUFF_VERSION := 0.14.10

help:
	@echo "Available targets:"
	@echo "  make style    - Format code with ruff"
	@echo "  make lint     - Check code with ruff"
	@echo "  make typecheck - Run mypy type checking"
	@echo "  make test     - Run tests with pytest"
	@echo "  make check    - Run style, lint, and typecheck"

style:
	uvx ruff@$(RUFF_VERSION) format .

lint:
	uvx ruff@$(RUFF_VERSION) check .

typecheck:
	uv run mypy src/

test:
	uv run pytest

check: style lint typecheck
