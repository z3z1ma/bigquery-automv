# BigQuery AutoMV Quickstart Guide

This guide will help you get started with `bq-automv`, a tool for automatically creating materialized views in BigQuery based on query pattern analysis.

## Prerequisites

- Python 3.14+
- Google Cloud project with BigQuery enabled
- BigQuery IAM permissions:
  - `bigquery.jobs.create`
  - `bigquery.tables.read` / `bigquery.tables.getData`
  - `bigquery.tables.update` (for MV creation)

## Installation

```bash
# Clone the repository
git clone https://github.com/z3z1ma/bigquery-automv.git
cd bigquery-automv

# Install with uv (recommended)
uv sync

# Or install with pip
pip install -e .
```

## Configuration

Set your Google Cloud project:

```bash
export GOOGLE_CLOUD_PROJECT="your-project-id"
export BQ_AUTOMV_REGION="region-us"  # or your region
export BQ_AUTOMV_DATASET="your_dataset"  # target dataset for MVs
```

## Workflow 1: Analyze Query Patterns

Identify expensive, repetitive queries that benefit from materialized views.

```bash
bq-automv analyze \
  --start-date 2024-01-01 \
  --end-date 2024-01-31 \
  --min-executions 50 \
  --min-bytes 1073741824 \
  --output results.json
```

**Parameters:**
- `--start-date` / `--end-date`: Analysis window (YYYY-MM-DD)
- `--min-executions`: Minimum execution count (default: 10)
- `--min-bytes`: Minimum bytes processed (default: 1GB)
- `--max-families`: Maximum number of query families (default: 100)
- `--output`: Write results to file (JSON/CSV)

**Output includes:**
- Query hash (family identifier)
- Execution count
- Total bytes processed
- Smart Tuning eligibility
- Impact score (estimated savings)

## Workflow 2: Check Smart Tuning Eligibility

Verify if a query is eligible for BigQuery Smart Tuning automatic rerouting.

```bash
# Check SQL directly
bq-automv smart-tuning-check \
  --sql "SELECT user_id, COUNT(*) FROM \`project.dataset.table\` GROUP BY user_id"

# Check from file
bq-automv smart-tuning-check --from-file query.sql

# JSON output
bq-automv smart-tuning-check --sql "SELECT..." --json

# Verbose output
bq-automv smart-tuning-check --sql "SELECT..." --verbose
```

**Exit codes:**
- `0`: Eligible for Smart Tuning
- `1`: Fatal error (parse failure, invalid input)
- `2`: Not eligible for Smart Tuning

## Workflow 3: Generate Cost Optimization Report

Generate detailed cost analysis for specific query candidates.

```bash
# Report on query hash
bq-automv report abc123def456

# Report on multiple hashes
bq-automv report abc123 def456 ghi789

# Read hashes from file
bq-automv report --from-file hashes.txt

# Custom pricing and JSON output
bq-automv report abc123 --price-per-tib 5.00 --format json

# Specify date range
bq-automv report abc123 --start-date 2024-01-01 --end-date 2024-01-31
```

**Output formats:** markdown, json, csv

## Workflow 4: Generate Materialized Views

Create and deploy materialized views for eligible queries.

```bash
# Dry run (preview DDL)
bq-automv generate-mv abc123 --dry-run

# Generate and deploy MV
bq-automv generate-mv abc123 --yes

# Generate multiple MVs
bq-automv generate-mv abc123 def456 --yes

# Read hashes from file
bq-automv generate-mv --from-file hashes.txt --yes

# Replace existing MV
bq-automv generate-mv abc123 --replace --yes

# Custom refresh interval
bq-automv generate-mv abc123 --refresh-interval-minutes 30 --yes
```

**Exit codes:**
- `0`: Success
- `1`: Fatal error
- `2`: Partial success (some MVs failed)

## Workflow 5: Monitor MV Impact

Track usage and cost savings of deployed materialized views.

```bash
# Basic impact report
bq-automv impact --start-date 2024-02-01 --end-date 2024-02-28

# With baseline comparison
bq-automv impact \
  --start-date 2024-02-01 \
  --end-date 2024-02-28 \
  --baseline-mode previous_period

# Specific MV
bq-automv impact --mv-name automv_abc123 --start-date 2024-02-01

# JSON output
bq-automv impact --start-date 2024-02-01 --format json

# Write to file
bq-automv impact --start-date 2024-02-01 --output report.md
```

**Output includes:**
- Total bytes saved
- Total slot milliseconds saved
- Dollars saved (on-demand equivalent)
- Per-MV breakdown
- Unused MVs

## Workflow 6: Full End-to-End Pipeline

Complete workflow from analysis to monitoring.

```bash
# Step 1: Analyze query patterns
bq-automv analyze \
  --start-date 2024-01-01 \
  --end-date 2024-01-31 \
  --min-executions 50 \
  --min-bytes 1073741824 \
  --output candidates.json

# Step 2: Extract top candidates (jq example)
jq -r '.candidates[] | select(.smart_tuning_eligible == true) | .query_hash' candidates.json > top_hashes.txt

# Step 3: Verify eligibility for top candidates
while read hash; do
  bq-automv smart-tuning-check --sql "$(jq -r --arg h "$hash" '.candidates[] | select(.query_hash == $h) | .representative_query' candidates.json)" --json
done < top_hashes.txt > eligibility_results.json

# Step 4: Generate MVs for eligible queries
bq-automv generate-mv --from-file top_hashes.txt --dry-run

# Review the DDL output, then deploy:
bq-automv generate-mv --from-file top_hashes.txt --yes

# Step 5: Monitor impact after 30 days
bq-automv impact \
  --start-date 2024-02-01 \
  --end-date 2024-02-28 \
  --baseline-mode previous_period \
  --format markdown \
  --output impact_report.md
```

## Common Options

All commands support these global options:

```bash
--project PROJECT_ID          # Google Cloud project
--region REGION                # BigQuery region (default: region-us)
--dataset DATASET              # Target dataset for MVs
--dry-run                      # Preview changes without executing
--verbose, -v                  # Enable verbose logging
--json                         # Output in JSON format
--enable-preview-eligibility   # Enable preview eligibility features
--include-user-email           # Include user email in results
--include-query-text           # Include full query text in results
```

## Exit Codes

All commands use consistent exit codes:

- `0`: Success
- `1`: Fatal error (validation failure, permission denied, etc.)
- `2`: Partial success or expected failure (e.g., query not eligible, no usage found)

## Error Handling

Errors are formatted consistently:

**JSON mode (`--json`):**
```json
{
  "error": "AnalyzeError",
  "message": "Project ID is required",
  "suggestion": "Set --project or GOOGLE_CLOUD_PROJECT environment variable"
}
```

**Text mode:**
```
Error: Project ID is required
Suggestion: Set --project or GOOGLE_CLOUD_PROJECT environment variable
```

## Tips and Best Practices

1. **Start with analyze**: Always run analyze first to identify candidates
2. **Use dry-run**: Preview MV DDL before deploying
3. **Monitor impact**: Track savings with the impact command
4. **Clean up unused MVs**: Use impact report to identify unused MVs
5. **Adjust thresholds**: Tune `--min-executions` and `--min-bytes` for your workload

## Troubleshooting

**Permission denied:**
```bash
# Check IAM permissions
gcloud projects get-iam-policy PROJECT_ID

# Ensure you have BigQuery Admin or appropriate roles
```

**No candidates found:**
- Lower `--min-executions` threshold
- Lower `--min-bytes` threshold
- Expand date range
- Check region matches your query location

**MV not used by Smart Tuning:**
- Verify MV and base tables are in the same region
- Check MV eligibility with `smart-tuning-check`
- Review query patterns match MV structure

## Next Steps

- See [spec.md](./spec.md) for detailed specifications
- See [plan.md](./plan.md) for implementation details
- See [tasks.md](./tasks.md) for task breakdown

## Version

Check your version:

```bash
bq-automv --version
# Output: bq-automv 0.1.0
```
