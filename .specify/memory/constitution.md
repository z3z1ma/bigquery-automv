<!--
  Sync Impact Report
  ==================
  Version Change: INITIAL → 1.0.0
  Rationale: Initial constitution ratification for bigquery-automv project

  Principles Added:
  - I. Python Modernity (Python 3.14+, type hints, async-first)
  - II. uv Package Management (exclusive use of uv, no pip/poetry)
  - III. CLI-First Design (primary interface is command-line)
  - IV. Data Safety (dry-run mode, explicit confirmation, idempotency)
  - V. Observability (structured logging, query tracking)

  Sections Added:
  - Development Standards (pre-commit, ruff, Makefile)
  - BigQuery Best Practices (query patterns, cost awareness)

  Templates Status:
  - plan-template.md: ✅ Compatible (Constitution Check section present)
  - spec-template.md: ✅ Compatible (User story structure aligns with CLI-first design)
  - tasks-template.md: ✅ Compatible (Task structure supports Python development)

  No placeholders deferred - all fields populated.
-->

# BigQuery AutoMV Constitution

## Core Principles

### I. Python Modernity

All code MUST use modern Python practices:

- **Python Version**: Python 3.14+ minimum (via pyproject.toml `requires-python`)
- **Type Hints**: Required for all function signatures and complex types
- **Async-First**: Use async/await for I/O-bound operations (BigQuery API calls)
- **Rationale**: Modern Python ensures maintainability, better IDE support, and
  leverages latest language features for cleaner, more efficient code.

### II. uv Package Management (NON-NEGOTIABLE)

All package management MUST use `uv` exclusively:

- **FORBIDDEN**: pip, pip freeze, virtualenv, poetry, pipenv
- **REQUIRED**: Use `uv add <package>` for dependencies
- **REQUIRED**: Use `uv add --dev <package>` for dev dependencies
- **REQUIRED**: Use `uv sync` to synchronize environments
- **REQUIRED**: Use `uvx ruff` for code quality tools (via Makefile)
- **Manual pyproject.toml edits**: ONLY `[tool.*]` sections permitted
- **Rationale**: uv provides fast, reliable dependency management. Mixed tools
  cause lockfile conflicts and reproducibility issues.

### III. CLI-First Design

Every feature MUST be accessible via command-line interface:

- **Entry Point**: `bigquery-automv` command (defined in `[project.scripts]`)
- **Text Protocol**: stdin/args for input, stdout for results, stderr for errors
- **Output Formats**: Support human-readable (default) + JSON (via `--json` flag)
- **Dry-Run**: All destructive operations MUST support `--dry-run` mode
- **Rationale**: CLI enables automation, scripting, and CI/CD integration. Text
  I/O ensures debuggability and composability with Unix tools.

### IV. Data Safety

All operations that modify BigQuery resources MUST follow safety protocols:

- **Dry-Run Default**: Analysis commands MUST NOT modify resources by default
- **Explicit Confirmation**: Destructive operations (MV creation) require explicit
  user action or `--yes` flag
- **Idempotency**: Running the same command twice MUST be safe
- **Query Patterns**: Identify candidates from INFORMATION_SCHEMA.JOBS without
  executing user queries
- **Rationale**: Materialized views incur storage costs and query complexity.
  Automated creation must be safe, reversible, and cost-conscious.

### V. Observability

All operations MUST emit structured, actionable output:

- **Structured Logging**: Use JSON-formatted logs for machine parsing
- **Verbosity Levels**: Support `-v`/`--verbose` for detailed operation traces
- **Query Metadata**: Track query patterns, frequencies, and estimated savings
- **Error Context**: Errors MUST include relevant query IDs, timestamps, and
  recovery suggestions
- **Rationale**: Debugging distributed systems (BigQuery + local tool) requires
  rich context. Structured logs enable monitoring and analysis.

## Development Standards

### Code Quality

All code MUST pass automated quality gates before commit:

- **Pre-commit Hooks**: Configured in `.pre-commit-config.yaml`, run on `git commit`
- **Formatting**: `ruff format` (via `make style`)
- **Linting**: `ruff check` (via `make lint`)
- **Installation**: `pre-commit install` (one-time per machine)

### Testing Strategy

Tests are OPTIONAL unless explicitly requested in feature specification:

- When tests ARE requested: Follow TDD (tests written first, must fail, then implement)
- Test types: Unit (pytest), Integration (BigQuery emulator or test project)
- Mock BigQuery API calls for unit tests to avoid costs

## BigQuery Best Practices

### Query Analysis

- **Source**: INFORMATION_SCHEMA.JOBS (region-scoped or aggregate)
- **Candidates**: Queries with repetition patterns, high cost, or stable results
- **Exclusions**: Ad-hoc queries, one-time operations, already-optimized queries

### Materialized View Design

- **Smart Tuning**: Leverage BigQuery automatic query routing
- **Refresh Intervals**: Match query patterns (e.g., daily for ETL workflows)
- **Partitioning/Clustering**: Inherit from source tables where applicable
- **Cost Awareness**: Estimate storage vs. compute trade-offs before creation

## Governance

This constitution governs all development activity for bigquery-automv.

### Amendment Procedure

1. Propose changes via issue tracking (bd)
2. Document rationale and impact analysis
3. Update version according to semantic versioning:
   - **MAJOR**: Backward-incompatible principle removal/redefinition
   - **MINOR**: New principle or section added
   - **PATCH**: Clarifications, wording improvements, non-semantic changes
4. Propagate changes to dependent templates (plan, spec, tasks)
5. Update all agents and workflows to reflect new principles

### Compliance

- All feature specifications MUST pass Constitution Check (see plan-template.md)
- PR reviews MUST verify adherence to Core Principles
- Violations MUST be justified in Complexity Tracking table
- Use CLAUDE.md for runtime development guidance

**Version**: 1.0.0 | **Ratified**: 2026-01-03 | **Last Amended**: 2026-01-03
