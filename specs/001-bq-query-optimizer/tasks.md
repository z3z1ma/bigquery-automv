# Tasks: BigQuery Query Optimizer

**Input**: Design documents from `/specs/001-bq-query-optimizer/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/cli.md, quickstart.md

**Tests**: Tests are OPTIONAL for this feature. Test tasks are only included if explicitly required by the specification. The spec does not mandate TDD approach.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3, US4, US5)
- Include exact file paths in descriptions

## Path Conventions

- **Single project Python package**: `src/bigquery_automv/`, `tests/` at repository root
- All paths are relative to repository root

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and Python package structure with uv

- [ ] T001 Initialize Python project structure with uv in pyproject.toml (Python 3.14+, setuptools backend)
- [ ] T002 [P] Add core dependencies: `uv add cyclopts sqlglot google-cloud-bigquery aiohttp`
- [ ] T003 [P] Add dev dependencies: `uv add --dev pytest pytest-asyncio ruff mypy pre-commit`
- [ ] T004 [P] Create directory structure: src/bigquery_automv/{cli,models,services,lib}/ and tests/{unit,integration}/
- [ ] T005 [P] Configure ruff formatting and linting in pyproject.toml (ruff.toml config section)
- [ ] T006 [P] Setup pre-commit hooks in .pre-commit-config.yaml (ruff, trailing whitespace)
- [ ] T007 Create CLI entry point in pyproject.toml [project.scripts]: `bq-automv = "bigquery_automv.cli.app:main"`
- [ ] T008 [P] Create Makefile with style, lint, test, check targets using uvx ruff

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

### Configuration & Logging

- [ ] T009 Create CommonConfig dataclass in src/bigquery_automv/cli/app.py with @Parameter(name="*") for global flags (project, region, dataset, dry_run, verbose, json, enable_preview_eligibility, include_user_email, include_query_text)
- [ ] T010 [P] Implement structured logging in src/bigquery_automv/lib/logging.py with verbosity levels and JSON support
- [ ] T011 [P] Create configuration models in src/bigquery_automv/lib/config.py (ImpactScoringConfig, MVConfig, AnalysisConfig)

### Core Models (from data-model.md)

- [ ] T012 [P] Create QueryCandidate dataclass in src/bigquery_automv/models/query_candidate.py with all fields from data-model.md
- [ ] T013 [P] Create TableReference dataclass in src/bigquery_automv/models/query_candidate.py (in same file)
- [ ] T014 [P] Create SmartTuningCheckResult dataclass in src/bigquery_automv/models/mv_artifact.py with eligibility fields
- [ ] T015 [P] Create MaterializedViewArtifact dataclass in src/bigquery_automv/models/mv_artifact.py with MVStatus enum
- [ ] T016 [P] Create CostAnalysisResult dataclass in src/bigquery_automv/models/cost_analysis.py with dollar calculations
- [ ] T017 [P] Create ImpactReport and MVImpact dataclasses in src/bigquery_automv/models/impact_report.py

### BigQuery Client Foundation

- [ ] T018 Implement async BigQuery client wrapper in src/bigquery_automv/services/bq_client.py with google-cloud-bigquery async methods, region validation, and error handling

### SQL Parser Foundation

- [ ] T019 Implement sqlglot wrapper in src/bigquery_automv/services/sql_parser.py with BigQuery dialect parsing, AST traversal, aggregation detection, WHERE clause extraction, and unsupported pattern detection

### CLI App Skeleton

- [ ] T020 Create cyclopts App() instance in src/bigquery_automv/cli/app.py with CommonConfig integration and command stubs for analyze, report, smart-tuning-check, generate-mv, impact

**Checkpoint**: Foundation ready - user story implementation can now begin in parallel

---

## Phase 3: User Story 1 - Identify Expensive Repetitive Queries (Priority: P1) 🎯 MVP

**Goal**: Scan BigQuery INFORMATION_SCHEMA.JOBS to identify queries with high execution frequency and resource consumption, ranked by impact score

**Independent Test**: Run analyze command against a project with query history and verify tool correctly identifies queries grouped by normalized_literals hash, sorted by impact_score (frequency × resource consumption)

### Implementation for User Story 1

- [ ] T021 [P] [US1] Create AnalyzerService in src/bigquery_automv/services/analyzer.py with INFORMATION_SCHEMA.JOBS query logic
- [ ] T022 [P] [US1] Implement query grouping by normalized_literals in AnalyzerService with aggregation of execution_count, bytes_billed_total, slot_ms_total
- [ ] T023 [P] [US1] Implement impact scoring in AnalyzerService.calculate_impact_score() using dollar-equivalent model (tib_billed * price_per_tib + slot_weight * slot_tib_equiv)
- [ ] T024 [P] [US1] Create filtering logic in AnalyzerService for min_executions, min_bytes, min_slot_ms, max_families thresholds
- [ ] T025 [P] [US1] Implement time range filtering with creation_time partitioning in AnalyzerService
- [ ] T026 [P] [US1] Add representative query caching in AnalyzerService (sample query text per hash for SQL analysis)
- [ ] T027 [US1] Implement analyze command in src/bigquery_automv/cli/commands/analyze.py with cyclopts decorator, calling AnalyzerService and outputting JSON/CSV/table formats
- [ ] T028 [US1] Add output formatters in src/bigquery_automv/cli/commands/analyze.py for JSON (with meta, candidates, summary structure), CSV, and human-readable table
- [ ] T029 [US1] Add --output file writing in analyze command with format auto-detection from extension
- [ ] T030 [US1] Implement error handling in analyze command for invalid dates, auth failures, query errors with exit codes 0/1/2
- [ ] T031 [US1] Add pagination support in AnalyzerService for INFORMATION_SCHEMA.JOBS queries (handle >1M jobs)
- [ ] T032 [US1] Implement exclusion filters in AnalyzerService: SCRIPT statement_type, NULL total_bytes_billed (with warning), cache hits (NULL referenced_tables)
- [ ] T033 [US1] Add region validation in analyze command (verify region parameter matches INFORMATION_SCHEMA region format)
- [ ] T034 [US1] Integrate CommonConfig in analyze command (inherit --project, --region, --dataset, --dry-run, --verbose, --json flags)
- [ ] T035 [US1] Add logging in analyze command with structured logs for families_analyzed, skipped_with_reasons, runtime_per_phase
- [ ] T036 [US1] Handle NULL total_bytes_billed queries in AnalyzerService (row-level security queries) with warning annotation

**Checkpoint**: At this point, User Story 1 should be fully functional - run `bq-automv analyze --start-date 2024-01-01` and get ranked list of expensive query families

---

## Phase 4: User Story 3 - Detect Smart Tuning Candidates (Priority: P1)

**Goal**: Automatically determine which queries are eligible for BigQuery Smart Tuning based on SQL pattern analysis

**Independent Test**: Run smart-tuning-check with sample queries and verify tool correctly flags eligible queries (aggregations, simple filters) and rejects ineligible ones (UNION ALL, LEFT JOIN, non-deterministic functions)

### Implementation for User Story 3

- [ ] T037 [P] [US3] Create SmartTuningService in src/bigquery_automv/services/smart_tuning.py with sqlglot-based eligibility checker
- [ ] T038 [P] [US3] Implement unsupported pattern detection in SmartTuningService: UNION ALL, LEFT/RIGHT/FULL OUTER JOIN, self-joins, window functions, ARRAY subqueries
- [ ] T039 [P] [US3] Implement non-deterministic function detection in SmartTuningService: RAND, CURRENT_DATE, SESSION_USER, CURRENT_TIME
- [ ] T040 [P] [US3] Implement UDF detection in SmartTuningService (function calls not in built-in list)
- [ ] T041 [P] [US3] Implement aggregate function validation in SmartTuningService (supported list: ANY_VALUE, COUNT, SUM, AVG, etc.)
- [ ] T042 [P] [US3] Implement unsupported aggregate pattern detection: HAVING on aggregates, computed aggregates like COUNT(*) / 10
- [ ] T043 [P] [US3] Add cross-project reference detection in SmartTuningService (referenced_tables with different project_id)
- [ ] T044 [P] [US3] Add region mismatch detection in SmartTuningService (verify all referenced_tables match target dataset region)
- [ ] T045 [P] [US3] Implement logical view reference detection in SmartTuningService (query INFORMATION_SCHEMA.TABLES to check if referenced table is a view)
- [ ] T046 [P] [US3] Create eligibility ruleset in SmartTuningService with rulebook_version field (default "v1.0") and enable_preview_eligibility flag handling
- [ ] T047 [US3] Implement smart-tuning-check command in src/bigquery_automv/cli/commands/smart_tuning_check.py accepting QUERY_HASH positional or --sql/--from-file options
- [ ] T048 [US3] Add --verbose output in smart-tuning-check command showing detailed analysis: aggregation_functions, join_types, has_ctes, unsupported_features, disqualification_reasons
- [ ] T049 [US3] Implement query hash retrieval in smart-tuning-check command (fetch representative query text from cache or INFORMATION_SCHEMA.JOBS by hash)
- [ ] T050 [US3] Add eligibility_basis field in SmartTuningCheckResult ("stable" or "preview") based on enable_preview_eligibility flag and disqualifying patterns
- [ ] T051 [US3] Implement column analysis for predicate lifting in SmartTuningService: extract liftable_columns (SELECT/GROUP BY) and non_liftable_filters (WHERE-only columns)
- [ ] T052 [US3] Add recommended MV structure in SmartTuningCheckResult: recommended_mv_select, recommended_mv_where, recommended_mv_group_by
- [ ] T053 [US3] Integrate SQL parser service in SmartTuningService for parsing with BigQuery dialect
- [ ] T054 [US3] Add error handling in smart-tuning-check for parse failures (exit code 2) with clear error messages
- [ ] T055 [US3] Add rulebook_version parameter in SmartTuningService and output in all SmartTuningCheckResult objects

**Checkpoint**: At this point, User Story 3 should be fully functional - run `bq-automv smart-tuning-check --sql "SELECT COUNT(*) FROM table"` and get eligibility result

---

## Phase 5: User Story 2 - Generate Cost Optimization Report (Priority: P2)

**Goal**: Generate detailed reports showing projected cost savings for optimizing specific queries

**Independent Test**: Run report command with query hashes and verify output includes accurate cost projections based on BigQuery pricing, historical spend, and estimated savings

### Implementation for User Story 2

- [ ] T056 [P] [US2] Create ReporterService in src/bigquery_automv/services/reporter.py with cost calculation logic
- [ ] T057 [P] [US2] Implement historical spend calculation in ReporterService using dollar-equivalent model: (bytes_billed_total / 2^40) * price_per_tib
- [ ] T058 [P] [US2] Implement projected spend calculation in ReporterService: daily_avg_spend = historical / days_analyzed, projected_monthly = daily_avg * 30, projected_yearly = daily_avg * 365
- [ ] T059 [P] [US2] Implement estimated savings calculation in ReporterService for Smart Tuning candidates (70-90% reduction estimate, default 80%)
- [ ] T060 [P] [US2] Add confidence level calculation in ReporterService based on execution_count and variance (HIGH/MEDIUM/LOW)
- [ ] T061 [US2] Implement report command in src/bigquery_automv/cli/commands/report.py accepting QUERY_HASH... positional arguments and --from-file option
- [ ] T062 [US2] Add output formatters in report command for markdown, json, csv formats (--format flag)
- [ ] T063 [US2] Add custom pricing support in ReporterService via --price-per-tib flag (default $6.25)
- [ ] T064 [US2] Integrate with AnalyzerService in report command to fetch candidate data by query hash
- [ ] T065 [US2] Add table output in report command with markdown format showing per-query breakdown: execution_count, historical_spend, projected_monthly, estimated_savings, smart_tuning_eligible
- [ ] T066 [US2] Implement query hash file reading in report command (--from-file option, one hash per line)
- [ ] T067 [US2] Add summary section in report output with total queries analyzed, total historical spend, total projected monthly, total estimated savings
- [ ] T068 [US2] Handle missing query hashes in report command gracefully with warning messages

**Checkpoint**: At this point, User Story 2 should be fully functional - run `bq-automv report abc123... --format markdown` and get cost projections

---

## Phase 6: User Story 4 - Auto-Generate Materialized Views (Priority: P2)

**Goal**: Automatically generate and deploy materialized views for Smart Tuning-eligible queries using predicate lifting algorithm

**Independent Test**: Run generate-mv command against eligible queries and verify: (1) MV DDL statements are correctly generated, (2) views deploy to specified dataset, (3) views can be queried successfully

### Implementation for User Story 4

- [ ] T069 [P] [US4] Create MVGeneratorService in src/bigquery_automv/services/mv_generator.py with DDL generation logic
- [ ] T070 [P] [US4] Implement deterministic MV naming in MVGeneratorService: {prefix}_{family_hash_short}_{signature_hash_short} with collision resistance
- [ ] T071 [P] [US4] Implement signature hash calculation in MVGeneratorService (stable hash of canonical MV SQL for idempotency)
- [ ] T072 [P] [US4] Implement predicate lifting/generalization algorithm in MVGeneratorService per spec: compute shared_predicates (intersection across family), drop varying predicates, identify liftable_columns
- [ ] T073 [P] [US4] Implement MV DDL template in MVGeneratorService with OPTIONS (enable_refresh, refresh_interval_minutes), AS SELECT clause, WHERE clause, GROUP BY
- [ ] T074 [P] [US4] Add MV metadata generation in MVGeneratorService: family_hash, signature_hash, rulebook_version, synthesis_version, synthesis_warnings, eligibility_basis
- [ ] T075 [P] [US4] Implement "covers all query rows" validation in MVGeneratorService (MV WHERE must be superset of all query predicates)
- [ ] T076 [P] [US4] Add GROUP BY validation in MVGeneratorService (must be identical across family sample after canonicalization, fail with group_by_mismatch if not)
- [ ] T077 [P] [US4] Implement single-base-table enforcement in MVGeneratorService (reject queries with any JOIN per Option A conservative policy)
- [ ] T078 [P] [US4] Add region validation in MVGeneratorService (verify all referenced_tables region matches target dataset region, fail with region_mismatch if not)
- [ ] T079 [P] [US4] Implement synthesis_audit generation in MVGeneratorService: sample_size_k, sample_job_ids, dropped_predicates, warnings
- [ ] T080 [P] [US4] Add representative query sampling in MVGeneratorService: top K by bytes_billed, tie-break by creation_time then job_id (default K=20 via --sample-size-k)
- [ ] T081 [US4] Implement generate-mv command in src/bigquery_automv/cli/commands/generate_mv.py accepting QUERY_HASH... positional arguments and --from-file option
- [ ] T082 [US4] Add --dry-run flag in generate-mv command to output DDL without executing
- [ ] T083 [US4] Implement MV deployment in generate-mv command using BigQueryClient (execute DDL when not --dry-run)
- [ ] T084 [US4] Add --replace flag in generate-mv command for drop-and-recreate semantics with explicit confirmation (unless --yes)
- [ ] T085 [US4] Implement idempotency handling in generate-mv command: check if MV exists, compare signature_hash, NO-OP if match, report name_conflict if differ
- [ ] T086 [US4] Add concurrency handling in generate-mv command: handle "already exists" races by re-fetching MV definition and comparing signature_hash
- [ ] T087 [US4] Add --refresh-interval-minutes and --no-refresh flags in generate-mv command for MV OPTIONS configuration
- [ ] T088 [US4] Add --mv-prefix flag in generate-mv command (default "automv_")
- [ ] T089 [US4] Add --name-scheme-version flag in generate-mv command for future naming changes (default "v1")
- [ ] T090 [US4] Implement continue-on-error behavior in generate-mv command (process all hashes even if some fail, report failures at end)
- [ ] T091 [US4] Add --yes/-y flag in generate-mv command to skip confirmation prompt
- [ ] T092 [US4] Implement DDL output formatting in generate-mv command with header comments (MV Name, Family Hash, Signature Hash, Region, Rulebook Version, Synthesis Version, Eligibility Basis)
- [ ] T093 [US4] Add batch operations support in generate-mv command (multiple query hashes with progress reporting)
- [ ] T094 [US4] Integrate SmartTuningService in generate-mv command to verify eligibility before generating DDL
- [ ] T095 [US4] Add target dataset validation in generate-mv command (verify dataset exists and region matches source tables)
- [ ] T096 [US4] Implement MV metadata storage in BigQuery metadata table (automv_metadata) with fields: mv_name, source_query_hash, signature_hash, created_at, base_tables, status, eligibility_basis, etc.

**Checkpoint**: At this point, User Story 4 should be fully functional - run `bq-automv generate-mv abc123... --dry-run` to see DDL, then without --dry-run to deploy

---

## Phase 7: User Story 5 - Track Optimization Impact (Priority: P3)

**Goal**: Compare query performance and costs before and after materialized view deployment using materialized_view_statistics

**Independent Test**: Capture baseline metrics, run queries after MV deployment, then run impact command and verify report shows before/after comparisons with calculated savings

### Implementation for User Story 5

- [ ] T097 [P] [US5] Create ImpactService in src/bigquery_automv/services/impact.py with materialized_view_statistics querying logic
- [ ] T098 [P] [US5] Implement MV usage detection in ImpactService via INFORMATION_SCHEMA.JOBS materialized_view_statistics (filter where mv.chosen = TRUE)
- [ ] T099 [P] [US5] Implement matching executions query in ImpactService (find jobs with same normalized_literals hash passing same filtering rules as analysis)
- [ ] T100 [P] [US5] Add baseline mode support in ImpactService: none, previous_period, explicit_range with --baseline-mode flag
- [ ] T101 [P] [US5] Implement savings calculation in ImpactService: bytes_saved = max(0, baseline - current), slot_ms_saved = max(0, baseline - current), dollar_saved = bytes_saved / 2^40 * price_per_tib
- [ ] T102 [P] [US5] Add per-MV attribution logic in ImpactService: allocate savings proportionally by bytes scanned reduction or even_split if platform attribution limited
- [ ] T103 [P] [US5] Implement unused MV detection in ImpactService (queries with zero Smart Tuning usage in measurement window)
- [ ] T104 [US5] Implement impact command in src/bigquery_automv/cli/commands/impact.py with --start-date, --end-date flags
- [ ] T105 [US5] Add --baseline-mode flag in impact command with options: none, previous_period, explicit_range
- [ ] T106 [US5] Add --baseline-start and --baseline-end flags in impact command for explicit_range mode
- [ ] T107 [US5] Add --mv-name filter flag in impact command to analyze specific MV
- [ ] T108 [US5] Implement output formatters in impact command for markdown and json formats (--format flag)
- [ ] T109 [US5] Add summary section in impact output: total_bytes_saved, total_slot_ms_saved, total_dollars_saved_on_demand_equiv, total_queries_accelerated, matching_executions_count
- [ ] T110 [US5] Add per-MV breakdown table in impact output with baseline vs current comparison, savings percentage, smart_tuning_usage_count, status
- [ ] T111 [US5] Add unused MVs section in impact output with recommendations for review or drop
- [ ] T112 [US5] Handle no-usage scenario in impact command gracefully (report "no Smart Tuning usage detected" with recommendations)
- [ ] T113 [US5] Add normalization for different period lengths in ImpactService (per-day or per-month rates)

**Checkpoint**: At this point, User Story 5 should be fully functional - run `bq-automv impact --start-date 2024-01-01` and get before/after comparison

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories and production readiness

- [ ] T114 [P] Update quickstart.md with actual command examples and troubleshooting (validate all workflows work end-to-end)
- [ ] T115 [P] Add comprehensive docstrings to all service classes and public methods
- [ ] T116 [P] Add type hints to all functions using Python 3.14+ syntax
- [ ] T117 Run ruff format and lint on all files (`make style && make lint`)
- [ ] T118 Run pre-commit on all files (`pre-commit run --all-files`)
- [ ] T119 [P] Add unit tests for SQL parser service in tests/unit/test_sql_parser.py (test aggregation detection, unsupported patterns, WHERE clause extraction)
- [ ] T120 [P] Add unit tests for Smart Tuning service in tests/unit/test_smart_tuning.py (test eligibility rules, cross-project detection, region mismatch)
- [ ] T121 [P] Add unit tests for MV generator in tests/unit/test_mv_generator.py (test predicate lifting, DDL generation, naming scheme)
- [ ] T122 [P] Add unit tests for Reporter service in tests/unit/test_reporter.py (test cost calculations, savings estimates)
- [ ] T123 [P] Add integration test for analyze command in tests/integration/test_analyzer.py with mocked BigQuery client
- [ ] T124 Add CLI integration test in tests/integration/ that exercises full workflow: analyze → smart-tuning-check → generate-mv → impact
- [ ] T125 Verify all exit codes are correct (0=success, 1=fatal error, 2=partial failure) across all commands
- [ ] T126 Add error message structuring in all commands (stderr with JSON error context including error type, message, context, suggestion)
- [ ] T127 Add --version flag to CLI app to output tool version from pyproject.toml
- [ ] T128 Validate quickstart.md end-to-end example works (all 6 workflows complete successfully)
- [ ] T129 Add mypy type checking configuration and run on all files
- [ ] T130 Performance test: verify analyze command completes in under 2 minutes for 1M jobs (simulated or real data)
- [ ] T131 Add Makefile target for running all tests (`make test`)
- [ ] T132 Add Makefile target for running all quality checks (`make check`)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **User Stories (Phase 3-7)**: All depend on Foundational phase completion
  - US1 (Identify Queries) - Phase 3
  - US3 (Smart Tuning Check) - Phase 4 (can parallelize with US1 after Foundation)
  - US2 (Cost Report) - Phase 5 (depends on US1 for candidate data)
  - US4 (Generate MVs) - Phase 6 (depends on US3 for eligibility, US1 for query data)
  - US5 (Impact Tracking) - Phase 7 (depends on US4 for deployed MVs)
- **Polish (Phase 8)**: Depends on all desired user stories being complete

### User Story Dependencies

```
Foundation (Phase 2) must complete first

After Foundation:
├── US1 (Analyze) - Can proceed independently
├── US3 (Smart Tuning Check) - Can proceed in parallel with US1
├── US2 (Report) - Depends on US1 (needs candidate data from analyze)
├── US4 (Generate MVs) - Depends on US1 (query data) and US3 (eligibility check)
└── US5 (Impact) - Depends on US4 (needs deployed MVs to measure)
```

### Critical Path for MVP (US1 Only)

1. Complete Phase 1: Setup (T001-T008)
2. Complete Phase 2: Foundational (T009-T020)
3. Complete Phase 3: User Story 1 (T021-T036)
4. **STOP and VALIDATE**: Test `bq-automv analyze --start-date 2024-01-01` works end-to-end

### Critical Path for Full Feature

1. Foundation → US1 + US3 (parallel)
2. US1 → US2 (cost reporting for analyzed queries)
3. US1 + US3 → US4 (generate MVs for eligible candidates)
4. US4 → US5 (measure impact of deployed MVs)

### Parallel Opportunities

**Within Setup (Phase 1)**:
- T002, T003, T004, T005, T006, T008 can all run in parallel

**Within Foundation (Phase 2)**:
- T010, T011 can run in parallel (config/logging)
- T012-T017 can all run in parallel (all models)
- T018, T019 can run in parallel (BQ client, SQL parser)

**After Foundation**:
- US1 (Phase 3) and US3 (Phase 4) can proceed in parallel (different developers)

**Within US1**:
- T021-T026 can run in parallel (different service methods)

**Within US3**:
- T038-T046 can run in parallel (different eligibility checks)

**Within US4**:
- T069-T080 can run in parallel (different MV generation aspects)

**Within US5**:
- T097-T103 can run in parallel (different impact analysis components)

---

## Parallel Example: User Story 1 (Analyze)

```bash
# Launch service implementations in parallel:
Task: "Create AnalyzerService in src/bigquery_automv/services/analyzer.py"
Task: "Implement query grouping by normalized_literals in AnalyzerService"
Task: "Implement impact scoring in AnalyzerService.calculate_impure_score()"
Task: "Create filtering logic in AnalyzerService for thresholds"
Task: "Implement time range filtering with creation_time partitioning"
Task: "Add representative query caching in AnalyzerService"

# After service layer complete, proceed to CLI and output:
Task: "Implement analyze command in src/bigquery_automv/cli/commands/analyze.py"
Task: "Add output formatters in analyze command"
# ... remaining US1 tasks sequentially
```

---

## Implementation Strategy

### MVP First (User Story 1 Only) - Recommended Starting Point

1. Complete Phase 1: Setup (T001-T008)
2. Complete Phase 2: Foundational (T009-T020) - CRITICAL
3. Complete Phase 3: User Story 1 (T021-T036)
4. **STOP and VALIDATE**: Run `bq-automv analyze --start-date 2024-01-01 --min-executions 10`
5. Verify output shows ranked query families with impact scores
6. Demo/Deploy MVP for feedback

### Incremental Delivery (Full Feature)

1. Complete Setup + Foundational → Foundation ready
2. Add User Story 1 → Test independently → **MVP Milestone 1: Identify expensive queries**
3. Add User Story 3 → Test independently → **MVP Milestone 2: Check Smart Tuning eligibility**
4. Add User Story 2 → Test independently → **MVP Milestone 3: Generate cost reports**
5. Add User Story 4 → Test independently → **MVP Milestone 4: Auto-generate MVs**
6. Add User Story 5 → Test independently → **MVP Milestone 5: Measure impact**
7. Polish → **Production Ready**

### Parallel Team Strategy (Multiple Developers)

With 2-3 developers after Foundation:

**Sprint 1**:
- All: Complete Setup + Foundational together
- Dev A: User Story 1 (Analyze)
- Dev B: User Story 3 (Smart Tuning Check)

**Sprint 2**:
- Dev A: User Story 2 (Report - integrates with US1)
- Dev B: User Story 4 (Generate MVs - integrates US1 + US3)

**Sprint 3**:
- Dev A: User Story 5 (Impact)
- Dev B: Polish, testing, documentation

---

## Summary Statistics

- **Total Tasks**: 132
- **Setup Phase**: 8 tasks
- **Foundational Phase**: 12 tasks (BLOCKS all stories)
- **User Story 1 (P1)**: 16 tasks
- **User Story 3 (P1)**: 19 tasks
- **User Story 2 (P2)**: 13 tasks
- **User Story 4 (P2)**: 28 tasks
- **User Story 5 (P3)**: 17 tasks
- **Polish Phase**: 19 tasks

**Parallel Opportunities Identified**: 60+ tasks marked [P] can run in parallel within their phases

**Suggested MVP Scope**: Phase 1 + 2 + User Story 1 (36 tasks total) delivers core value: identify expensive query patterns

**Independent Test Criteria**:
- US1: Run analyze, get ranked queries by impact score
- US2: Run report, get cost projections
- US3: Run smart-tuning-check, get eligibility result
- US4: Run generate-mv dry-run, get valid DDL; run without dry-run, MVs deployed
- US5: Run impact, get before/after comparison

**Format Validation**: All tasks follow checklist format: `- [ ] [TaskID] [P?] [Story?] Description with file path`

---

## Notes

- [P] tasks = different files or methods, no dependencies
- [Story] label maps task to specific user story for traceability
- Each user story should be independently completable and testable
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
- All file paths are relative to repository root
- Python 3.14+ with uv package management only (no pip/poetry)
- cyclopts for CLI, sqlglot for SQL parsing, google-cloud-bigquery for BQ client
- Tests are optional per spec - no TDD requirement
- All services use async/await for I/O operations
- Dollar-equivalent impact scoring model with configurable parameters
- BigQuery INFORMATION_SCHEMA.JOBS has 180-day retention limit
- Smart Tuning requires specific SQL patterns - tool validates eligibility
- MVs use predicate lifting algorithm for WHERE clause generalization
- Region mismatch and cross-project references cause hard failures
