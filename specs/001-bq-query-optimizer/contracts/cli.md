# CLI Contracts: BigQuery Query Optimizer

**Feature**: `001-bq-query-optimizer`
**Date**: 2026-01-03

---

## Overview

This document defines the CLI interface contracts for all commands. The tool uses **cyclopts** for type-hint-driven argument parsing.

**Entry Point**: `bq-automv` (defined in `pyproject.toml` `[project.scripts]`)

---

## Global Options

These options are available to all commands via the `CommonConfig` dataclass:

| Option | Type | Default | Env Var | Description |
|--------|------|---------|---------|-------------|
| `--project` | STRING | `GOOGLE_CLOUD_PROJECT` | `BQ_AUTOMV_PROJECT` | GCP project ID |
| `--region` | STRING | `region-us` | `BQ_AUTOMV_REGION` | BigQuery region (single region per run) |
| `--dataset` | STRING | (required) | `BQ_AUTOMV_DATASET` | Target dataset for MVs (must be in same region as source tables) |
| `--dry-run` | BOOL | `false` | - | Preview without executing |
| `--verbose` / `-v` | BOOL | `false` | - | Enable verbose logging |
| `--json` | BOOL | `false` | - | Output JSON format |
| `--enable-preview-eligibility` | BOOL | `false` | - | Enable preview feature eligibility checks |
| `--include-user-email` | BOOL | `false` | - | Include user_email in output (privacy-sensitive) |
| `--include-query-text` | BOOL | `false` | - | Include full query text in output (privacy-sensitive) |

---

## Command: `analyze`

Analyze BigQuery INFORMATION_SCHEMA.JOBS to identify expensive query patterns.

### Signature

```bash
bq-automv analyze [OPTIONS]
```

### Options

| Option | Type | Required | Default | Description |
|--------|------|----------|---------|-------------|
| `--start-date` | DATE | Yes | - | Start of analysis window (inclusive) |
| `--end-date` | DATE | No | `today` | End of analysis window (inclusive) |
| `--min-executions` | INT | No | `10` | Minimum execution count per query family |
| `--min-bytes` | INT | No | `1073741824` (1GB) | Minimum bytes processed threshold |
| `--min-slot-ms` | INT | No | `0` | Minimum slot milliseconds threshold |
| `--max-families` | INT | No | `100` | Maximum number of query families to return |
| `--output` | PATH | No | - | Write results to file (JSON/CSV) |
| `--price-per-tib` | FLOAT | No | `6.25` | BigQuery on-demand price per TiB for impact scoring |
| `--slot-weight` | FLOAT | No | `0.25` | Weight for slot component in impact score |
| `--slot-ms-per-tib-equivalent` | FLOAT | No | `3.6e9` | Slot-ms to TiB equivalent conversion factor |
| `--sample-size-k` | INT | No | `20` | Number of sample queries for MV synthesis |
| `--rulebook-version` | STRING | No | `latest` | Smart Tuning rulebook version for eligibility checks |

### Output (JSON)

```json
{
  "meta": {
    "version": "1.0.0",
    "generated_at": "2024-01-31T12:00:00Z",
    "command": "analyze",
    "rulebook_version": "v1.0",
    "impact_model_version": "v1.0",
    "region": "region-us"
  },
  "analysis_period": {
    "start_date": "2024-01-01",
    "end_date": "2024-01-31",
    "days_analyzed": 31
  },
  "candidates": [
    {
      "query_hash": "abc123...",
      "representative_query": "SELECT ...",
      "execution_count": 1523,
      "bytes_billed_total": 123456789012,
      "total_bytes_processed": 9876543210,
      "slot_ms_total": 987654321,
      "impact_score": 745.32,
      "dollar_cost_est_on_demand": 728.48,
      "first_seen": "2024-01-01T10:00:00Z",
      "last_seen": "2024-01-31T23:59:59Z",
      "referenced_tables": [
        {"project_id": "my-project", "dataset_id": "analytics", "table_id": "events", "region": "US"}
      ],
      "smart_tuning_eligible": true,
      "eligibility_basis": "stable",
      "smart_tuning_reasons": [],
      "impacted_users": []
    }
  ],
  "summary": {
    "total_candidates": 42,
    "smart_tuning_eligible_count": 15,
    "preview_eligible_count": 3,
    "cross_project_ineligible_count": 5,
    "region_mismatch_count": 2,
    "total_bytes_billed": 12345678901234,
    "estimated_monthly_cost": 1234.56
  }
}
```

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Error (invalid parameters, query failure) |
| 2 | Partial success (some queries failed) |

---

## Command: `report`

Generate cost optimization report for specific query candidates.

### Signature

```bash
bq-automv report [OPTIONS] QUERY_HASH...
```

### Positional Arguments

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `QUERY_HASH` | STRING | Yes | One or more query hashes to report on |

### Options

| Option | Type | Required | Default | Description |
|--------|------|----------|---------|-------------|
| `--format` | FORMAT | No | `markdown` | Output format: `markdown`, `json`, `csv` |
| `--price-per-tib` | FLOAT | No | `6.25` | BigQuery on-demand pricing per TiB |
| `--from-file` | PATH | No | - | Read query hashes from file (one per line) |

### Output (Markdown)

```markdown
# Cost Optimization Report

Generated: 2024-01-31 12:00:00 UTC

## Summary

| Metric | Value |
|--------|-------|
| Queries Analyzed | 5 |
| Smart Tuning Eligible | 3 |
| Total Historical Spend | $1,234.56 |
| Projected Monthly Spend | $1,500.00 |
| Estimated Monthly Savings | $900.00 (60%) |

## Query Details

### abc123...

| Metric | Value |
|--------|-------|
| Execution Count | 1,523 |
| Historical Spend | $456.78 |
| Projected Monthly | $500.00 |
| Smart Tuning Eligible | Yes |
| Est. Monthly Savings | $350.00 (70%) |

**Query:**
```sql
SELECT COUNT(*) FROM analytics.events ...
```

**Referenced Tables:**
- `my-project.analytics.events`
```

---

## Command: `smart-tuning-check`

Check if queries are eligible for BigQuery Smart Tuning.

### Signature

```bash
bq-automv smart-tuning-check [OPTIONS] (QUERY_HASH | --sql STRING)
```

### Input Options

| Option | Type | Required | Description |
|--------|------|----------|-------------|
| `QUERY_HASH` | STRING | Yes* | Query hash to check (if not using --sql) |
| `--sql` | STRING | Yes* | Direct SQL string to analyze |
| `--from-file` | PATH | No | Read SQL from file |

### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--verbose` | BOOL | `false` | Show detailed analysis |

### Output (JSON)

```json
{
  "meta": {
    "version": "1.0.0",
    "generated_at": "2024-01-31T12:00:00Z",
    "command": "smart-tuning-check",
    "rulebook_version": "v1.0",
    "enable_preview_eligibility": false
  },
  "query_hash": "abc123...",
  "eligible": true,
  "eligibility_basis": "stable",
  "disqualification_reasons": [],
  "unsupported_features": [],
  "aggregation_functions": ["COUNT", "SUM"],
  "join_types": ["INNER"],
  "has_ctes": false,
  "cross_project_references": false,
  "region_mismatch": false,
  "liftable_columns": ["store_id", "DATE_TRUNC(transaction_date, MONTH)"],
  "non_liftable_filters": ["region = 'US'"],
  "recommended_mv": {
    "select": ["DATE_TRUNC(transaction_date, MONTH) AS month", "store_id", "SUM(amount)"],
    "where": ["region = 'US'"],
    "group_by": ["DATE_TRUNC(transaction_date, MONTH)", "store_id"]
  },
  "synthesis_audit": {
    "sample_size_k": 20,
    "sample_job_ids": ["job1", "job2", "..."],
    "dropped_predicates": [],
    "warnings": []
  }
}
```

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Eligible for Smart Tuning |
| 1 | Not eligible |
| 2 | Error (invalid SQL, parse failure) |

---

## Command: `generate-mv`

Generate and deploy materialized views for eligible queries.

### Signature

```bash
bq-automv generate-mv [OPTIONS] QUERY_HASH...
```

### Positional Arguments

| Argument | Type | Required | Description |
|----------|------|----------|-------------|
| `QUERY_HASH` | STRING | Yes | One or more query hashes to generate MVs for |

### Options

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--mv-prefix` | STRING | `automv_` | Prefix for generated MV names |
| `--name-scheme-version` | STRING | `v1` | Naming scheme version (for future changes) |
| `--refresh-interval-minutes` | INT | `60` | MV refresh interval |
| `--no-refresh` | BOOL | `false` | Disable automatic refresh |
| `--replace` | BOOL | `false` | Drop and recreate existing MVs |
| `--enable-auto-cleanup` | BOOL | `false` | Enable automatic cleanup of stale MVs |
| `--from-file` | PATH | - | Read query hashes from file |

### Safety Flags

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `--dry-run` | BOOL | `false` | Print DDL without executing |
| `--yes` / `-y` | BOOL | `false` | Skip confirmation prompt |

### Output (Dry Run)

```sql
-- Materialized View for query family: abc123...
-- MV Name: automv_abc123_def456 (signature_hash for idempotency)
-- Region: US (matches source tables)
-- Rulebook Version: v1.0
-- Synthesis Version: v1.0
-- Eligibility Basis: stable

CREATE MATERIALIZED VIEW IF NOT EXISTS `my-project.analytics.automv_abc123_def456`
OPTIONS (
  enable_refresh = true,
  refresh_interval_minutes = 60
)
AS
SELECT
  DATE_TRUNC(transaction_date, MONTH) AS month,
  store_id,
  SUM(amount) AS total_amount
FROM `my-project.analytics.transactions`
WHERE region = 'US'
GROUP BY 1, 2;
```

### Output (Execution)

```
Generating materialized views for 2 queries...

✓ Created automv_abc123_def456 in my-project.analytics (region: US)
✗ Failed automv_def789_ghi012: Permission denied

Summary: 1 succeeded, 1 failed, 0 skipped
```

---

## Command: `impact`

Report on materialized view usage and savings.

### Signature

```bash
bq-automv impact [OPTIONS]
```

### Options

| Option | Type | Required | Default | Description |
|--------|------|----------|---------|-------------|
| `--start-date` | DATE | Yes | - | Start of impact analysis period |
| `--end-date` | DATE | No | `today` | End of impact analysis period |
| `--baseline-mode` | ENUM | No | `none` | Baseline mode: `none`, `previous_period`, `explicit_range` |
| `--baseline-start` | DATE | No* | - | Baseline start (required when baseline_mode=explicit_range) |
| `--baseline-end` | DATE | No* | - | Baseline end (required when baseline_mode=explicit_range) |
| `--mv-name` | STRING | No | - | Filter to specific MV name |
| `--format` | FORMAT | No | `markdown` | Output format |

### Output (Markdown)

```markdown
# Materialized View Impact Report

Period: 2024-01-01 to 2024-01-31

## Summary

| Metric | Value |
|--------|-------|
| Active MVs | 5 |
| Queries Accelerated | 1,234 |
| Bytes Saved | 123.45 GB |
| Slot MS Saved | 987,654,321 |
| Dollars Saved | $123.45 |

## Per-MV Breakdown

### automv_abc123

| Metric | Baseline | Current | Savings |
|--------|----------|---------|---------|
| Avg Bytes/Query | 1.2 GB | 150 MB | 1.05 GB (87.5%) |
| Avg Slot MS/Query | 45,000 | 5,000 | 40,000 (88.9%) |
| Execution Count | 500 | 500 | - |
| Smart Tuning Usage | - | 423 (84.6%) | - |
| **Dollar Savings** | - | - | **$67.50** |

**Status**: ACTIVE

---

## Unused Materialized Views

The following MVs had no Smart Tuning usage detected:

| MV Name | Created | Recommendation |
|---------|---------|----------------|
| automv_unused1 | 2024-01-01 | Review or drop |
| automv_unused2 | 2024-01-15 | Review or drop |
```

---

## Output Formats

### JSON Schema

All `--json` output follows this structure:

```json
{
  "meta": {
    "version": "1.0.0",
    "generated_at": "2024-01-31T12:00:00Z",
    "command": "analyze"
  },
  "data": { /* command-specific data */ },
  "errors": []  // Only present on partial failures
}
```

### CSV Format

For `analyze` and `report` commands, CSV output includes headers:

```csv
query_hash,execution_count,total_bytes_billed,smart_tuning_eligible,estimated_savings
abc123...,1523,123456789012,true,350.00
```

---

## Error Messages

All errors are written to stderr with structured context:

```json
{
  "error": "BigQueryError",
  "message": "Permission denied: BigQuery BigQuery: Permission denied on table ...",
  "context": {
    "query_hash": "abc123...",
    "project": "my-project",
    "dataset": "analytics"
  },
  "suggestion": "Ensure the service account has bigquery.tables.create permission"
}
```

Common errors:

| Error | Cause | Suggestion |
|-------|-------|------------|
| `InvalidArgumentError` | Invalid date format | Use YYYY-MM-DD format |
| `PermissionDenied` | Missing IAM permissions | Grant BigQuery admin/editor |
| `NotFound` | Dataset doesn't exist | Create target dataset first |
| `ParseError` | Invalid SQL | Check query syntax with --sql flag |

---

## Configuration File

Optional `pyproject.toml` configuration:

```toml
[tool.bq-automv]
project = "my-project"
dataset = "analytics"
region = "region-us"
default_refresh_minutes = 60
price_per_tib = 6.25
slot_weight = 0.25
slot_ms_per_tib_equivalent = 3.6e9
sample_size_k = 20
enable_preview_eligibility = false
```

---

## Examples

### Analyze last 30 days with custom impact scoring

```bash
bq-automv analyze \
  --start-date 2024-01-01 \
  --end-date 2024-01-31 \
  --min-executions 50 \
  --price-per-tib 6.25 \
  --slot-weight 0.25 \
  --output results.json
```

### Analyze with preview feature eligibility

```bash
bq-automv analyze \
  --start-date 2024-01-01 \
  --end-date 2024-01-31 \
  --enable-preview-eligibility \
  --region region-us \
  --dataset analytics
```

### Check Smart Tuning eligibility with verbose output

```bash
bq-automv smart-tuning-check --sql "SELECT COUNT(*) FROM my_table" --verbose
```

### Generate MVs with dry run

```bash
bq-automv generate-mv \
  --from-file candidates.txt \
  --dry-run \
  --refresh-interval-minutes 120 \
  --mv-prefix mymv_
```

### Deploy MVs with replace mode

```bash
bq-automv generate-mv abc123... def456... \
  --replace \
  --yes
```

### Check impact with baseline comparison

```bash
bq-automv impact \
  --start-date 2024-01-01 \
  --baseline-mode previous_period \
  --format markdown
```

### Check impact with explicit baseline

```bash
bq-automv impact \
  --start-date 2024-01-01 \
  --baseline-mode explicit_range \
  --baseline-start 2023-12-01 \
  --baseline-end 2023-12-31 \
  --format json
```
