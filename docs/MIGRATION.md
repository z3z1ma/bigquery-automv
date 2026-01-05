# Migration Guide: v0.x to v1.5.0

This guide helps you migrate from the previous CLI version (v0.x) to the new agent-friendly CLI (v1.5.0).

## Breaking Changes

Version 1.5.0 is a **breaking change** release. The old command structure has been completely replaced with a new object+lifecycle design.

| Old Command (v0.x) | New Command (v1.5.0) | Notes |
|---------------------|----------------------|-------|
| `analyze` | `candidates discover` | Same functionality, new name |
| `generate-mv` | `mv plan` + `mv apply` | Split into plan/apply phases |
| `impact` | `mv stats` | More detailed statistics |
| `report` | `mv list` + `mv stats` | Split into listing and stats |
| `smart-tuning-check` | `candidates discover` | Integrated into discover |
| `run` | *(removed)* | Use individual commands |

## Command Changes

### analyze → candidates discover

**Before (v0.x):**
```bash
bq-automv analyze --project-id my-project --days 7 -o candidates.json
```

**After (v1.5.0):**
```bash
bq-automv candidates discover --project my-project --days 7 -o candidates.json
```

**Key differences:**
- `--project-id` → `--project`
- Global options now go before the command name
- Output format unchanged

### generate-mv → mv plan + mv apply

**Before (v0.x):**
```bash
bq-automv generate-mv \
  --project-id my-project \
  --dataset-id my_dataset \
  --candidates-file candidates.json \
  --dry-run
```

**After (v1.5.0):**
```bash
# Step 1: Create a plan
bq-automv mv plan \
  --project my-project \
  --dataset my_dataset \
  --input candidates.json \
  -o plan.json

# Step 2: Review the plan
cat plan.json | jq '.summary'

# Step 3: Apply the plan
bq-automv mv apply \
  --plan plan.json \
  --project my-project \
  --dataset my_dataset
```

**Key differences:**
- Two-step process: plan first, then apply
- Review phase between plan and apply
- `--candidates-file` → `--input`
- Use global `--dry-run` instead of command-specific flag

### impact → mv stats

**Before (v0.x):**
```bash
bq-automv impact --project-id my-project --mv-name my_dataset.mv_name
```

**After (v1.5.0):**
```bash
bq-automv mv stats my_dataset.mv_name --project my-project --days 7
```

**Key differences:**
- MV name is now a positional argument
- `--project-id` → `--project`
- Added `--days` parameter (default: 7)

### report → mv list

**Before (v0.x):**
```bash
bq-automv report --project-id my-project
```

**After (v1.5.0):**
```bash
bq-automv mv list --project my-project --managed-only
```

**Key differences:**
- New `--managed-only` flag to show only automv-managed MVs
- Add `--dataset` to filter by dataset

## Global Options

New global options that apply to all commands:

| Option | Description | Example |
|--------|-------------|---------|
| `--json` | Enable JSON envelope output | `bq-automv --json mv list ...` |
| `--dry-run` | Global dry-run mode | `bq-automv --dry-run mv apply ...` |
| `--interactive / --non-interactive` | Force interactivity | `bq-automv --non-interactive mv drop ...` |
| `--verbose, -v` | Enable verbose logging | `bq-automv -v candidates discover ...` |

Global options must come **before** the command name:

```bash
# Correct
bq-automv --json candidates list --input candidates.json

# Incorrect
bq-automv candidates list --json --input candidates.json
```

## JSON Output Mode

Version 1.5.0 introduces standardized JSON envelope output for agent-friendly operation:

```bash
bq-automv --json mv list --project my-project
```

**Success response:**
```json
{
  "schema_version": "1.0.0",
  "ok": true,
  "command": "mv.list",
  "target": "my-project",
  "data": {
    "mvs": [...],
    "count": 5
  },
  "warnings": [],
  "meta": {
    "tool_version": "1.5.0",
    "timestamp": "2025-01-04T12:00:00Z"
  }
}
```

**Error response:**
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
  "command": "mv.list",
  "target": "my-project"
}
```

**Key rules:**
- stdout = JSON only when `--json` is set
- stderr = logs, warnings, errors (always)
- Exit codes: `0` = success, `1` = any failure

## Non-Interactive Mode

The new CLI is designed to work without prompts in automated environments:

```bash
# Non-interactive mode (no prompts, uses defaults)
bq-automv --non-interactive mv drop my_dataset.old_mv --project my-project
```

In non-interactive mode:
- No confirmation prompts
- Uses sensible defaults for destructive actions
- Safe for automation and CI/CD pipelines

## MV Labels

Version 1.5.0 introduces standardized labels for all auto-created MVs:

| Label | Value | Purpose |
|-------|-------|---------|
| `automv_managed` | "true" | Identifies tool-managed MVs |
| `automv_family_hash` | hash | Query family (change detection) |
| `automv_signature_hash` | hash | Specific query signature |
| `automv_version` | "1.5.0" | CLI version that created MV |

**Finding managed MVs:**
```bash
bq-automv mv list --project my-project --managed-only
```

**Filtering by labels (BigQuery SQL):**
```sql
SELECT *
FROM `my-project.my_dataset.INFORMATION_SCHEMA.TABLES`
WHERE option_value LIKE '%automv_managed=true%'
```

## Example Workflows

### Before (v0.x) - Single Interactive Command

```bash
bq-automv run \
  --project-id my-project \
  --dataset-id my_dataset \
  --days 7
# Interactive prompts for MV selection
```

### After (v1.5.0) - Multi-Step Agent-Friendly Workflow

```bash
# 1. Discover candidates
bq-automv --json candidates discover \
  --project my-project \
  --days 7 \
  -o candidates.json

# 2. Parse and filter candidates (jq/Python/agent)
jq '.candidates[] | select(.estimated_savings_mb > 100)' candidates.json

# 3. Create plan
bq-automv --json mv plan \
  --input candidates.json \
  --project my-project \
  --dataset my_dataset \
  -o plan.json

# 4. Review plan
jq '.summary' plan.json

# 5. Apply plan (filter to only create, not replace)
bq-automv --json mv apply \
  --plan plan.json \
  --project my-project \
  --dataset my_dataset \
  --filter create

# 6. Verify
bq-automv --json mv list --project my-project --managed-only
bq-automv --json mv stats my_dataset.automv_abc123 --project my-project --days 7
```

## Removed Features

The following features have been removed:

### `run` Command

The `run` command has been removed. Use the individual commands instead:

- Use `candidates discover` to analyze queries
- Use `mv plan` + `mv apply` to create MVs

### Interactive Selection

The new CLI does not include interactive selection of MVs. Instead:

1. Use `candidates prune` to filter by impact
2. Review the plan file before applying
3. Use `--filter` with `mv apply` to select specific actions

### Some Old Options

- `--enable-preview-eligibility`: Removed (simplified)
- `--include-user-email`: Removed (privacy-focused)
- Local `--dry-run` on `generate-mv`: Now a global flag

## Rollback

If you need to rollback to v0.x after upgrading:

```bash
pip uninstall bigquery-automv
pip install bigquery-automv==0.1.0
```

Or using uv:

```bash
uv pip uninstall bigquery-automv
uv pip install bigquery-automv==0.1.0
```

## Getting Help

For more information:

- See [README.md](../README.md) for full CLI reference
- Run `bq-automv spec` for machine-readable CLI specification
- Run `bq-automv --help` for help
- Run `bq-automv COMMAND --help` for command-specific help

## Support

If you encounter issues during migration:

1. Check the JSON error output for remediation suggestions
2. Review this migration guide
3. File an issue on GitHub with the error code and message
