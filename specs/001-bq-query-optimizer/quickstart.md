# Quickstart Guide: BigQuery Query Optimizer

**Feature**: `001-bq-query-optimizer`
**Date**: 2026-01-03

---

## Prerequisites

### 1. Authentication

Set up Google Cloud authentication:

```bash
# Using Application Default Credentials
gcloud auth application-default login

# Or set the path to service account key
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json
```

### 2. Permissions

Required IAM roles:
- `roles/bigquery.resourceViewer` - For querying INFORMATION_SCHEMA.JOBS
- `roles/bigquery.admin` - For creating materialized views

### 3. Target Dataset

Create a dataset for materialized views:

```bash
bq mk --dataset --location=US my-project:analytics_mvs
```

---

## Installation

```bash
# Clone repository
git clone https://github.com/z3z1ma/bigquery-automv
cd bigquery-automv

# Install with uv
uv sync

# Verify installation
uv run bq-automv --help
```

---

## Common Workflows

### Workflow 1: Find Expensive Queries

Analyze query history to find the most costly query patterns:

```bash
# Basic analysis (uses last 30 days by default)
bq-automv analyze 2024-01-01

# Full analysis with custom parameters
bq-automv analyze \
  --start-date 2024-01-01 \
  --end-date 2024-01-31 \
  --min-executions 20 \
  --min-bytes 1073741824 \
  --output candidates.json

# With project override
bq-automv analyze 2024-01-01 --project my-project
```

**What it does**:
- Queries INFORMATION_SCHEMA.JOBS for the date range
- Groups queries by `normalized_literals` hash
- Identifies patterns with high execution frequency × cost
- Checks Smart Tuning eligibility

**Output** (JSON):
```json
{
  "candidates": [
    {
      "query_hash": "abc123...",
      "execution_count": 1523,
      "total_bytes_billed": 123456789012,
      "smart_tuning_eligible": true
    }
  ]
}
```

---

### Workflow 2: Generate Cost Report

Get financial projections for optimization candidates:

```bash
# Generate report for specific query hashes
bq-automv report --query-hashes abc123... def456... \
  --format markdown \
  --price-per-tib 6.25

# Read hashes from file (one per line)
bq-automv report --from-file eligible_hashes.txt \
  --format json \
  --output report.json

# Using date range instead of days lookback
bq-automv report abc123... \
  --start-date 2024-01-01 \
  --end-date 2024-01-31
```

**What it does**:
- Calculates historical spend from `total_bytes_billed`
- Projects monthly/yearly costs
- Estimates Smart Tuning savings (70-90% reduction)

**Output** (Markdown):
```markdown
## Query: abc123...

| Metric | Value |
|--------|-------|
| Execution Count | 1,523 |
| Historical Spend | $456.78 |
| Projected Monthly | $500.00 |
| Est. Monthly Savings | $350.00 (70%) |
```

---

### Workflow 3: Check Smart Tuning Eligibility

Verify if a query can benefit from automatic MV routing:

```bash
# Check from file
bq-automv smart-tuning-check --sql-file query.sql --verbose

# Check inline SQL
bq-automv smart-tuning-check --sql "
  SELECT
    DATE_TRUNC(transaction_date, MONTH) AS month,
    store_id,
    SUM(amount) AS total
  FROM analytics.transactions
  WHERE region = 'US'
  GROUP BY 1, 2
" --verbose

# With target dataset for region validation
bq-automv smart-tuning-check --sql-file query.sql \
  --target-dataset analytics_mvs \
  --target-project my-project
```

**What it does**:
- Parses SQL with sqlglot
- Checks for unsupported patterns (UNION ALL, LEFT JOIN, etc.)
- Identifies aggregation functions
- Analyzes WHERE clauses for "predicate lifting" potential

**Output**:
```
✓ Eligible for Smart Tuning

Analysis:
- Aggregations: COUNT, SUM
- Joins: None
- CTEs: None
- Unsupported patterns: None

Recommended MV:
- Liftable columns: store_id, DATE_TRUNC(transaction_date, MONTH)
- Non-liftable filters: region = 'US'
```

---

### Workflow 4: Generate Materialized Views

Create MVs for eligible queries:

```bash
# Dry run first (inspect DDL without deploying)
bq-automv generate-mv abc123... --dry-run

# Deploy with defaults
bq-automv generate-mv abc123... \
  --dataset analytics_mvs \
  --yes

# Deploy with custom refresh interval
bq-automv generate-mv abc123... \
  --dataset analytics_mvs \
  --refresh-interval-minutes 1440 \
  --enable-refresh \
  --yes

# Deploy with preview eligibility features
bq-automv generate-mv abc123... \
  --dataset analytics_mvs \
  --enable-preview-eligibility \
  --yes

# Generate for multiple hashes
bq-automv generate-mv --from-file eligible_hashes.txt \
  --dataset analytics_mvs \
  --yes
```

**What it does**:
- Generates `CREATE MATERIALIZED VIEW` DDL
- Uses predicate lifting to set WHERE clause
- Deploys to BigQuery (unless `--dry-run`)

**Generated DDL**:
```sql
CREATE MATERIALIZED VIEW `my-project.analytics_mvs.automv_abc123`
OPTIONS (
  enable_refresh = true,
  refresh_interval_minutes = 60
)
AS
SELECT
  DATE_TRUNC(transaction_date, MONTH) AS month,
  store_id,
  SUM(amount) AS total
FROM `my-project.analytics.transactions`
WHERE region = 'US'
GROUP BY 1, 2;
```

---

### Workflow 5: Track Impact

Measure actual savings after MV deployment:

```bash
# Wait 7 days for queries to use the MV

# Basic impact report
bq-automv impact \
  --start-date 2024-01-15 \
  --end-date 2024-01-22 \
  --format markdown

# With explicit baseline comparison
bq-automv impact \
  --start-date 2024-01-15 \
  --end-date 2024-01-22 \
  --baseline-mode explicit_range \
  --baseline-start 2024-01-01 \
  --baseline-end 2024-01-07

# With automatic previous period baseline
bq-automv impact \
  --start-date 2024-01-15 \
  --end-date 2024-01-22 \
  --baseline-mode previous_period

# Filter by specific MV name
bq-automv impact \
  --start-date 2024-01-15 \
  --mv-name automv_abc123_def456
```

**What it does**:
- Queries `materialized_view_statistics` for MV usage
- Compares before/after query metrics
- Calculates actual dollar savings

**Output**:
```markdown
## Impact: automv_abc123

| Metric | Before | After | Savings |
|--------|--------|-------|---------|
| Avg Bytes/Query | 1.2 GB | 150 MB | 87.5% |
| Execution Count | 500 | 500 | - |
| **Dollar Savings** | - | - | **$67.50** |

Smart Tuning Usage: 423 of 500 queries (84.6%)
```

---

## Environment Variables

Set defaults to avoid repeating flags:

```bash
# ~/.bashrc or ~/.zshrc
export BQ_AUTOMV_PROJECT=my-project
export BQ_AUTOMV_DATASET=analytics_mvs
export BQ_AUTOMV_REGION=region-us
```

Now commands become:
```bash
bq-automv analyze 2024-01-01
# No need to specify --project, --dataset, --region
```

---

## Full Example: End-to-End

```bash
# 1. Analyze last 30 days
bq-automv analyze \
  --start-date $(date -v-30d +%Y-%m-%d) \
  --min-executions 50 \
  --output candidates.json

# 2. Extract Smart Tuning eligible hashes
jq '.candidates[] | select(.smart_tuning_eligible) | .query_hash' \
  candidates.json > eligible_hashes.txt

# 3. Generate cost report
bq-automv report --from-file eligible_hashes.txt \
  --format markdown > cost_report.md

# 4. Review DDL before deploying
bq-automv generate-mv --from-file eligible_hashes.txt \
  --dry-run > mv_ddl.sql

# 5. Deploy MVs
bq-automv generate-mv --from-file eligible_hashes.txt \
  --dataset analytics_mvs \
  --yes

# 6. Wait 7 days, then check impact
sleep 7d
bq-automv impact \
  --start-date $(date -v-7d +%Y-%m-%d) \
  --end-date $(date +%Y-%m-%d) \
  --baseline-mode previous_period
```

---

## Troubleshooting

### "Permission denied"

**Error**: `Permission denied: BigQuery BigQuery: Permission denied on table`

**Solution**:
```bash
# Grant BigQuery Admin role
gcloud projects add-iam-policy-binding my-project \
  --member=user:you@example.com \
  --role=roles/bigquery.admin
```

### "No candidates found"

**Cause**: No queries meet thresholds or date range has no data.

**Solution**:
```bash
# Lower thresholds
bq-automv analyze \
  --start-date 2024-01-01 \
  --min-executions 1 \
  --min-bytes 0
```

### "MV not being used"

**Check 1**: Verify Smart Tuning eligibility
```bash
bq-automv smart-tuning-check --sql "$(bq query --nouse_legacy_sql ..."
```

**Check 2**: Look for rejection reasons
```bash
# Query INFORMATION_SCHEMA directly
bq query --nouse_legacy_sql "
SELECT
  mv.rejected_reason,
  COUNT(*) as count
FROM \`region-us.INFORMATION_SCHEMA.JOBS_BY_PROJECT\`,
  UNNEST(materialized_view_statistics.materialized_view) mv
WHERE mv.table_reference.table_id LIKE 'automv_%'
GROUP BY 1
"
```

---

## Best Practices

### 1. Start with Dry Run

Always use `--dry-run` before creating MVs:
```bash
bq-automv generate-mv abc123... --dry-run
```

### 2. Set Appropriate Refresh Intervals

Match refresh interval to data freshness requirements:
- **Real-time data**: 15-30 minutes
- **Daily ETL**: 1440 minutes (24 hours)
- **Batch analytics**: 10080 minutes (7 days)

```bash
bq-automv generate-mv abc123... --refresh-interval-minutes 1440
```

### 3. Monitor Unused MVs

Check impact regularly to identify unused MVs:
```bash
bq-automv impact --start-date $(date -v-30d +%Y-%m-%d) | grep "UNUSED"
```

Drop unused MVs to avoid storage costs:
```bash
bq rm -t my-project.analytics_mvs.automv_unused
```

### 4. Understand Predicate Lifting

**Liftable columns** (in SELECT/GROUP BY):
- Queries can filter more restrictively
- Example: MV has `store_id`, query can use `WHERE store_id = 123`

**Non-liftable filters** (in WHERE only):
- Queries must match exactly
- Example: MV has `WHERE region = 'US'`, query cannot use `region = 'EU'`

**Best practice**: Make commonly-filtered columns liftable:
```sql
-- Good: date is liftable
CREATE MATERIALIZED VIEW mv AS
SELECT
  DATE_TRUNC(date, MONTH) AS month,  -- liftable
  SUM(amount)
FROM table
WHERE region = 'US'  -- non-liftable: fixed
GROUP BY 1;

-- Avoid: date is non-liftable
CREATE MATERIALIZED VIEW mv AS
SELECT
  store_id,
  SUM(amount)
FROM table
WHERE date >= '2024-01-01'  -- non-liftable: inflexible
GROUP BY 1;
```

---

## Next Steps

- [ ] Run initial analysis on your project
- [ ] Review Smart Tuning eligible candidates
- [ ] Generate cost projections for stakeholders
- [ ] Deploy MVs for top 5 candidates
- [ ] Monitor impact after 7 days
- [ ] Iterate on MV definitions based on usage patterns

---

## Reference

| Command | Purpose |
|---------|---------|
| `analyze` | Find expensive query patterns |
| `report` | Generate cost projections |
| `smart-tuning-check` | Verify Smart Tuning eligibility |
| `generate-mv` | Create materialized views |
| `impact` | Measure MV usage and savings |

Full CLI documentation: See `contracts/cli.md`
