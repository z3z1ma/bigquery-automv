# BigQuery AutoMV

BigQuery materialized view automation tool - analyzes query history to identify candidates for BigQuery smart tuning and automatically creates materialized views.

## Features

- **Query Analysis**: Analyze INFORMATION_SCHEMA.JOBS to find repetitive queries that benefit from materialization
- **Smart Tuning Integration**: Generate MVs that BigQuery can automatically use for query acceleration
- **Label Management**: Track managed MVs with standardized labels
- **Usage Statistics**: Monitor MV usage and effectiveness over time
- **Agent-Friendly CLI**: JSON output mode and non-interactive operation for automation

## Installation

```bash
pip install bigquery-automv
```

Or using uv:

```bash
uv pip install bigquery-automv
```

## Quick Start

```bash
# 1. Discover MV candidates from query history
bq-automv candidates discover \
  --project my-project \
  --start-date 2025-01-01 \
  --end-date 2025-01-07 \
  -o candidates.json

# 2. Review candidates
bq-automv candidates list --input candidates.json

# 3. Create a plan
bq-automv mv plan \
  --input candidates.json \
  --project my-project \
  --dataset my_dataset \
  -o plan.json

# 4. Review the plan
cat plan.json | jq '.summary'

# 5. Apply the plan
bq-automv mv apply \
  --plan plan.json \
  --project my-project \
  --dataset my_dataset

# 6. Check MV status
bq-automv mv list --project my-project --managed-only
bq-automv mv stats my_dataset.automv_abc123 --project my-project --days 7
```

## CLI Reference

### Global Options

| Option | Description |
|--------|-------------|
| `--project` | GCP project ID (also set via `GOOGLE_CLOUD_PROJECT` env var) |
| `--region` | BigQuery region (default: `US`) |
| `--dataset` | Default dataset for MV operations |
| `--dry-run` | Show what would be done without making changes |
| `--verbose, -v` | Enable verbose logging |
| `--json` | Enable JSON envelope output (agent-friendly) |
| `--interactive / --non-interactive` | Force interactive/non-interactive mode |

### Candidates Commands

#### `bq-automv candidates discover`

Analyze query history to discover MV candidates.

```bash
bq-automv candidates discover \
  --project PROJECT \
  --start-date YYYY-MM-DD \
  --end-date YYYY-MM-DD \
  --days N \
  -o OUTPUT_FILE
```

**Options:**
- `--project`: BigQuery project ID
- `--start-date`: Start of analysis period
- `--end-date`: End of analysis period
- `--days`: Number of days to look back (alternative to start/end dates)
- `-o, --output`: Output file for candidates (JSON or CSV)

#### `bq-automv candidates list`

List discovered candidates from a file.

```bash
bq-automv candidates list --input FILE [--format FORMAT]
```

**Options:**
- `--input`: Input candidates file (JSON)
- `--format`: Output format (default: `table`, options: `table`, `json`)

#### `bq-automv candidates get HASH`

Get details of a specific candidate by query hash.

```bash
bq-automv candidates get HASH --input FILE
```

**Options:**
- `HASH`: Query hash (prefix match supported)
- `--input`: Input candidates file (JSON)

#### `bq-automv candidates prune`

Filter candidates by impact/execution thresholds.

```bash
bq-automv candidates prune \
  --input FILE \
  --min-savings-mb MB \
  --min-executions N \
  -o OUTPUT_FILE
```

**Options:**
- `--input`: Input candidates file (JSON)
- `--min-savings-mb`: Minimum estimated savings in MB
- `--min-executions`: Minimum execution count
- `-o, --output`: Output file for pruned candidates

### MV Commands

#### `bq-automv mv plan`

Generate a materialized view creation plan from candidates.

```bash
bq-automv mv plan \
  --input FILE \
  --project PROJECT \
  --dataset DATASET \
  -o OUTPUT_FILE
```

**Options:**
- `--input`: Input candidates file (JSON)
- `--project`: Target project ID
- `--dataset`: Target dataset ID
- `-o, --output`: Output plan file (JSON)

#### `bq-automv mv apply`

Execute a materialized view plan.

```bash
bq-automv mv apply \
  --plan FILE \
  --project PROJECT \
  --dataset DATASET \
  [--filter ACTIONS]
```

**Options:**
- `--plan`: Plan file (JSON)
- `--project`: Target project ID
- `--dataset`: Target dataset ID
- `--filter`: Filter actions (comma-separated: `create`, `replace`, `keep`)

#### `bq-automv mv list`

List materialized views.

```bash
bq-automv mv list --project PROJECT [--dataset DATASET] [--managed-only]
```

**Options:**
- `--project`: Project ID
- `--dataset`: Filter by dataset (optional)
- `--managed-only`: Only show automv-managed MVs (with `automv_managed=true` label)

#### `bq-automv mv get MV_NAME`

Get details of a specific materialized view.

```bash
bq-automv mv get MV_NAME --project PROJECT
```

**Options:**
- `MV_NAME`: Materialized view name (e.g., `dataset.mv_name`)
- `--project`: Project ID

#### `bq-automv mv drop MV_NAME`

Drop a materialized view.

```bash
bq-automv mv drop MV_NAME --project PROJECT
```

**Options:**
- `MV_NAME`: Materialized view name to drop
- `--project`: Project ID

#### `bq-automv mv gc`

Garbage collect unused materialized views.

```bash
bq-automv mv gc --project PROJECT [--days-idle N] [--drop]
```

**Options:**
- `--project`: Project ID
- `--dataset`: Filter by dataset (optional)
- `--days-idle`: Days idle before considering for GC (default: `30`)
- `--drop`: Actually drop the MVs (default: dry-run)

#### `bq-automv mv stats MV_NAME`

Show usage statistics for a materialized view.

```bash
bq-automv mv stats MV_NAME --project PROJECT --days N
```

**Options:**
- `MV_NAME`: Materialized view name
- `--project`: Project ID
- `--days`: Number of days to look back (default: `7`)

### Spec Command

#### `bq-automv spec`

Emit machine-readable CLI specification for agent discoverability.

```bash
bq-automv spec
```

Always outputs JSON, regardless of `--json` flag.

## JSON Output Mode

Enable `--json` flag for agent-friendly output:

```bash
bq-automv --json candidates list --input candidates.json
```

**Response format (success):**
```json
{
  "schema_version": "1.0.0",
  "ok": true,
  "command": "candidates.list",
  "target": "my-project",
  "data": {
    "candidates": [...],
    "count": 10
  },
  "warnings": [],
  "meta": {
    "tool_version": "1.5.0",
    "timestamp": "2025-01-04T12:00:00Z"
  }
}
```

**Response format (error):**
```json
{
  "schema_version": "1.0.0",
  "ok": false,
  "error": {
    "type": "BigQueryError",
    "code": "NOT_FOUND",
    "message": "Dataset not found",
    "remediation": "Create the dataset first"
  },
  "command": "mv.plan",
  "target": "my-project.my_dataset"
}
```

## MV Labels

All auto-created MVs are labeled with:

| Label | Value | Purpose |
|-------|-------|---------|
| `automv_managed` | "true" | Identifies tool-managed MVs |
| `automv_family_hash` | hash | Query family (change detection) |
| `automv_signature_hash` | hash | Specific query signature |
| `automv_version` | "1.5.0" | CLI version that created MV |

Used by:
- `mv list --managed-only` to filter managed MVs
- `mv plan` to detect query changes (replace vs keep)
- `mv gc` to identify candidates for cleanup

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Any failure (error details in JSON envelope or stderr) |

## Migration from v0.x

See [MIGRATION.md](docs/MIGRATION.md) for detailed migration guide from the previous CLI version.

## Development

```bash
# Install development dependencies
uv sync

# Run tests
make test

# Format code
make style

# Check linting
make check
```

## License

MIT License - see LICENSE file for details.
