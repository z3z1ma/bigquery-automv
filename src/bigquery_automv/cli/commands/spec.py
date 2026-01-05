"""Spec command - emit machine-readable CLI specification for agent discoverability."""

import json

import click

from bigquery_automv.cli.app import __version__


@click.command("spec")
def spec_command() -> None:
    """Emit machine-readable CLI specification.

    This command outputs a JSON specification of the CLI's commands,
    parameters, and behavior. Agents can use this to understand the
    CLI's capabilities without parsing help text.

    Output is always JSON regardless of --json flag.
    """
    spec = {
        "name": "bq-automv",
        "version": __version__,
        "description": "BigQuery materialized view automation tool",
        "schema_version": "1.0.0",
        "output_format": {
            "success": {
                "schema_version": "string - Envelope format version",
                "ok": "boolean - Always true for success responses",
                "command": "string - Command name (e.g., 'candidates.discover')",
                "target": "string - Target identifier (e.g., 'project.dataset')",
                "data": "object - Command result data",
                "warnings": "array<string> - Accumulated warnings",
                "meta": "object - Metadata (tool_version, timestamp, etc.)",
            },
            "error": {
                "schema_version": "string - Envelope format version",
                "ok": "boolean - Always false for error responses",
                "error": {
                    "type": "string - Error type (e.g., 'BigQueryError')",
                    "code": "string - Error code (e.g., 'DATASET_NOT_FOUND')",
                    "message": "string - Human-readable error message",
                    "remediation": "string (optional) - Suggestion for fixing the error",
                },
                "command": "string - Command name",
                "target": "string - Target identifier",
            },
        },
        "global_options": {
            "--project": "GCP project ID (env: GOOGLE_CLOUD_PROJECT, BQ_AUTOMV_PROJECT)",
            "--region": "BigQuery region (default: US, env: BQ_AUTOMV_REGION)",
            "--dataset": "BigQuery dataset (env: BQ_AUTOMV_DATASET)",
            "--dry-run": "Show what would be done without making changes",
            "--verbose, -v": "Verbose logging",
            "--json": "Enable JSON envelope output (stdout = JSON only, stderr = logs)",
            "--interactive/--non-interactive": "Force interactive/non-interactive mode",
        },
        "commands": {
            "candidates": {
                "description": "Manage query candidates for materialized views",
                "subcommands": {
                    "discover": {
                        "description": "Analyze query history to discover MV candidates",
                        "options": {
                            "--start-date": "Start of analysis window (YYYY-MM-DD, required)",
                            "--end-date": "End of analysis window (YYYY-MM-DD, default: today)",
                            "--min-executions": "Minimum execution count (default: 10)",
                            "--min-bytes": "Minimum bytes processed threshold (default: 1GB)",
                            "--min-slot-ms": "Minimum slot milliseconds (default: 0)",
                            "--max-families": "Maximum query families to return (default: 100)",
                            "--output": "Write results to file (.json or .csv)",
                            "--persist": "Persist candidates to BigQuery table",
                        },
                        "output": {
                            "candidates": "array of candidate objects",
                            "metrics": "analysis metrics",
                            "analysis_period": "date range information",
                        },
                    },
                    "list": {
                        "description": "List discovered candidates from file or table",
                        "options": {
                            "--input, -i": "Input candidates file (required if not persisting)",
                            "--format": "Output format: table or json (default: auto-detect)",
                        },
                    },
                    "get": {
                        "description": "Get details of a specific candidate",
                        "arguments": {
                            "query_hash": "Query hash (prefix match supported)",
                        },
                        "options": {
                            "--input, -i": "Input candidates file",
                        },
                    },
                    "prune": {
                        "description": "Filter candidates by impact threshold",
                        "options": {
                            "--input, -i": "Input candidates file (required)",
                            "--min-impact": "Minimum impact score threshold (default: 10)",
                            "--output, -o": "Output file for pruned candidates (required)",
                        },
                    },
                },
            },
            "mv": {
                "description": "Manage materialized views",
                "subcommands": {
                    "plan": {
                        "description": "Generate a materialized view plan from candidates",
                        "options": {
                            "--input, -i": "Candidates file (required)",
                            "--project-id": "BigQuery project ID (required)",
                            "--dataset-id": "Target dataset for MVs (required)",
                            "--output, -o": "Output plan file (required)",
                            "--mv-prefix": "Prefix for MV names (default: 'automv_')",
                            "--refresh-interval-minutes": "MV refresh interval (default: 60)",
                        },
                        "output": {
                            "actions": "array of action objects (create/replace/keep/skip/drop)",
                            "summary": "summary counts by action type",
                        },
                    },
                    "apply": {
                        "description": "Apply a materialized view plan",
                        "options": {
                            "--plan, -p": "Plan file from mv plan (required)",
                            "--project-id": "BigQuery project ID (required)",
                            "--dataset-id": "Target dataset for MVs (required)",
                            "--filter": "Filter actions to apply (e.g., 'create,replace')",
                        },
                        "output": {
                            "created": "array of created MV names",
                            "replaced": "array of replaced MV names",
                            "skipped": "array of skipped MV names",
                            "failed": "array of failed actions with errors",
                        },
                    },
                    "list": {
                        "description": "List materialized views",
                        "options": {
                            "--project-id": "BigQuery project ID (required)",
                            "--dataset-id": "Filter by dataset ID",
                            "--managed-only": "Only show automv-managed MVs",
                        },
                        "output": {
                            "mvs": "array of MV metadata objects",
                            "count": "total count",
                        },
                    },
                    "get": {
                        "description": "Get details of a specific materialized view",
                        "arguments": {
                            "mv_name": "Materialized view name (e.g., dataset.mv_name)",
                        },
                        "options": {
                            "--project-id": "BigQuery project ID (required)",
                        },
                        "output": {
                            "mv": "MV metadata object",
                        },
                    },
                    "drop": {
                        "description": "Drop a materialized view",
                        "arguments": {
                            "mv_name": "Materialized view name",
                        },
                        "options": {
                            "--project-id": "BigQuery project ID (required)",
                        },
                    },
                    "gc": {
                        "description": "Garbage collect unused materialized views",
                        "options": {
                            "--project-id": "BigQuery project ID (required)",
                            "--dataset-id": "Filter by dataset ID",
                            "--days-idle": "Days idle before considering for GC (default: 30)",
                            "--drop": "Actually drop the MVs (default: dry-run)",
                        },
                        "output": {
                            "unused_mvs": "array of unused MV candidates",
                            "dropped": "array of dropped MV names (if --drop)",
                        },
                    },
                    "stats": {
                        "description": "Show usage statistics for a materialized view",
                        "arguments": {
                            "mv_name": "Materialized view name",
                        },
                        "options": {
                            "--project-id": "BigQuery project ID (required)",
                            "--days": "Number of days to analyze (default: 7)",
                        },
                        "output": {
                            "mv_name": "Materialized view name",
                            "period_days": "Analysis period",
                            "query_count": "Number of queries using the MV",
                            "total_slot_ms": "Total slot milliseconds",
                            "first_used": "First usage timestamp",
                            "last_used": "Last usage timestamp",
                        },
                    },
                },
            },
        },
        "mv_labels": {
            "automv_managed": "Boolean flag ('true') indicating MV is managed by automv",
            "automv_family_hash": "Hash of query family (for change detection)",
            "automv_signature_hash": "Hash of specific query signature",
            "automv_version": "Version of automv that created the MV",
        },
        "exit_codes": {
            "0": "Success",
            "1": "Error (any failure)",
        },
    }

    print(json.dumps(spec, indent=2))
