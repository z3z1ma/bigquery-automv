# Data Model: BigQuery Query Optimizer

**Feature**: `001-bq-query-optimizer`
**Date**: 2026-01-03

---

## Overview

This document defines the core entities and their relationships for the BigQuery materialized view automation system. The system analyzes query history, identifies optimization candidates, generates materialized views, and tracks impact.

---

## Entity Definitions

### 1. QueryCandidate

Represents a unique query pattern identified by normalized hash. This is the core entity for analysis.

```python
@dataclass
class QueryCandidate:
    """A unique query pattern identified from INFORMATION_SCHEMA.JOBS analysis."""

    # Identity
    query_hash: str  # normalized_literals hash from BigQuery
    representative_query: str  # Sample SQL text for this pattern

    # Execution metrics (aggregated across all jobs in family)
    execution_count: int
    bytes_billed_total: int  # SUM(total_bytes_billed) across jobs in family
    total_bytes_processed: int
    slot_ms_total: int  # SUM(total_slot_ms) across jobs in family
    impact_score: float  # Dollar-equivalent impact score per Impact Scoring Model
    dollar_cost_est_on_demand: float  # Estimated on-demand cost from bytes

    # Scoring metadata
    impact_model_version: str  # Version string for scoring model (e.g., "v1.0")
    rulebook_version: str  # Smart Tuning eligibility rulebook version

    # Time range
    first_seen: datetime
    last_seen: datetime

    # Query characteristics
    referenced_tables: list[TableReference]
    statement_type: str  # SELECT, INSERT, etc.
    has_aggregations: bool
    aggregation_functions: list[str]  # ['COUNT', 'SUM', ...]
    join_types: list[str]  # ['INNER', 'LEFT', etc.] or empty
    has_ctes: bool
    has_unsupported_patterns: bool  # UNION ALL, window functions, etc.

    # Smart Tuning eligibility
    smart_tuning_eligible: bool
    eligibility_basis: str  # "stable" or "preview"
    smart_tuning_reasons: list[str]  # Disqualification reasons if not eligible

    # Users (for notification/stakeholder tracking)
    impacted_users: list[str]  # user_email from jobs (only if --include-user-email)
```

**Derived Calculations** (per Impact Scoring Model):
```python
# Impact scoring uses dollar-equivalent model:
tib_billed = bytes_billed_total / (2 ** 40)
slot_tib_equiv = slot_ms_total / slot_ms_per_tib_equivalent
dollar_cost_on_demand = tib_billed * price_per_tib
dollar_cost_slot_equiv = slot_tib_equiv * price_per_tib
impact_score = dollar_cost_on_demand + slot_weight * dollar_cost_slot_equiv
```
- `has_aggregations` = True if `aggregation_functions` is non-empty
- `has_unsupported_patterns` = True if contains UNION ALL, LEFT JOIN, window functions, UDFs

**Validation Rules**:
- `query_hash` must be non-empty
- `execution_count` >= 1
- `total_bytes_billed` can be NULL (row-level security queries)
- `referenced_tables` must have at least 1 table

---

### 2. TableReference

Represents a BigQuery table referenced by a query.

```python
@dataclass
class TableReference:
    """A BigQuery table referenced in a query."""

    project_id: str
    dataset_id: str
    table_id: str
    region: str  # BigQuery region (e.g., "US", "eu")
    processed_bytes: int | None = None  # Bytes from this specific table

    @property
    def full_name(self) -> str:
        return f"{self.project_id}.{self.dataset_id}.{self.table_id}"

    @property
    def is_cross_project(self, other_project: str) -> bool:
        """True if this table is in a different project than the specified one."""
        return self.project_id != other_project

    @property
    def is_logical_view(self) -> bool:
        """True if this is a view (not a base table)."""
        # Determined by querying INFORMATION_SCHEMA.TABLES
        # Requires additional metadata fetch
        pass
```

---

### 3. SmartTuningCheckResult

Represents the detailed Smart Tuning eligibility analysis for a query.

```python
@dataclass
class SmartTuningCheckResult:
    """Result of Smart Tuning eligibility check."""

    query_hash: str
    eligible: bool
    eligibility_basis: str  # "stable" or "preview"

    # Detailed analysis
    disqualification_reasons: list[str]  # If not eligible
    unsupported_features: list[str]  # ['UNION ALL', 'LEFT OUTER JOIN', ...]

    # Query structure
    aggregation_functions: list[str]
    join_types: list[str]
    has_ctes: bool
    subquery_types: list[str]  # ['SCALAR', 'ARRAY', 'CORRELATED']

    # Cross-tenancy and region checks
    cross_project_references: bool  # True if any referenced table is in a different project
    region_mismatch: bool  # True if referenced tables don't match target dataset region

    # Column analysis (for predicate lifting)
    liftable_columns: list[str]  # Columns in SELECT/GROUP BY
    non_liftable_filters: list[str]  # Filters on non-output columns

    # Recommendations
    recommended_mv_filters: list[str]  # Shared predicates across query family
    recommended_mv_select: list[str]  # SELECT expressions for MV
    recommended_mv_group_by: list[str]  # GROUP BY expressions

    # Synthesis audit (if MV generation was attempted)
    synthesis_audit: dict | None  # Contains sample_size_k, dropped_predicates, warnings, etc.
```

**Eligibility Rules** (from research):

| Condition | Required for Eligibility |
|-----------|-------------------------|
| No UNION ALL | ✅ |
| No LEFT/RIGHT/FULL OUTER JOIN | ✅ |
| No window functions | ✅ |
| No non-deterministic functions (NOW, RAND) | ✅ |
| No UDFs | ✅ |
| Uses only supported aggregates | ✅ |
| References base tables (not logical views) | ✅ |
| All query columns in MV output | ✅ |
| MV WHERE includes all query rows | ✅ |

---

### 4. MaterializedViewArtifact

Represents a created materialized view.

```python
@dataclass
class MaterializedViewArtifact:
    """A generated materialized view."""

    # Identity
    mv_name: str  # e.g., automv_abc123_def456
    source_query_hash: str  # Links to QueryCandidate (family_hash)
    signature_hash: str  # Stable hash of canonical MV SQL (for idempotency)

    # Location
    project_id: str
    dataset_id: str
    mv_region: str  # Explicit region (must match source tables)

    # Definition
    ddl_definition: str  # Full CREATE MATERIALIZED VIEW statement
    base_tables: list[TableReference]

    # Configuration
    refresh_interval_minutes: int
    enable_refresh: bool
    partition_expiration_days: int | None

    # Metadata
    created_at: datetime
    created_by: str  # User/service account
    created_by_tool_version: str  # Tool version that created this MV
    rulebook_version: str  # Eligibility rulebook version at creation
    synthesis_version: str  # MV synthesis algorithm version
    synthesis_warnings: list[str]  # Warnings from synthesis (e.g., "dropped_varying_predicates")

    # Status tracking
    status: MVStatus  # PROPOSED, CREATED, ACTIVE, STALE, INVALID, DEPRECATED, DROPPED
    eligibility_basis: str  # "stable" or "preview"
    last_refreshed: datetime | None

    # Usage statistics (from impact tracking)
    usage_count: int  # Number of times Smart Tuning used this MV
    total_bytes_saved: int
    total_slot_ms_saved: int
    last_used: datetime | None
```

```python
class MVStatus(Enum):
    """Status of a materialized view."""
    PROPOSED = "proposed"      # MV recommended but not created
    CREATED = "created"        # MV exists in BigQuery
    ACTIVE = "active"          # MV is being used by Smart Tuning above threshold
    STALE = "stale"            # MV exists but usage below threshold for N days
    INVALID = "invalid"        # Base table/schema changed, MV is unusable
    DEPRECATED = "deprecated"  # MV scheduled for removal
    DROPPED = "dropped"        # MV was removed
```

**DDL Generation Template**:

```sql
-- MV Name: {prefix}_{family_hash_short}_{signature_hash_short}
-- Family Hash: {family_hash}
-- Signature Hash: {signature_hash}
-- Region: {mv_region}
-- Rulebook Version: {rulebook_version}
-- Synthesis Version: {synthesis_version}
-- Eligibility Basis: {eligibility_basis}

CREATE MATERIALIZED VIEW IF NOT EXISTS `{project_id}.{dataset_id}.{mv_name}`
OPTIONS (
  enable_refresh = {enable_refresh},
  refresh_interval_minutes = {refresh_interval_minutes}
)
AS
SELECT
  {select_expressions}
FROM `{base_table_project}.{base_table_dataset}.{base_table_id}`
WHERE {shared_predicates}
GROUP BY {group_by_expressions};
```

---

### 5. CostAnalysisResult

Represents financial analysis of a query candidate.

```python
@dataclass
class CostAnalysisResult:
    """Financial analysis of a query candidate."""

    query_hash: str

    # Historical costs
    historical_spend_usd: float  # Actual spend over analysis period
    days_analyzed: int
    daily_avg_spend: float

    # Projected costs
    projected_monthly_spend: float
    projected_yearly_spend: float

    # Smart Tuning savings estimates
    smart_tuning_eligible: bool
    estimated_savings_percentage: float  # 0.0 to 1.0 (e.g., 0.8 = 80%)
    estimated_monthly_savings_usd: float
    estimated_yearly_savings_usd: float

    # Pricing model
    price_per_tib: float  # Default: $6.25

    # Confidence level
    confidence: str  # 'HIGH', 'MEDIUM', 'LOW'
```

**Calculation Formulas**:

```python
# Historical
historical_spend_usd = (total_bytes_billed / (1024**4)) * price_per_tib
daily_avg_spend = historical_spend_usd / days_analyzed

# Projected
projected_monthly_spend = daily_avg_spend * 30
projected_yearly_spend = daily_avg_spend * 365

# Savings (if Smart Tuning eligible)
if smart_tuning_eligible:
    # 70-90% reduction is typical for eligible queries
    estimated_savings_percentage = 0.8  # Conservative estimate
    estimated_monthly_savings = projected_monthly_spend * estimated_savings_percentage
else:
    estimated_savings_percentage = 0.0
    estimated_monthly_savings = 0.0
```

**Confidence Levels**:

| Confidence | Criteria |
|------------|----------|
| HIGH | 100+ executions, consistent patterns, eligible for Smart Tuning |
| MEDIUM | 10-100 executions, some variance |
| LOW | <10 executions, high variance, or insufficient data |

---

### 6. ImpactReport

Represents before/after comparison of optimization.

```python
@dataclass
class ImpactReport:
    """Before/after comparison for deployed materialized views."""

    # Report metadata
    report_id: str
    generated_at: datetime
    period_start: datetime
    period_end: datetime
    baseline_mode: str  # "none", "previous_period", or "explicit_range"
    baseline_period_start: datetime | None  # If comparing to baseline
    baseline_period_end: datetime | None  # If comparing to baseline

    # Per-MV statistics
    mv_impacts: list[MVImpact]

    # Aggregates
    total_bytes_saved: int
    total_slot_ms_saved: int
    total_dollars_saved_on_demand_equiv: float  # On-demand-equivalent savings
    total_queries_accelerated: int
    matching_executions_count: int  # Total matching jobs in measurement window

    # Unused MVs
    unused_mvs: list[str]  # MV names with zero usage
```

```python
@dataclass
class MVImpact:
    """Impact statistics for a single materialized view."""

    mv_name: str
    source_query_hash: str

    # Baseline (before MV)
    baseline_avg_bytes_processed: int | None  # None if baseline_mode=none
    baseline_avg_slot_ms: int | None
    baseline_execution_count: int | None

    # Current (after MV)
    current_avg_bytes_processed: int
    current_avg_slot_ms: int
    current_execution_count: int

    # Savings
    bytes_saved_per_execution: int
    slot_ms_saved_per_execution: int
    total_bytes_saved: int
    total_slot_ms_saved: int
    dollars_saved_on_demand_equiv: float  # On-demand-equivalent savings

    # Matching executions
    matching_executions_count: int  # Jobs matching family hash in measurement window

    # Usage
    smart_tuning_usage_count: int  # Times MV was used via Smart Tuning
    direct_query_count: int  # Times MV was queried directly
    usage_percentage: float  # Of matching queries that used MV

    # Attribution metadata
    attribution_method: str  # "bytes_proportional", "even_split", or "unknown"

    # Status
    status: MVStatus  # ACTIVE, STALE, INVALID, etc.
```

**Detection via INFORMATION_SCHEMA**:

```sql
-- Find queries that used MV via Smart Tuning
SELECT
  j.job_id,
  mv.table_reference.table_id AS mv_name,
  j.total_bytes_processed,
  j.total_slot_ms
FROM `region-us.INFORMATION_SCHEMA.JOBS` j,
  UNNEST(materialized_view_statistics.materialized_view) mv
WHERE mv.chosen = TRUE
  AND mv.table_reference.table_id = 'automv_abc123';
```

---

## Entity Relationships

```
┌─────────────────┐
│ QueryCandidate  │
│  (query_hash)   │
└────────┬────────┘
         │ 1
         │
         ├──┬──────────────────────────┐
         │                          │
         │ 1                        │ 1
         │                          │
         ▼                          ▼
┌─────────────────────┐    ┌────────────────────────┐
│ TableReference      │    │ SmartTuningCheckResult │
│ (referenced_tables) │    │   (query_hash)         │
└─────────────────────┘    └───────────┬────────────┘
                                       │
                                       │ 1
                                       │ recommends
                                       │
                                       ▼
                              ┌──────────────────────┐
                              │ MaterializedView     │
                              │   Artifact           │
                              │ (source_query_hash)  │
                              └──────────┬───────────┘
                                         │
                                         │ 1
                                         │
                                         ▼
                                ┌──────────────────┐
                                │   ImpactReport   │
                                │ (mv_impacts)     │
                                └──────────────────┘

┌─────────────────┐
│ CostAnalysis    │
│   Result        │
│ (query_hash)    │
└─────────────────┘
         │ 1
         │ analyzes
         │
         ▼
┌─────────────────┐
│ QueryCandidate  │
└─────────────────┘
```

---

## State Transitions

### QueryCandidate Lifecycle

```
                    ┌──────────────┐
                    │   DISCOVERED │
                    │ (Found in    │
                    │  JOBS scan)  │
                    └──────┬───────┘
                           │
                           ▼
                    ┌──────────────┐
                    │   ANALYZED   │
                    │ (Smart       │
                    │  Tuning      │
                    │  checked)    │
                    └──────┬───────┘
                           │
              ┌────────────┴────────────┐
              │                         │
              ▼                         ▼
      ┌──────────────┐          ┌──────────────┐
      │   ELIGIBLE   │          │ INELIGIBLE   │
      │ (Smart       │          │ (Cannot use   │
      │  Tuning OK)  │          │  Smart Tuning)│
      └──────┬───────┘          └──────────────┘
             │
             ▼
      ┌──────────────┐
      │  OPTIMIZED   │
      │ (MV created) │
      └──────────────┘
```

### MaterializedViewArtifact Lifecycle

```
┌──────────┐
│ PROPOSED │
└────┬─────┘
     │
     │ MV created
     ▼
┌──────────┐
│  CREATED │
└────┬─────┘
     │
     ▼
┌──────────┐
│  ACTIVE  │◄────────────────┐
└────┬─────┘                 │
     │                       │
     │ No usage detected      │ Regular usage
     ▼                        │
┌──────────┐                  │
│  STALE   │──────────────────┘
└────┬─────┘
     │
     │ Base table changed / schema drift
     ▼
┌──────────┐      ┌───────────┐
│ INVALID  │─────>│ PROPOSED  │ (regeneration if still high-impact)
└────┬─────┘      └───────────┘
     │
     │ Scheduled for removal (stale > N days)
     ▼
┌──────────┐
│DEPRECATED│
└────┬─────┘
     │
     │ Manual drop or auto-cleanup
     ▼
┌──────────┐
│ DROPPED  │
└──────────┘
```

---

## Storage Considerations

### Internal Storage (Application State)

| Entity | Storage | Notes |
|--------|---------|-------|
| QueryCandidate | In-memory (analysis results) | Ephemeral |
| MaterializedViewArtifact | BigQuery table | Persistent metadata |
| ImpactReport | Generated on-demand | Not stored |

### BigQuery Metadata Table Schema

```sql
CREATE TABLE `{project}.{dataset}.automv_metadata` (
  mv_name STRING,
  source_query_hash STRING,
  signature_hash STRING,
  created_at TIMESTAMP,
  base_tables ARRAY<STRUCT<
    project_id STRING,
    dataset_id STRING,
    table_id STRING
  >>,
  project_id STRING,
  dataset_id STRING,
  mv_region STRING,
  status STRING,
  eligibility_basis STRING,
  refresh_interval_minutes INT64,
  ddl_definition STRING,
  created_by_tool_version STRING,
  rulebook_version STRING,
  synthesis_version STRING,
  synthesis_warnings ARRAY<STRING>,
  last_refreshed TIMESTAMP,
  usage_count INT64,
  total_bytes_saved INT64,
  total_slot_ms_saved INT64,
  last_used TIMESTAMP
);
```

---

## Validation Rules Summary

| Entity | Rule |
|--------|------|
| QueryCandidate | `query_hash` non-empty, `execution_count` >= 1 |
| SmartTuningCheckResult | Must include aggregation functions if query has GROUP BY |
| MaterializedViewArtifact | `mv_name` must start with prefix (e.g., `automv_`) |
| CostAnalysisResult | `price_per_tib` > 0 |
| ImpactReport | `period_end` > `period_start` |

---

## Indexing Strategy

For BigQuery metadata table:
- Cluster by `status` for filtering active vs unused
- Partition by `created_at` for time-based queries
- No secondary indexes (BigQuery doesn't support them)

For query analysis:
- Use `normalized_literals` as grouping key
- Filter by `creation_time` (partitioning column)
