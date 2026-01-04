# Feature Specification: BigQuery Query Optimizer with Smart Materialized Views

**Feature Branch**: `001-bq-query-optimizer`
**Created**: 2026-01-03
**Status**: Draft
**Input**: User description: "We need to create a CLI tool which uses the latest Python frameworks for managing CLIs. What we need this tool to do is to analyze the BigQuery information schema and find queries which look suspicious. That's queries that are executed many times. For a specific normalized hash, or the compound score of the number of times that the query is executed and the amount of data or slot time that the query consumed, is above a certain bound. If we find queries that are flagged in this way, we want to be able to generate a report of queries that are our candidates for optimization. Now, queries that are candidates for optimization or red flags, we should be able to produce a report which tells us approximately how much money these queries cost, projected cost, things like that. So like a detailed report in that way. The next thing - The next thing we want to be able to do with these queries is to have a boolean flag on these queries that says if they're candidates for BigQuery's smart tuner. https://docs.cloud.google.com/bigquery/docs/materialized-views-use#smart_tuning BigQuery Smart Tuning is very finicky, and a query has to meet very specific criteria in order to be a candidate for Smart Tuning. We need to read the documentation extremely carefully. If we look at a query and see that it could have theoretically been smart-tuned, then these Boolean flags are set to true. This package will collect queries which can be smart-tuned and automatically generate materialized views. We can then query the information schema, and we should be able to generate some cost optimization artifacts: A predictive one when we create these MVs - An actual one when we realize the savings. These MVs should be considered easy to throw away, easy to recreate. They're not designed to be first-class in any way; they're totally created and only exist for BigQuery's implicit optimization."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Identify Expensive Repetitive Queries (Priority: P1)

As a data platform engineer, I want to scan BigQuery INFORMATION_SCHEMA.JOBS to automatically identify queries that consume significant resources, so that I can prioritize optimization efforts on the most costly query patterns.

**Why this priority**: This is the foundational capability. Without identifying which queries are expensive, no other optimization work can occur. This delivers immediate value by surfacing the most impactful optimization opportunities.

**Independent Test**: Can be fully tested by running the analysis against a project with query history and verifying that the tool correctly identifies queries with high execution frequency and resource consumption, sorted by impact score.

**Acceptance Scenarios**:

1. **Given** a BigQuery project with query history in INFORMATION_SCHEMA.JOBS, **When** I run the analyze command with a time range and threshold parameters, **Then** the tool outputs a list of queries ranked by their optimization impact score (frequency × resource consumption)
2. **Given** a query that executes 1000 times processing 10 TB each time, **When** the analysis runs, **Then** this query appears at the top of the results with a high impact score
3. **Given** the --output flag specifies a file path, **When** the analysis completes, **Then** results are written to the specified file in JSON format with query hashes, execution counts, bytes processed, slot milliseconds, and calculated costs
4. **Given** queries that vary only in literal values (e.g., different dates or IDs), **When** the analysis runs, **Then** these queries are grouped by their normalized hash and aggregated together

---

### User Story 2 - Generate Cost Optimization Report (Priority: P2)

As a data platform manager, I want a detailed report showing the projected cost savings for optimizing specific queries, so that I can justify optimization work to stakeholders and prioritize based on ROI.

**Why this priority**: Cost reporting is essential for business justification. This enables data-driven decisions about which optimizations to pursue first based on financial impact.

**Independent Test**: Can be fully tested by generating a report for identified queries and verifying that it includes accurate cost projections based on BigQuery pricing models, historical spend, and estimated savings.

**Acceptance Scenarios**:

1. **Given** a list of candidate queries from analysis, **When** I run the report command with a query hash or list of hashes, **Then** the tool generates a report containing: total spend, projected spend without optimization, projected savings percentage, and estimated monthly/yearly savings
2. **Given** the --format flag set to "markdown", **When** the report generates, **Then** output is formatted as a readable markdown table with cost breakdowns
3. **Given** the --format flag set to "json", **When** the report generates, **Then** output is machine-readable JSON suitable for integration with other tools
4. **Given** a query with execution history showing 500 executions at $5 each, **When** the report generates, **Then** it shows historical spend of $2,500 and projects future spend if no changes are made
5. **Given** the --pricing flag with custom values, **When** the report calculates costs, **Then** it uses the provided on-demand pricing per TiB instead of defaults

---

### User Story 3 - Detect Smart Tuning Candidates (Priority: P1)

As a BigQuery optimizer, I want to automatically determine which queries are eligible for BigQuery Smart Tuning with materialized views, so that I can focus efforts on queries that can benefit from automatic optimization.

**Why this priority**: Smart Tuning is a powerful zero-code optimization feature. Identifying eligible queries enables significant performance gains without modifying application code, making this a high-impact capability.

**Independent Test**: Can be fully tested by analyzing queries with known SQL patterns and verifying that the tool correctly flags queries that meet Smart Tuning criteria (aggregations, simple filters, no unsupported features) and rejects those that don't (UNION ALL, LEFT JOIN, non-deterministic functions).

**Acceptance Scenarios**:

1. **Given** a query hash from analysis results, **When** I run the smart-tuning-check command, **Then** the tool parses the query SQL and returns a boolean indicating Smart Tuning eligibility with a list of any disqualifying issues
2. **Given** a query with SELECT, WHERE, GROUP BY, and simple aggregations (SUM, COUNT, AVG), **When** the smart tuning check runs, **Then** it returns eligible=true
3. **Given** a query containing UNION ALL, **When** the smart tuning check runs, **Then** it returns eligible=false with reason "Smart tuning is not supported for materialized views with UNION ALL"
4. **Given** a query containing LEFT OUTER JOIN, **When** the smart tuning check runs, **Then** it returns eligible=false with reason "Smart tuning is not supported for materialized views with LEFT OUTER JOIN"
5. **Given** a query using non-deterministic functions like NOW() or RAND(), **When** the smart tuning check runs, **Then** it returns eligible=false with reason "Non-deterministic functions are not supported"
6. **Given** a query referencing a logical view instead of base tables, **When** the smart tuning check runs, **Then** it returns eligible=false with reason "Materialized views that reference logical views do not support smart tuning"
7. **Given** the --verbose flag, **When** the check runs, **Then** it outputs detailed analysis of which Smart Tuning criteria pass and which fail

---

### User Story 4 - Auto-Generate Materialized Views (Priority: P2)

As a data engineer, I want the tool to automatically generate and deploy materialized views for Smart Tuning-eligible queries, so that BigQuery can automatically optimize those queries without application changes.

**Why this priority**: This automates the optimization workflow, reducing manual work and accelerating the path to cost savings. Once MVs exist, Smart Tuning works transparently to improve query performance.

**Independent Test**: Can be fully tested by running the MV generation command against a set of eligible queries and verifying that: (1) materialized view DDL statements are correctly generated, (2) views are deployed to the specified dataset, and (3) the views can be queried successfully.

**Acceptance Scenarios**:

1. **Given** one or more Smart Tuning-eligible query hashes, **When** I run the generate-mv command with --dataset and --project flags, **Then** the tool generates CREATE MATERIALIZED VIEW DDL statements for each query with optimized names (e.g., automv_<hash>)
2. **Given** the --dry-run flag, **When** the command runs, **Then** it outputs the DDL statements without executing them against BigQuery
3. **Given** the --execute flag without --dry-run, **When** the command runs, **Then** it executes the DDL statements to create materialized views in BigQuery and reports success/failure for each
4. **Given** a query with WHERE date > '2021-01-01', **When** generating the MV, **Then** the WHERE clause is preserved in the materialized view definition to enable partition alignment
5. **Given** the --enable-refresh and --refresh-interval-minutes flags, **When** creating MVs, **Then** the generated DDL includes automatic refresh configuration with specified interval
6. **Given** a failure during MV creation (e.g., permissions error), **When** the command runs, **Then** it continues processing remaining queries and reports all failures at the end with specific error messages
7. **Given** existing MVs with the same names, **When** the command runs with --replace, **Then** it drops and recreates the materialized views; without --replace, it skips those queries

---

### User Story 5 - Track Optimization Impact (Priority: P3)

As a platform owner, I want to compare query performance and costs before and after materialized view deployment, so that I can measure the actual impact of optimizations and validate that the MVs are providing value.

**Why this priority**: Measurement is critical for proving ROI and maintaining stakeholder confidence. This closes the loop on optimization work by validating that predicted savings materialize.

**Independent Test**: Can be fully tested by capturing baseline metrics before MV creation, then running the impact command after queries have executed against the MVs, and verifying that the report shows before/after comparisons with calculated savings.

**Acceptance Scenarios**:

1. **Given** previously deployed materialized views, **When** I run the impact command with a time range, **Then** the tool queries INFORMATION_SCHEMA.JOBS to find queries that used the MVs via Smart Tuning and compares their costs to historical baselines
2. **Given** materialized view usage detected in job statistics, **When** the impact analysis runs, **Then** the report shows: number of queries accelerated by MVs, total slot milliseconds saved, total bytes processed saved, and dollar amount saved
3. **Given** the --baseline flag with a previous timestamp, **When** calculating impact, **Then** the tool compares current period metrics to the baseline period to show delta
4. **Given** no Smart Tuning usage detected for a materialized view, **When** the impact report generates, **Then** it flags that MV as "unused" with a recommendation to review or drop it
5. **Given** the --format markdown flag, **When** the report generates, **Then** it produces a summary table showing each MV with usage statistics and savings

---

### Edge Cases

- What happens when INFORMATION_SCHEMA.JOBS contains queries with NULL values for total_bytes_billed or total_slot_ms (e.g., row-level security queries)?
- How does the system handle queries that reference tables that no longer exist when attempting to create materialized views?
- What happens when a user lacks necessary permissions (bigquery.tables.create) to create materialized views in the target dataset?
- How does the tool handle queries with extremely long SQL text that exceeds string length limits?
- What happens when the normalized hash matches queries that are semantically different due to schema changes in underlying tables?
- How does the system handle materialized view creation when the base table has partitioning that doesn't align with the query structure?
- **What happens when the target dataset is in a different region than the source tables?** → MV creation MUST fail with explicit error `region_mismatch`.
- **What happens when concurrent executions attempt to create the same materialized view?** → Tool handles "already exists" races by refetching MV definition and comparing signature_hash to decide NO-OP vs conflict.
- How does the system handle queries that use UDFs (user-defined functions) which are not supported in materialized views?
- What happens when a materialized view's base tables are dropped or schema changes after MV creation? → MV marked INVALID, tool attempts regeneration if still high-impact.
- How does the cost calculation handle queries with mixed pricing models (on-demand vs flat-rate reservations)? → Dollar estimates reflect on-demand pricing only; bytes/slot-ms deltas reported for evaluation.
- **What happens when queries reference tables in a different project?** → Treated as NOT ELIGIBLE for MV generation (cross-project references not supported by Smart Tuning).
- **What happens when a query is only eligible under preview features?** → Reported as NOT ELIGIBLE by default unless `--enable-preview-eligibility` flag is set.
- **What happens when MV name already exists with different signature?** → Default: report "name_conflict" and do not modify; with `--replace`: drop and recreate after warning.
- **What happens when predicate generalization fails?** → MV generation fails with reason "predicate_generalization_failed".
- **What happens when family queries have different GROUP BY columns?** → MV generation fails with reason "group_by_mismatch".
- **What happens when queries in a family have different join structures?** → "Mixed family" detection skips MV generation (marked as analysis-only).

## Requirements *(mandatory)*

### Functional Requirements

#### Query Analysis

- **FR-001**: The tool MUST query INFORMATION_SCHEMA.JOBS (or JOBS_BY_PROJECT) to extract query history including job_id, query, query_info.query_hashes.normalized_literals, total_bytes_billed, total_bytes_processed, total_slot_ms, creation_time, end_time, user_email, state, statement_type, referenced_tables, and reservation_id
- **FR-002**: The tool MUST accept time range filters (--start-date and --end-date) to limit analysis scope
- **FR-003**: The tool MUST accept optional thresholds (--min-executions, --min-bytes, --min-slot-ms) to filter results
- **FR-004**: The tool MUST group queries by normalized_literals hash to aggregate queries that differ only in literal values
- **FR-005**: The tool MUST calculate an impact score for each query hash according to the [Impact Scoring Model](#impact-scoring-model) section, which defines a dollar-equivalent ranking heuristic with configurable parameters
- **FR-006**: The tool MUST exclude SCRIPT statement type jobs from aggregation to avoid double-counting metrics
- **FR-007**: The tool MUST exclude NULL total_bytes_billed queries with a warning annotation (queries over tables with row-level security)
- **FR-008**: The tool MUST support output formats: JSON, CSV, and human-readable table
- **FR-009**: The tool MUST cache query text for a representative sample of each hash to enable SQL analysis

#### Smart Tuning Eligibility Detection

- **FR-010**: The tool MUST parse query SQL to determine eligibility for BigQuery Smart Tuning based on documented criteria and the specified rulebook version
- **FR-011**: The tool MUST flag as ineligible queries that reference tables in a different project (cross-project references) because Smart Tuning will not apply to such queries
- **FR-012**: The tool MUST verify that all referenced tables reside in the same region as the target dataset; MV generation MUST fail with reason `region_mismatch` if regions differ
- **FR-013**: The tool MUST flag as ineligible queries containing: UNION ALL, LEFT OUTER JOIN, RIGHT/FULL OUTER JOIN, self-joins, window functions, ARRAY subqueries, or non-deterministic functions (RAND, CURRENT_DATE, SESSION_USER, CURRENT_TIME). Note: UNION ALL and LEFT OUTER JOIN may be eligible under preview features if `--enable-preview-eligibility` is set.
- **FR-014**: The tool MUST flag as ineligible queries using UDFs (user-defined functions)
- **FR-015**: The tool MUST flag as ineligible queries using unsupported aggregate patterns (HAVING on aggregates, computed aggregates like COUNT(*) / 10)
- **FR-016**: The tool MUST flag as ineligible queries that reference logical views instead of base tables (smart tuning not supported)
- **FR-017**: The tool MUST verify the query uses only supported aggregation functions: ANY_VALUE, APPROX_COUNT_DISTINCT, ARRAY_AGG, AVG, BIT_AND, BIT_OR, BIT_XOR, COUNT, COUNTIF, HLL_COUNT.INIT, LOGICAL_AND, LOGICAL_OR, MAX, MIN, MAX_BY, MIN_BY, SUM
- **FR-018**: The tool MUST return a boolean eligible flag, an `eligibility_basis` field ("stable" or "preview"), and disqualification reasons when not eligible
- **FR-019**: The tool MUST validate that the query follows the materialized view SQL pattern: [WITH cte[, ...]] SELECT [{ALL | DISTINCT}] expression [[AS] alias] [, ...] FROM from_item [, ...] [WHERE bool_expression] [GROUP BY expression [, ...]]
- **FR-020**: The tool MUST include the `rulebook_version` in all eligibility check results to support reproducibility and auditability

#### Materialized View Generation

- **FR-021**: The tool MUST generate CREATE MATERIALIZED VIEW DDL statements for eligible queries
- **FR-022**: The tool MUST generate unique materialized view names using the scheme: `{prefix}_{family_hash_short}_{signature_hash_short}` for collision resistance and idempotency
- **FR-023**: The tool MUST verify that all referenced tables are in the same region as the target dataset before generating MV DDL
- **FR-024**: The tool MUST preserve the query's WHERE clause in the materialized view definition, applying the conservative predicate generalization algorithm defined in the [Materialized View Construction Contract](#materialized-view-construction-contract)
- **FR-025**: The tool MUST add enable_refresh = true and refresh_interval_minutes options to generated MVs
- **FR-026**: The tool MUST support a --dry-run flag to output DDL without executing
- **FR-027**: The tool MUST support an --execute flag to deploy MVs to BigQuery
- **FR-028**: The tool MUST support a --replace flag to drop and recreate existing MVs with explicit confirmation
- **FR-029**: The tool MUST support --dataset and --project flags to specify target location
- **FR-030**: The tool MUST handle batch operations (multiple query hashes) with continue-on-error behavior
- **FR-031**: The tool MUST store metadata linking each MV to its source query hash, signature hash, creation timestamp, and rulebook_version
- **FR-032**: The tool MUST validate that the target dataset exists and is in the same region as source tables before attempting MV creation

#### Cost Calculation & Reporting

- **FR-033**: The tool MUST calculate costs using BigQuery on-demand pricing: $6.25 per TiB for query analysis (default, configurable via --price-per-tib)
- **FR-034**: The tool MUST accept custom pricing via --price-per-tib, --slot-weight, and --slot-ms-per-tib-equivalent flags for the impact scoring model
- **FR-035**: The tool MUST calculate total historical spend as: SUM(total_bytes_billed) / (1024^4) × price_per_tib for each query family
- **FR-036**: The tool MUST calculate projected monthly spend as: (historical spend / days_analyzed) × 30
- **FR-037**: The tool MUST estimate potential savings for Smart Tuning candidates as percentage of bytes processed that could be served from MV cache (estimate: 70-90% for eligible queries)
- **FR-038**: The tool MUST generate reports in multiple formats: markdown, JSON, CSV
- **FR-039**: The tool MUST include in reports: query hash, execution count, bytes_billed_total, slot_ms_total, dollar_cost_est_on_demand, impact_score, impact_model_version, rulebook_version, historical cost, projected monthly cost, estimated savings percentage, estimated dollar savings, Smart Tuning eligible flag, eligibility_basis

#### Impact Tracking

- **FR-040**: The tool MUST query INFORMATION_SCHEMA.JOBS with materialized_view_statistics to detect Smart Tuning usage
- **FR-041**: The tool MUST identify which queries were rewritten to use specific materialized views
- **FR-042**: The tool MUST support baseline comparison using --baseline-mode flag with options: none, previous_period, explicit_range
- **FR-043**: The tool MUST define "matching executions" as jobs with the same normalized_literals hash that pass the same filtering rules as analysis
- **FR-044**: The tool MUST compare query metrics (bytes processed, slot ms) before and after MV deployment based on the specified baseline mode
- **FR-045**: The tool MUST calculate actual savings as max(0, baseline - current) aggregated over matching jobs, with dollar savings as on-demand-equivalent
- **FR-046**: The tool MUST report on unused materialized views (no Smart Tuning usage detected) with recommendations for review or cleanup
- **FR-047**: The tool MUST include attribution_method in impact reports indicating how savings were allocated when multiple MVs are involved

#### CLI Interface

- **FR-048**: The tool MUST provide a command-line interface using a modern Python CLI framework (cyclopts)
- **FR-049**: The tool MUST support commands: analyze, report, smart-tuning-check, generate-mv, impact
- **FR-050**: The tool MUST provide --help documentation for all commands and flags
- **FR-051**: The tool MUST support configuration via environment variables for: GOOGLE_APPLICATION_CREDENTIALS, default project ID, default dataset, default region
- **FR-052**: The tool MUST support global privacy flags: --include-user-email (default false), --include-query-text (default false)
- **FR-053**: The tool MUST support --enable-preview-eligibility flag (default false) for opt-in to preview feature eligibility
- **FR-054**: The tool MUST validate required flags and provide clear error messages for missing required parameters, including region mismatch validation
- **FR-055**: The tool MUST support verbose output mode (--verbose, -v) for detailed execution information
- **FR-056**: The tool MUST use appropriate exit codes: 0 for success, 1 for fatal errors, 2 for partial failures

### Key Entities

- **Query Candidate**: Represents a unique query pattern identified by normalized hash. Attributes: query_hash, representative_query, execution_count, bytes_billed_total, slot_ms_total, impact_score, dollar_cost_est_on_demand, impact_model_version, rulebook_version, first_seen, last_seen, referenced_tables, statement_type, Smart Tuning eligible flag, eligibility_basis ("stable" or "preview"), disqualification reasons
- **Materialized View Artifact**: Represents a created materialized view. Attributes: mv_name (with signature_hash component), source_query_hash (family_hash), signature_hash, mv_region, project, dataset, DDL definition, base tables, refresh_interval, created_at, created_by_tool_version, rulebook_version, synthesis_version, synthesis_warnings, eligibility_basis, status (PROPOSED/CREATED/ACTIVE/STALE/INVALID/DEPRECATED/DROPPED), usage statistics
- **Cost Analysis Result**: Represents financial analysis of a query candidate. Attributes: query hash, bytes_billed_total, slot_ms_total, historical spend (on-demand-equivalent), projected monthly spend, projected yearly spend, estimated savings percentage, estimated dollar savings, price_per_tib used, confidence level
- **Impact Report**: Represents before/after comparison of optimization. Attributes: report_id, period_start, period_end, baseline_mode, baseline_period_start, baseline_period_end, mv_impacts, total_bytes_saved, total_slot_ms_saved, total_dollars_saved_on_demand_equiv, matching_executions_count, unused_mvs
- **Smart Tuning Check Result**: Represents eligibility analysis. Attributes: query_hash, eligible (boolean), eligibility_basis, disqualification_reasons, unsupported_features, aggregation functions, join types, has_ctes, subquery_types, cross_project_references, region_mismatch_flag

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Users can analyze up to 180 days of query history (INFORMATION_SCHEMA.JOBS retention limit) in under 2 minutes for projects with up to 1 million query jobs
- **SC-002**: Smart Tuning eligibility detection achieves 95% accuracy compared to manual verification against BigQuery documentation, excluding preview-dependent patterns unless `--enable-preview-eligibility` is set
- **SC-003**: Materialized view generation produces syntactically valid DDL that executes successfully on BigQuery in 99% of eligible cases (excluding cases with region mismatch, cross-project references, or mixed families)
- **SC-004**: Cost projections (on-demand-equivalent) are within 20% of actual spend when compared to historical billing data over the same period. Projection accuracy evaluated using on-demand pricing model; reservation-based costs require manual evaluation.
- **SC-005**: The tool correctly identifies 90% of queries that would benefit from Smart Tuning (avoiding false negatives), excluding cross-project reference queries which are correctly identified as not eligible
- **SC-006**: Generated materialized views are used by Smart Tuning in at least 70% of matching query executions within 7 days of deployment. "Matching executions" are defined as jobs with the same normalized_literals hash passing the same filtering rules.
- **SC-007**: Impact reports accurately reflect materialized view usage as verified by INFORMATION_SCHEMA.JOBS materialized_view_statistics. Total savings is reliable; per-MV allocation is best-effort when platform attribution is limited.
- **SC-008**: Users can successfully complete the full workflow (analyze → check → generate → impact) in under 15 minutes for a typical project with 10-20 optimization candidates
- **SC-009**: The tool handles error conditions gracefully (permissions errors, missing tables, region mismatch, cross-project references) with clear error messages 100% of the time
- **SC-010**: Queries flagged as high-impact (top 10% by impact_score per the [Impact Scoring Model](#impact-scoring-model)) when optimized with materialized views show measurable performance improvement (reduced bytes processed) in 80% of cases

## System Scope and Constraints

### Tenancy Boundaries

The system operates under the following hard constraints:

- **Single Project**: The tool operates on a SINGLE GCP PROJECT per run. All analysis and MV creation operations are scoped to one project.

- **Single Region**: The tool operates on a SINGLE BIGQUERY REGION per run. BigQuery regions are isolated, and INFORMATION_SCHEMA.JOBS is region-specific.

### Materialized View Creation Boundaries

- **Co-location Requirement**: Any generated Materialized View (MV) MUST be created in the SAME REGION as the source table(s) it references. This is a hard requirement because Smart Tuning requires MVs to be co-located with their base tables for automatic query rewriting.

- **Target Dataset Region**: MV creation MUST be restricted to a target dataset that resides in the same region as the source tables. If the target dataset is in a different region, MV generation MUST fail with an explicit error.

### Cross-Project References

- **Cross-Project Tables**: Queries that reference tables in other projects (different project_id) MUST be treated as NOT ELIGIBLE for Smart Tuning. The tool MUST NOT produce MV DDL recommendations for such queries because Smart Tuning will not apply to cross-project references.

- **Project Boundary Enforcement**: The tool MUST verify all referenced tables belong to the configured project before generating MV recommendations.

---

## BigQuery Preview Features Policy

### Definitions

- **Preview Feature**: Refers to BigQuery capabilities or Smart Tuning/MV eligibility behaviors that are explicitly labeled as Preview by Google. These features may require opt-in, may change without notice, and may not be enabled in all environments.

### Default Behavior

- **Conservative Assumption**: The system MUST assume preview features are NOT enabled unless explicitly opted in.

- **Eligibility Checks**: Eligibility checks MUST be conservative by default. If a query is only eligible under preview-only rules, it MUST be reported as NOT ELIGIBLE unless the opt-in flag is set.

### Optional Opt-In Mode

The tool provides the following CLI flag:

```
--enable-preview-eligibility (default: false)
```

When `--enable-preview-eligibility` is `true`:
- Eligibility checks MAY include preview-allowed patterns as enumerated in the rulebook version.
- Output MUST clearly label any recommendations that depend on preview eligibility with `eligibility_basis="preview"`.

### Rulebook Versioning

- The spec defines a **Smart Tuning Rulebook Version** field in outputs (`rulebook_version`).
- Updates to eligibility rules MUST be versioned and release-noted.
- Output includes `rulebook_version` to allow reproducibility and auditability.

### Uncertainty Acknowledgment

"Google preview features may not be smart tunable in a given environment; preview-dependent recommendations are best-effort and require verification."

---

## Impact Scoring Model

### Overview

The impact_score provides a deterministic, interpretable ranking heuristic for query families. It prioritizes cost savings under on-demand pricing while distinguishing CPU-heavy queries when bytes are similar.

### Model Selection Rationale

**Choice: Dollar-Equivalent Model** (chosen over z-score blend)

The dollar-equivalent model was selected over the alternative z-score blend approach for the following reasons:

1. **Interpretability**: Scores are expressed in dollar-equivalent terms, making them immediately meaningful to stakeholders. A score of "$750" is more intuitive than a z-score of "2.3".

2. **Actionability**: Dollar values directly inform ROI decisions. Teams can compare impact scores against MV storage costs (~$0.02/GB/month) to prioritize optimizations.

3. **Stability**: The dollar-equivalent model is less sensitive to distribution changes in the query population. Z-scores require recomputing mean/stddev across the entire dataset, which can fluctuate as query patterns change.

4. **Transparency**: The relationship between input metrics (bytes, slot-ms) and the final score is transparent and auditable. Z-score blends introduce statistical abstraction that obscures the contribution of each component.

5. **Future-Proofing**: The model_version field allows for algorithm evolution while maintaining historical comparability. If a future analysis shows slot_ms are under- or over-weighted, the conversion factors can be adjusted in a new model version.

**Trade-offs Accepted**:
- The dollar-equivalent model requires defining conversion factors (slot_ms_per_tib_equivalent) that are somewhat arbitrary. The default of 3.6e9 slot-ms/TiB was chosen to keep the slot component secondary (25% weight) while still distinguishing CPU-heavy queries.
- Dollar estimates are on-demand-equivalent and may not match actual billing for projects using reservations. This is explicitly documented as a limitation.

### Aggregation Semantics

For each query family F (grouped by `normalized_literals` hash):

- `exec_count(F)` = number of jobs in F in the analysis window
- `bytes_billed_total(F)` = SUM(total_bytes_billed) across jobs in F
- `slot_ms_total(F)` = SUM(total_slot_ms) across jobs in F (if available)

**Note**: Totals are already aggregated over executions. DO NOT multiply by exec_count again.

### Primary Scoring Objective

We want a stable ranking that primarily correlates to dollar savings under on-demand pricing, with secondary weight for CPU-intensive queries.

### Scoring Formula

The tool uses a **dollar-equivalent model** with explicit unit conversion:

```python
# Convert bytes to TiB
tib_billed = bytes_billed_total(F) / (2 ** 40)

# Convert slot-ms to TiB-equivalent
slot_tib_equiv = slot_ms_total(F) / slot_ms_per_tib_equivalent

# Dollar cost estimates
dollar_cost_on_demand = tib_billed * price_per_tib
dollar_cost_slot_equiv = slot_tib_equiv * price_per_tib

# Final impact score
impact_score(F) = dollar_cost_on_demand + slot_weight * dollar_cost_slot_equiv
```

### Default Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `price_per_tib` | 6.25 USD | BigQuery on-demand price per TiB (configurable) |
| `slot_weight` | 0.25 (dimensionless) | Weight for slot component (configurable) |
| `slot_ms_per_tib_equivalent` | 3.6e9 slot-ms/TiB | Conversion factor (configurable) |

### Interpretation

- The bytes-based term is the PRIMARY driver of the score.
- The slot term breaks ties and elevates CPU-heavy queries when bytes are similar.
- This is a **ranking heuristic**, not a billing statement.

### Required Outputs

For each family, the tool MUST output:
- `bytes_billed_total`: Sum of bytes billed across all executions
- `slot_ms_total`: Sum of slot milliseconds across all executions
- `exec_count`: Number of executions in the window
- `dollar_cost_est_on_demand`: Estimated on-demand cost from bytes
- `impact_score`: Final ranking score
- `impact_model_version`: Version string for the scoring model (allows future changes)

### Model Versioning

The `impact_model_version` field (e.g., "v1.0") allows scoring changes without breaking comparability. Historical scores retain their model version label.

---

## Materialized View Construction Contract

### Overview

This section defines the contract for generating valid, eligible, deterministic materialized views from query families.

### Inputs

For each eligible family F:
- Collect a deterministic sample of queries S from the family for MV synthesis and validation.

**Representative Selection** (deterministic):
1. Choose top K queries by `bytes_billed`, descending
2. Tie-break: most recent `creation_time`
3. Tie-break: `job_id` (or stable hash)

Default `K = 20` (configurable via CLI `--sample-size-k`).

### Canonicalization

Parse each query to AST. If parse fails, family is "analysis-only" (no MV generation) with reason "parse_failed".

**Normalization steps**:
- Identifier normalization (case, quoting)
- Literal placeholder substitution
- Predicate ordering for commutative boolean expressions where possible

### "Covers All Query Rows" Invariant

**Core Requirement**: For a family F, the MV WHERE clause MUST NOT exclude any base-table rows that are required by ANY query in S.

**Formal Definition**:
- For each query Qi in S, Qi's predicate must imply MV_predicate OR MV_predicate is TRUE.
- The MV WHERE clause is a logical weakening of per-query predicates.
- If the tool cannot construct an MV predicate provably non-restrictive, MV generation MUST FAIL with reason "predicate_generalization_failed".

### Predicate Lifting / Generalization Algorithm

**Conservative Generalization Strategy**:

For each predicate atom type:

| Predicate Type | Generalization Rule |
|----------------|---------------------|
| Equality: `col = literal` | If literals vary across queries, DROP predicate from MV WHERE. Include only if constant across all S. |
| IN list: `col IN (...)` | If lists differ, DROP predicate (do not union lists). Include only if identical across all S. |
| Range: `BETWEEN`, `>=`, `<=` | If bounds differ, DROP predicate. Include only if identical across all S. |
| IS NULL / IS NOT NULL | Keep only if identical across all S. |
| Non-deterministic functions / UDFs | Disqualifying (already handled in eligibility check). |

**Net Effect**: MV WHERE becomes the intersection of predicates identical across all sampled queries. Everything else is filtered at query runtime.

This conservative approach satisfies "covers all query rows" because it can only be less restrictive than any individual query.

### Projection and Aggregation Contract

- MV SELECT list MUST include all grouping columns and aggregate expressions such that any query in the family can be satisfied.
- If family queries differ in selected columns/aggregates, MV MUST include superset only if allowed by MV rules; otherwise, fail with "projection_mismatch".
- GROUP BY columns MUST be identical across family sample S (after canonicalization). If not identical, fail with "group_by_mismatch".

### Join Handling

**Policy (Option A - Conservative)**:
- MV synthesis supports ONLY single-base-table queries.
- If a query includes ANY JOIN, it is NOT ELIGIBLE for MV generation by this tool.
- Such queries may still be "smart tuning eligible" if Google supports them directly, but we do not synthesize MVs for them.

**Rationale**: BigQuery MV eligibility has complex constraints for joins. Supporting joins increases implementation risk significantly. Future extensions may relax this constraint.

### Determinism Requirements

Given identical inputs (project, region, time window, flags, rulebook_version), the MV DDL output MUST be deterministic.

### Validation Step (Pre-DDL)

Before outputting DDL:
1. Re-parse the generated MV query.
2. Validate against the eligibility ruleset.
3. Emit a `synthesis_audit` structure including:
   - K value
   - Sample job IDs or stable hashes
   - Dropped predicates summary
   - Reasons for any failures

### Output Labeling

Each MV recommendation MUST include:
- `family_hash`: normalized_literals hash of the source family
- `mv_sql`: Full CREATE MATERIALIZED VIEW statement
- `mv_dataset`: Target dataset ID
- `mv_region`: Explicit region (must match source tables)
- `rulebook_version`: Eligibility rulebook version
- `synthesis_version`: MV synthesis algorithm version
- `synthesis_warnings`: List of warnings (e.g., "dropped_varying_predicates")
- `eligibility_basis`: "stable" or "preview"

---

## MV Naming and Idempotent Application

### Naming Scheme

MV names MUST be deterministic and collision-resistant:

```
mv_name = "{prefix}_{family_hash_short}_{signature_hash_short}"
```

Where:
- `prefix`: Configurable (default: `automv_`)
- `family_hash_short`: First 8-12 characters of normalized_literals hash
- `signature_hash_short`: First 8-12 characters of stable hash of canonical MV SQL

This prevents collisions across different MV definitions for the same family and supports idempotency.

### Metadata Tagging

MV DDL SHOULD include labels (if supported) or store a companion metadata record containing:
- `family_hash`
- `signature_hash`
- `created_by_tool_version`
- `created_at` (timestamp)
- `rulebook_version`

### Idempotency Semantics

**Default Mode: "safe apply"**
- If MV name does not exist: CREATE
- If MV name exists and `signature_hash` matches: NO-OP (report "already_exists")
- If MV name exists but `signature_hash` differs: Do NOT modify; report "name_conflict" and recommend new name or `--replace` flag

**Replace Mode: `--replace` flag**
- If exists and differs: DROP and recreate (or CREATE OR REPLACE if allowed)
- MUST log prior signature and warn about risk
- MUST require explicit confirmation (no implicit replacement)

### Concurrency

- Tool MUST assume concurrent writers are possible.
- On create, MUST handle "already exists" races by:
  1. Re-fetching MV definition
  2. Comparing `signature_hash`
  3. Deciding NO-OP vs conflict based on comparison
- Concurrency conflicts MUST NOT corrupt outputs; failures must be explicit.

### CLI Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--mv-prefix` | STRING | `automv_` | Prefix for generated MV names |
| `--replace` | BOOL | `false` | Enable replace mode |
| `--name-scheme-version` | STRING | `v1` | Naming scheme version (for future changes) |

---

## Impact Measurement Methodology

### Measurement Window

- Report impact over a specified time window [T_start, T_end]
- Default window: Last N days (e.g., 30), configurable via CLI

### Baseline Definition

**Baseline Modes**:

| Mode | Description |
|------|-------------|
| `none` | No baseline comparison; report current period only |
| `previous_period` | Baseline is immediately preceding period of equal duration |
| `explicit_range` | User provides explicit baseline start/end timestamps |

CLI flags: `--baseline-mode {none,previous_period,explicit_range}`, `--baseline-start`, `--baseline-end`

### "Matching Executions" Definition

A job "matches" a family if:
- Its `normalized_literals` hash equals the family hash
- It passes the same filtering rules as analysis (excluded statement types, errors, etc.)

The "matching executions" denominator for SC-006 is the count of matching jobs in the measurement window.

### MV Usage Attribution

**Detection**:
- A job is considered "MV-used" if `INFORMATION_SCHEMA.JOBS` reports non-empty `materialized_view_statistics` referencing the MV.

**Multiple MVs**:
- If multiple MVs are listed, attribute usage to each MV listed.
- For savings attribution, avoid double counting:
  - **Primary attribution**: Allocate savings proportionally by bytes scanned reduction if available
  - **Fallback**: Split evenly if per-MV attribution not available

**Best-effort Note**: If the platform does not provide sufficient attribution detail, total savings is reliable but per-MV allocation may be approximate.

### Savings Computation

Metrics:
- `bytes_saved` = max(0, bytes_billed_baseline - bytes_billed_actual), aggregated over matching jobs
- `slot_ms_saved` = max(0, slot_ms_baseline - slot_ms_actual), aggregated
- `dollar_saved_on_demand_equiv` = bytes_saved / 2^40 * price_per_tib

### Normalization

When comparing periods of different lengths, normalize to per-day or per-month rates.

---

## MV Lifecycle and Governance

### Lifecycle States

| State | Description |
|-------|-------------|
| `PROPOSED` | MV recommended but not created |
| `CREATED` | MV exists in BigQuery |
| `ACTIVE` | MV is being used by Smart Tuning above threshold |
| `STALE` | MV exists but usage below threshold for N days |
| `INVALID` | MV exists but is unusable (base table/schema change, errors) |
| `DEPRECATED` | MV scheduled for removal |
| `DROPPED` | MV removed |

### Cleanup Policy

**Default**: The system MUST NOT auto-drop MVs by default.

**Recommendations**:
- If STALE for > 30 days (default, configurable), recommend drop.

**Auto-cleanup Mode**:
- `--enable-auto-cleanup` flag (default: false)
- MUST support `--dry-run` mode for preview
- When enabled, respects stale threshold before dropping

### Invalidation Detection

Detection signals:
- MV query compilation errors on refresh/usage => INVALID
- Base table dropped => INVALID
- Region mismatch => INVALID
- Schema drift: referenced columns no longer exist or type incompatible => INVALID

Detection via:
- `INFORMATION_SCHEMA.TABLES/COLUMNS` checks for referenced objects
- Re-parse MV definition and verify referenced columns exist

### Drift Management

- On INVALID, tool SHOULD attempt to regenerate replacement MV (new signature/name) if family is still high-impact and eligible.
- Tool MUST keep audit logs/metadata linking replacement to prior MV signature.

### Refresh Interval

- Default refresh interval: 60 minutes (configurable)
- Upper/lower bounds enforced if specified
- Refresh cost NOT included in projected savings (noted as risk/unknown)

---

## Non-Functional Requirements

### Determinism

- Given identical inputs (project, region, time window, rulebook_version, flags), outputs MUST be deterministic.
- This includes impact scores, MV DDL, and recommendations.

### Reliability and Partial Failure Semantics

**Error Classification**:
- **Fatal errors**: No results produced (e.g., auth failure, invalid parameters)
- **Partial results**: Some families processed, others skipped

**Partial Results**:
- MUST include explicit reasons per skipped family (permission denied, parse failure, mixed family, etc.)
- Exit codes: 0 (success), 1 (fatal error), 2 (partial failure)

### Performance Budgets

- Scale: Up to 1M jobs per window within target runtime
- Pagination strategy: Use page tokens for INFORMATION_SCHEMA.JOBS queries
- API rate limiting: Maximum requests per minute (best-effort)
- Degrade behavior: If quota exceeded, stop early and report partial results

### Cost Safety

- Tool queries against BigQuery metadata MUST minimize billed data (INFORMATION_SCHEMA only).
- Tool MUST NOT run user query texts against base tables during analysis unless explicitly enabled (future `--deep-validate` flag).

### Security and Privacy

**Sensitive Data Handling**:
- Query text and user identifiers are sensitive.
- By default, output reports MUST redact literal values.
- Default: disable user_email output unless explicitly enabled.

**Privacy Flags**:
| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--include-user-email` | BOOL | `false` | Include user_email in output |
| `--include-query-text` | BOOL | `false` | Include full query text in output |

**Logging**:
- Logs MUST avoid storing full query text unless explicitly enabled.
- Structured logs for debugging (no sensitive data by default).

### Observability

Tool MUST emit structured logs/metrics for:
- `families_analyzed`: Number of families processed
- `families_eligible`: Number eligible for Smart Tuning
- `mvs_generated`: Number of MVs created
- `skips_with_reasons`: Map of skip reason -> count
- `runtime_per_phase`: Execution time per analysis phase

---

## Assumptions

1. **Pricing Model**: Cost calculations use on-demand BigQuery pricing ($6.25/TiB as of 2025). Dollar estimates reflect on-demand-equivalent pricing. Projects using reservations/slot commitments will have different actual billing; the tool reports bytes and slot-ms deltas to support evaluation. Reservation-based pricing will be addressed in a future spec revision.

2. **Query History Access**: The user running the tool has the BigQuery Resource Viewer role (bigquery.jobs.listAll permission) to query INFORMATION_SCHEMA.JOBS.

3. **MV Creation Permissions**: The user has bigquery.tables.create permission in the target dataset to create materialized views.

4. **Smart Tuning Effectiveness**: The tool assumes that eligible queries will benefit from Smart Tuning with estimated 70-90% reduction in bytes processed. Actual results vary based on data distribution and query patterns.

5. **Materialized View Lifecycle**: MVs are considered ephemeral and can be dropped/recreated without concern for downstream dependencies. The tool does not check for existing dependencies.

6. **Normalization Stability**: The normalized_literals hash remains stable for queries that are semantically equivalent. Schema changes or implicit column references (SELECT *) may change the hash.

7. **Information Schema Freshness**: INFORMATION_SCHEMA.JOBS data is near real-time but may have a delay of up to a few minutes for recent job completion.

8. **Query Text Availability**: The JOBS_BY_PROJECT view includes the query column; some regions or configurations may have restrictions on query text access.

9. **Refresh Intervals**: Default MV refresh interval is 60 minutes. Users may need to adjust based on data freshness requirements.

10. **Single Region Operation**: The tool operates on a single BigQuery region per run. Multi-region projects require separate analysis per region.

11. **Preview Feature Availability**: Preview features may not be available in all environments. The tool is conservative by default and requires opt-in for preview-dependent eligibility.
