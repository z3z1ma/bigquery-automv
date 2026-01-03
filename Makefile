.PHONY: style lint check help

# ensure this is in sync with .pre-commit-config.yaml
RUFF_VERSION := 0.14.10

help:
	@echo "Available targets:"
	@echo "  make style  - Format code with ruff"
	@echo "  make lint   - Check code with ruff"
	@echo "  make check  - Run both format and lint checks"

style:
	uvx ruff@$(RUFF_VERSION) format .

lint:
	uvx ruff@$(RUFF_VERSION) check .

check: style lint
