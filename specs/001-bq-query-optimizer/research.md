# Research: BigQuery Query Optimizer with Smart Materialized Views

**Feature**: `001-bq-query-optimizer`
**Date**: 2026-01-03
**Status**: Complete

---

## Executive Summary

This document captures research findings for building a BigQuery materialized view automation tool. Key decisions:

1. **CLI Framework**: Cyclopts (modern, type-hint-driven, async-first)
2. **SQL Parsing**: sqlglot (BigQuery dialect support, AST traversal)
3. **Query Source**: INFORMATION_SCHEMA.JOBS (normalized_literals for grouping)
4. **Smart Tuning**: Extremely specific requirements around column liftable patterns

---

## 1. CLI Framework: Cyclopts

### Decision
**Chosen**: Cyclopts (`uv add cyclopts`)

### Rationale
| Feature | Cyclopts | Typer | Click |
|---------|----------|-------|-------|
| Type-hint driven | ✅ Full | Partial | Manual |
| Union types | ✅ Native | Limited | Custom |
| Boolean flags | ✅ Auto `--no-*` | Manual | Manual |
| Async support | ✅ Native | Extensions | Extensions |
| Code verbosity | ~29 lines | ~47 lines | Boilerplate-heavy |
| Help generation | ✅ Auto from docstrings | Limited | Manual strings |

### Key Patterns

**Shared Configuration via Dataclass**:
```python
from cyclopts import App, Parameter
from dataclasses import dataclass

@Parameter(name="*")  # Flatten namespace
@dataclass
class CommonConfig:
    """Shared parameters for all commands."""
    project: str = "my-project"
    dataset: str = "my_dataset"
    dry_run: bool = False
    verbose: bool = False

app = App(name="bq-automv")

@app.command
def analyze(days: int, *, common: CommonConfig | None = None):
    """Analyze query patterns for MV candidates."""
    if common is None:
        common = CommonConfig()
    # All commands inherit --project, --dataset, --dry-run, --verbose
```

**Boolean Flags with Auto-Negation**:
```python
@app.command
def deploy(
    *,
    dry_run: Annotated[bool, Parameter(
        negative="",  # Disable --no-dry-run
        help="Preview changes without executing",
    )] = False,
    json_output: Annotated[bool, Parameter(name="--json")] = False,
):
    pass
```

**Async Commands**:
```python
@app.command
async def fetch(url: str):
    """Async BigQuery API calls."""
    async with aiohttp.ClientSession() as session:
        await session.get(url)
```

### Environment Variable Support
```python
@app.command
def deploy(
    *,
    api_key: Annotated[str, Parameter(env_var=["API_KEY", "DEPLOY_API_KEY"])],
    region: Annotated[str, Parameter(env_var="AWS_REGION")] = "us-east-1",
):
    pass
```

### Exit Codes
```python
import sys

if __name__ == "__main__":
    sys.exit(app())  # Return value becomes exit code
```

### Alternatives Considered
- **Typer**: More mature ecosystem but less type-safe, more boilerplate
- **Click**: Very popular but decorator-heavy, not type-hint driven
- **argparse**: Built-in but verbose, poor async support

---

## 2. SQL Parsing: sqlglot

### Decision
**Chosen**: sqlglot (`uv add sqlglot`)

### Rationale
- BigQuery dialect support (`parse_one(sql, dialect="bigquery")`)
- Pure Python, no dependencies
- AST traversal with `find()`, `find_all()`, `walk()`
- Column reference detection
- Aggregation function detection
- Query normalization for pattern matching

### Key Patterns

**Parse and Extract Components**:
```python
from sqlglot import parse_one, exp

ast = parse_one(query, dialect="bigquery")

# Get clauses
where = ast.find(exp.Where)
select = ast.find(exp.Select)
group_by = select.args.get("group")
joins = list(ast.find_all(exp.Join))
ctes = ast.find(exp.With)
```

**Detect Aggregations**:
```python
agg_funcs = {func.name for func in ast.find_all(exp.AggFunc)}
# {'COUNT', 'SUM', 'AVG', 'MAX', 'MIN', ...}
```

**Extract Column References**:
```python
def extract_where_columns(sql):
    ast = parse_one(sql, dialect="bigquery")
    where = ast.find(exp.Where)
    if not where:
        return []
    columns = {col.sql() for col in where.find_all(exp.Column)}
    return list(columns)
```

**Normalize Queries (Pattern Matching)**:
```python
def normalize_query(sql):
    ast = parse_one(sql, dialect="bigquery")
    def replace_literals(node):
        if isinstance(node, exp.Literal):
            return exp.Literal.string("__LIT__")
        return node
    normalized_ast = ast.transform(replace_literals)
    return normalized_ast.sql()

# Groups queries that differ only in literals
query1 = "SELECT * WHERE id = 123"
query2 = "SELECT * WHERE id = 456"
assert normalize_query(query1) == normalize_query(query2)
```

**Check for Unsupported Patterns**:
```python
def has_unsupported_patterns(sql):
    ast = parse_one(sql, dialect="bigquery")

    # Check for UNION ALL
    if ast.find(exp.Union):
        return True, "UNION ALL not supported"

    # Check for LEFT OUTER JOIN
    for join in ast.find_all(exp.Join):
        if join.side and "LEFT" in join.side:
            return True, "LEFT OUTER JOIN not supported"

    # Check for window functions
    if ast.find(exp.Window):
        return True, "Window functions not supported"

    return False, None
```

### Alternatives Considered
- **sqlparse**: No BigQuery dialect, no AST
- **moz-sql-parser**: Abandoned project
- **Custom regex**: Fragile, hard to maintain

---

## 3. BigQuery Schema: INFORMATION_SCHEMA.JOBS

### Decision
**Source**: `INFORMATION_SCHEMA.JOBS` (region-scoped)

### Key Columns

| Column | Type | Use Case |
|--------|------|----------|
| `query` | STRING | Full SQL for analysis |
| `query_info.query_hashes.normalized_literals` | STRING | **Group queries by pattern** |
| `referenced_tables` | ARRAY(STRUCT) | Extract base tables |
| `total_bytes_billed` | INT64 | Cost calculation |
| `total_bytes_processed` | INT64 | Data volume analysis |
| `total_slot_ms` | INT64 | Compute consumption |
| `creation_time` | TIMESTAMP | **Partition column** - filter first |
| `job_type` | STRING | Filter to 'QUERY' |
| `statement_type` | STRING | Filter to 'SELECT' |
| `materialized_view_statistics` | ARRAY(STRUCT) | **Detect MV usage** (Preview) |

### Critical Limitations

1. **180-day retention** - Max history available
2. **Region-scoped** - Query `region-us.INFORMATION_SCHEMA.JOBS`, not hierarchical
3. **NULL for RLS queries** - `total_bytes_billed` is NULL for row-level security
4. **SCRIPT jobs** - Exclude to avoid double-counting
5. **Cache hits** - `referenced_tables` is NULL, filter these out

### Query Pattern for Analysis

```sql
WITH base_jobs AS (
  SELECT
    project_id,
    job_id,
    creation_time,
    total_slot_ms,
    query,
    query_info.query_hashes.normalized_literals,
    referenced_tables
  FROM `region-us.INFORMATION_SCHEMA.JOBS`
  WHERE
    job_type = 'QUERY'
    AND state = 'DONE'
    AND creation_time >= @start_time
    AND query_info.query_hashes.normalized_literals IS NOT NULL
    AND statement_type = 'SELECT'
    AND referenced_tables IS NOT NULL
    AND NOT EXISTS (
      SELECT 1 FROM UNNEST(referenced_tables) AS t
      WHERE t.project_id IS NULL OR t.project_id != project_id
    )
    -- Smart tuning does NOT support UNION ALL or LEFT OUTER JOIN
    AND NOT REGEXP_CONTAINS(LOWER(query), r'\bunion\s+all\b')
    AND NOT REGEXP_CONTAINS(LOWER(query), r'\bleft(\s+outer)?\s+join\b')
),

base_jobs_enriched AS (
  SELECT
    b.*,
    ARRAY(
      SELECT FORMAT('%s.%s.%s', t.project_id, t.dataset_id, t.table_id)
      FROM UNNEST(b.referenced_tables) AS t
      ORDER BY 1
    ) AS ref_tables_array
  FROM base_jobs AS b
),

families AS (
  SELECT
    normalized_literals,
    SUM(total_slot_ms) AS family_slot_ms,
    COUNT(*) AS family_job_count,
    COUNT(DISTINCT query) AS family_distinct_query_count,
    COUNT(DISTINCT ARRAY_TO_STRING(ref_tables_array, ',')) AS distinct_table_sets,
    ANY_VALUE(ref_tables_array) AS example_ref_tables_array
  FROM base_jobs_enriched
  GROUP BY normalized_literals
)

SELECT * FROM families
WHERE family_slot_ms >= @min_family_slot_ms
  AND distinct_table_sets = 1  -- Same base tables across family
ORDER BY family_slot_ms DESC
LIMIT @max_families;
```

### Detecting MV Usage (for Impact Tracking)

```sql
SELECT
  job_id,
  mv.table_reference.table_id AS mv_name,
  mv.chosen AS mv_used,
  mv.rejected_reason AS mv_rejection_reason
FROM `region-us.INFORMATION_SCHEMA.JOBS_BY_PROJECT`,
  UNNEST(materialized_view_statistics.materialized_view) mv
WHERE creation_time > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY);
```

---

## 4. BigQuery Smart Tuning: Exact Requirements

### Critical Insight: The "Predicate Lifting" Pattern

**The user's observation is correct**: Smart Tuning requires the MV and query to have essentially the **same WHERE clause filters** on **non-liftable columns**. This is the key constraint.

### Definitions

- **Liftable column**: Column that appears in MV's `SELECT` or `GROUP BY` (output column)
- **Non-liftable filter**: Filter in MV's `WHERE` on a column NOT in `SELECT`/`GROUP BY`

### Hard Requirements

For a query to be Smart Tuning eligible:

1. **Same base tables** - MV and query reference identical tables
2. **All columns in MV output** - Query's SELECT/GROUP BY columns must be MV outputs
3. **All rows in MV** - MV's WHERE must be a **superset** of query's WHERE (less or equal restrictive)
4. **Same dataset** - MV must be in same dataset as base tables
5. **Incremental MV** - No `allow_non_incremental_definition`
6. **No logical views** - MV cannot reference logical views
7. **Supported patterns only** - No UNION ALL, LEFT OUTER JOIN, window functions

### The Predicate Lifting Rule

| MV WHERE | Query WHERE | Smart Tuning? |
|----------|-------------|---------------|
| `date >= '2021-01-01'` (non-liftable) | `date >= '2022-01-01'` | ❌ NO - date not liftable, must match exactly |
| `promo_id IS NOT NULL` (non-liftable) | `promo_id = 123` | ❌ NO - promo_id not liftable |
| `store_id = 100` (non-liftable) | `store_id = 100` | ✅ YES - exact match on non-liftable |
| (none) | `store_id = 100` | ❌ NO - MV doesn't have this filter, can't add |

**Key insight**: Non-liftable filters in MV WHERE **must match exactly** in query WHERE. The query cannot apply a more restrictive filter on non-liftable columns.

For **liftable columns**, queries CAN be more restrictive:
- MV has `SELECT store_id, ...`
- Query can filter `WHERE store_id = 123` (more restrictive) ✅

### Example from User's Notes

```sql
-- User's MV
CREATE MATERIALIZED VIEW billing_mv_aws_monthly_cost_by_ci AS
SELECT
  TIMESTAMP_TRUNC(startTime, MONTH) AS time_granularity,
  labelsV2.ApplicationCI AS application_ci,
  SUM(cost) AS total_cost
FROM unifiedTable
WHERE cloudProvider = 'AWS'  -- Non-liftable filter
  AND startTime >= '2024-01-01'  -- Non-liftable filter
GROUP BY 1, 2;

-- ELIGIBLE: Exact match on non-liftable filters
SELECT SUM(total_cost)
FROM unifiedTable
WHERE cloudProvider = 'AWS'  -- Must match MV exactly
  AND startTime >= '2024-01-01'  -- Must match MV exactly
  AND labelsV2.ApplicationCI = 'my-app';  -- liftable, can filter further

-- NOT ELIGIBLE: Different non-liftable filter
SELECT SUM(total_cost)
FROM unifiedTable
WHERE cloudProvider = 'AWS'
  AND startTime >= '2024-06-01';  -- 6m vs 1y - different filter on non-liftable column
```

### User's Discovery

> "I can actively see the smart tuning working! But only if my query has the exact same filters (excluding the lifted columns) as the original query. What this means is that while my MV might include 1y of data, the queries against it need to have the same 1y date filter and cannot look at 6m for example."

**This is the expected behavior** based on BigQuery's implementation:
- If MV has `WHERE date >= '2024-01-01'` (non-liftable)
- Query with `WHERE date >= '2024-06-01'` → NOT ELIGIBLE
- Query with `WHERE date >= '2024-01-01'` → ELIGIBLE

**Solution**: For flexible date filtering, make the date column **liftable**:
```sql
-- Better MV pattern for flexible date filtering
CREATE MATERIALIZED VIEW mv AS
SELECT
  DATE_TRUNC(transaction_date, MONTH) AS month,  -- liftable!
  store_id,  -- liftable!
  SUM(amount) AS total
FROM sales
WHERE region = 'US'  -- Non-liftable: must match in queries
GROUP BY 1, 2;

-- Now queries can filter on month (liftable)
-- WHERE month >= '2024-06-01' works!
```

### Disqualifying Patterns

| Pattern | Smart Tuning? |
|---------|---------------|
| `UNION ALL` | ❌ NO (Preview only) |
| `LEFT OUTER JOIN` | ❌ NO (Preview only) |
| `RIGHT/FULL OUTER JOIN` | ❌ NO |
| Self-joins | ❌ NO |
| Window functions | ❌ NO |
| Non-deterministic functions (`NOW()`, `RAND()`) | ❌ NO |
| UDFs | ❌ NO |
| `HAVING` on aggregates | ❌ NO |
| Computed aggregates (`COUNT(*) / 10`) | ❌ NO |
| MV references logical view | ❌ NO |
| Non-incremental MV | ❌ NO |

### Supported Aggregation Functions

```
ANY_VALUE, APPROX_COUNT_DISTINCT, ARRAY_AGG, AVG,
BIT_AND, BIT_OR, BIT_XOR, COUNT, COUNTIF, HLL_COUNT.INIT,
LOGICAL_AND, LOGICAL_OR, MAX, MIN, MAX_BY, MIN_BY, SUM
```

### MV Generation Algorithm

Based on user's notes, the algorithm simplifies to:

1. **Extract from family's sample queries**:
   - SELECT expressions
   - GROUP BY expressions
   - WHERE clause predicates

2. **Compute predicates**:
   - `shared_predicates` = intersection of predicates across queries
   - `per_query_predicates` = everything else

3. **For each per_query predicate**, extract referenced columns:
   - These become **liftable columns** in MV

4. **MV definition**:
   - `FROM` = dominant base table
   - `WHERE` = AND over `shared_predicates` (non-liftable filters)
   - `SELECT` = group-by expressions + lifted columns + heavy aggregates
   - `GROUP BY` = group-by expressions + all lifted columns

**Example**:
```python
# Query 1: WHERE date >= '2024-01-01' AND region = 'US'
# Query 2: WHERE date >= '2024-01-01' AND region = 'EU'

# shared_predicates = ["date >= '2024-01-01'"]
# per_query_predicates = ["region = 'US'", "region = 'EU'"]
# lifted_columns = ["region"]

# MV:
# SELECT date, region, SUM(...)
# FROM table
# WHERE date >= '2024-01-01'  -- shared only
# GROUP BY date, region  -- includes lifted column
```

---

## 5. Cost Calculation

### BigQuery On-Demand Pricing
- **$6.25 per TiB** for queries (as of 2025)
- **10 MB minimum** per query
- Total bytes billed (not processed) = what you're charged

### Calculation Formula

```python
# Historical spend
historical_spend = (sum(total_bytes_billed) / (1024**4)) * price_per_tib

# Projected monthly
days_analyzed = (end_date - start_date).days
daily_avg = historical_spend / days_analyzed
projected_monthly = daily_avg * 30

# Estimated savings for Smart Tuning
# (70-90% reduction in bytes processed is typical)
estimated_savings = projected_monthly * 0.8  # 80% estimate
```

---

## 6. Technology Stack Summary

| Component | Technology | Command |
|-----------|------------|---------|
| CLI Framework | cyclopts | `uv add cyclopts` |
| SQL Parser | sqlglot | `uv add sqlglot` |
| BigQuery Client | google-cloud-bigquery | `uv add google-cloud-bigquery` |
| Async HTTP | aiohttp | `uv add aiohttp` |
| Testing | pytest | `uv add --dev pytest` |
| Type Checking | mypy | `uv add --dev mypy` |

### Project Structure (Single Package)
```text
src/bigquery_automv/
├── __init__.py
├── cli/
│   ├── __init__.py
│   ├── app.py           # Cyclopts App() definition
│   ├── commands/
│   │   ├── analyze.py
│   │   ├── report.py
│   │   ├── smart_tuning_check.py
│   │   ├── generate_mv.py
│   │   └── impact.py
├── models/
│   ├── __init__.py
│   ├── query_candidate.py
│   └── mv_artifact.py
├── services/
│   ├── __init__.py
│   ├── bq_client.py     # BigQuery async client
│   ├── sql_parser.py    # sqlglot wrapper
│   ├── analyzer.py      # Query analysis logic
│   └── mv_generator.py  # MV DDL generation
└── lib/
    ├── __init__.py
    ├── config.py
    └── utils.py

tests/
├── contract/
├── integration/
└── unit/
```

---

## 7. Open Questions & Risks

### Open Questions
1. **Partition column behavior**: User noted that date filters on partitioning columns seem to require exact matches. Need to verify if this is specific to partitioning or general predicate behavior.

2. **Preview features**: Should we support LEFT OUTER JOIN and UNION ALL (Preview-only, no Smart Tuning)?

3. **MV metadata storage**: Where to store MV → query hash mappings? Options:
   - BigQuery table
   - Local SQLite
   - File-based JSON

### Risks
1. **Smart Tuning opacity**: BigQuery's internal matching logic is not fully documented. Our eligibility checks may produce false positives/negatives.

2. **Cost of analysis**: Querying 180 days of INFORMATION_SCHEMA.JOBS can be expensive. Need to implement cost controls.

3. **MV storage costs**: Generated MVs incur storage charges (~$0.02/GB/month). Need lifecycle management.

4. **Region complexity**: Multi-region projects require separate queries per region.

---

## 8. Next Steps (Phase 1)

1. **Data Model**: Define entity schemas for QueryCandidate, MVArtifact, CostAnalysis, etc.
2. **API Contracts**: Define CLI command signatures and output formats
3. **Quickstart**: User-facing documentation for common workflows
