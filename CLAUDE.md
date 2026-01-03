# CLAUDE.md

This project uses **bd (beads)** for issue tracking. See @AGENTS.md for workflow details.

## Project Overview

**bigquery-automv** analyzes BigQuery INFORMATION_SCHEMA.JOBS query history to identify candidates for BigQuery smart tuning, then automatically creates materialized views (MVs) for applicable queries.

Smart tuning allows BigQuery to automatically reroute queries to use materialized views when possible, improving performance without requiring query changes.

**Core functionality:**
- Analyze historical query patterns from INFORMATION_SCHEMA.JOBS
- Identify repetitive queries that would benefit from materialization
- Generate and deploy optimized materialized views
- Track MV usage and effectiveness over time

## Package Management

**CRITICAL**: This project uses `uv` exclusively for Python package management.

- **DO NOT** use `pip install`, `pip freeze`, `virtualenv`, `poetry`, or similar tools
- **DO NOT** manually edit `[project]`, `[project.optional-dependencies]`, `[project.scripts]`, or `[build-system]` in pyproject.toml
- **DO** use `uv` commands: `uv add <package>`, `uv add --dev <package>`, `uv sync`, `uv lock`

**pyproject.toml**: Only manually edit `[tool.*]` sections. Dependencies are managed by `uv add` commands.

## Code Quality

### Pre-commit Hooks

Pre-commit hooks are configured in `.pre-commit-config.yaml`. They run automatically on git commit.

To install hooks (run once per machine):
```bash
pre-commit install
```

To manually run all hooks:
```bash
pre-commit run --all-files
```

### Makefile Commands

Format and lint code using the provided Makefile:

```bash
make style      # Format code with ruff
make lint       # Check code with ruff
make check      # Run both format and lint checks
```
These use `uvx ruff` to ensure consistent versions without manual installation.
