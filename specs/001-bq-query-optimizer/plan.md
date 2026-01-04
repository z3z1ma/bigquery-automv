# Implementation Plan: BigQuery Query Optimizer

**Branch**: `001-bq-query-optimizer` | **Date**: 2026-01-03 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/001-bq-query-optimizer/spec.md`

**Note**: This template is filled in by the `/speckit.plan` command. See `.specify/templates/commands/plan.md` for the execution workflow.

## Summary

Build a CLI tool that analyzes BigQuery INFORMATION_SCHEMA.JOBS to identify expensive, repetitive query patterns and automatically generate materialized views optimized for BigQuery Smart Tuning. The tool uses sqlglot for SQL parsing to validate Smart Tuning eligibility (including the critical "predicate lifting" pattern) and deploys MVs with appropriate WHERE clause filters.

**Technical approach**:
1. Query INFORMATION_SCHEMA.JOBS grouped by `normalized_literals` hash
2. Parse representative queries with sqlglot to extract structure
3. Apply Smart Tuning eligibility rules (UNION ALL, LEFT JOIN, aggregations, etc.)
4. Generate MVs using "predicate lifting" algorithm (shared predicates → MV WHERE, varying columns → liftable)
5. Track impact via `materialized_view_statistics`

## Technical Context

**Language/Version**: Python 3.14+
**Primary Dependencies**:
- cyclopts (CLI framework)
- sqlglot (SQL parsing, BigQuery dialect)
- google-cloud-bigquery (BigQuery client)
- aiohttp (async HTTP)

**Storage**: BigQuery tables (INFORMATION_SCHEMA.JOBS for analysis, metadata table for MV tracking)
**Testing**: pytest (with mocking for BigQuery API calls)
**Target Platform**: CLI tool running on macOS/Linux, connects to Google Cloud BigQuery
**Project Type**: single (CLI package)
**Performance Goals**:
- Analyze up to 180 days of query history in under 2 minutes
- Support projects with up to 1 million query jobs
- Generate MV DDL in under 5 seconds per query

**Constraints**:
- INFORMATION_SCHEMA.JOBS has 180-day retention limit
- Smart Tuning requires precise predicate matching (non-liftable filters must match exactly)
- MV storage costs (~$0.02/GB/month) require lifecycle management

**Scale/Scope**:
- Typical: 10-20 optimization candidates per project
- Large: Up to 100 candidates with batch operations
- Data: Up to 1M query jobs × 180 days of history

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- [x] Python 3.14+ with type hints and async/await for I/O operations
- [x] Package management via `uv` only (no pip/poetry)
- [x] CLI interface with `--dry-run` and `--json` output options
- [x] Data safety: idempotent operations, explicit confirmation for destructive actions
- [x] Structured logging with verbosity levels and error context
- [x] Code quality: ruff format/lint, pre-commit hooks configured

## Project Structure

### Documentation (this feature)

```text
specs/[###-feature]/
├── plan.md              # This file (/speckit.plan command output)
├── research.md          # Phase 0 output (/speckit.plan command)
├── data-model.md        # Phase 1 output (/speckit.plan command)
├── quickstart.md        # Phase 1 output (/speckit.plan command)
├── contracts/           # Phase 1 output (/speckit.plan command)
└── tasks.md             # Phase 2 output (/speckit.tasks command - NOT created by /speckit.plan)
```

### Source Code (repository root)

```text
src/bigquery_automv/
├── __init__.py
├── cli/
│   ├── __init__.py
│   ├── app.py           # Cyclopts App() definition with CommonConfig
│   └── commands/
│       ├── __init__.py
│       ├── analyze.py   # analyze command
│       ├── report.py    # report command
│       ├── smart_tuning_check.py
│       ├── generate_mv.py
│       └── impact.py
├── models/
│   ├── __init__.py
│   ├── query_candidate.py
│   ├── mv_artifact.py
│   ├── cost_analysis.py
│   └── impact_report.py
├── services/
│   ├── __init__.py
│   ├── bq_client.py     # BigQuery async client wrapper
│   ├── sql_parser.py    # sqlglot wrapper for parsing
│   ├── analyzer.py      # Query analysis logic
│   ├── smart_tuning.py  # Eligibility checker
│   ├── mv_generator.py  # MV DDL generation
│   └── reporter.py      # Cost calculation
└── lib/
    ├── __init__.py
    ├── config.py        # Configuration models
    ├── logging.py       # Structured logging setup
    └── utils.py         # Utility functions

tests/
├── __init__.py
├── conftest.py          # pytest fixtures (BQ mocks)
├── unit/
│   ├── test_sql_parser.py
│   ├── test_smart_tuning.py
│   ├── test_mv_generator.py
│   └── test_reporter.py
└── integration/
    └── test_analyzer.py  # With mocked BQ client
```

**Structure Decision**: Single-package Python project (Option 1). The tool is a CLI utility with no frontend or mobile components. Source code lives in `src/bigquery_automv/` following modern Python packaging conventions. The `cli/` directory contains command implementations using cyclopts, `models/` contains dataclasses for entities, `services/` contains business logic, and `lib/` contains shared utilities.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| [e.g., 4th project] | [current need] | [why 3 projects insufficient] |
| [e.g., Repository pattern] | [specific problem] | [why direct DB access insufficient] |
