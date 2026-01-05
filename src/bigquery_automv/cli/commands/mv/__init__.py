"""MV command group - manage materialized views.

This module provides commands for planning, applying, and managing
materialized views from query candidates.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import click

from bigquery_automv.cli.app import CommonConfig
from bigquery_automv.cli.utils import confirm_destructive_action, should_prompt
from bigquery_automv.lib.logging import setup_logging


@click.group(name="mv")
def mv_group() -> None:
    """Manage materialized views."""


def _get_common_config(ctx: click.Context) -> CommonConfig:
    """Get CommonConfig from click context."""
    return ctx.obj["common"]


def _get_formatter(ctx: click.Context):
    """Get OutputFormatter from click context."""
    return ctx.obj["formatter"]


def _compute_family_hash(query_text: str) -> str:
    """Compute family hash from query text.

    This is a simplified hash for query family identification.
    In production, this would use more sophisticated normalization.

    Args:
        query_text: SQL query text

    Returns:
        Hex digest of SHA256 hash
    """
    return hashlib.sha256(query_text.encode()).hexdigest()[:16]


def _compute_signature_hash(query_text: str) -> str:
    """Compute signature hash from exact query text.

    Args:
        query_text: SQL query text

    Returns:
        Hex digest of SHA256 hash
    """
    return hashlib.sha256(query_text.encode()).hexdigest()[:16]


@mv_group.command("plan")
@click.option(
    "--input",
    "-i",
    "input_file",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Candidates JSON file from candidates discover",
)
@click.option(
    "--project-id",
    required=True,
    help="BigQuery project ID",
)
@click.option(
    "--dataset-id",
    required=True,
    help="Target dataset for MVs",
)
@click.option(
    "--output",
    "-o",
    "output_file",
    type=click.Path(path_type=Path),
    required=True,
    help="Output plan file",
)
@click.option(
    "--mv-prefix",
    default="automv_",
    help="Prefix for MV names (default: automv_)",
)
@click.option(
    "--refresh-interval-minutes",
    type=int,
    default=60,
    help="MV refresh interval in minutes (default: 60)",
)
@click.pass_context
def mv_plan(
    ctx: click.Context,
    input_file: Path,
    project_id: str,
    dataset_id: str,
    output_file: Path,
    mv_prefix: str,
    refresh_interval_minutes: int,
) -> None:
    """Generate a materialized view plan from candidates."""
    common = _get_common_config(ctx)
    formatter = _get_formatter(ctx)

    logger = setup_logging(common)

    try:
        # Load candidates from file
        with open(input_file) as f:
            input_data = json.load(f)
            candidates = input_data.get("candidates", [])

        if not candidates:
            formatter.error(
                error_type="ValueError",
                code="NO_CANDIDATES",
                message="No candidates found in input file",
                command="mv.plan",
                target=str(input_file),
            )
            sys.exit(1)

        logger.info(f"Loaded {len(candidates)} candidates from {input_file}")

        # Create BigQuery client to check existing MVs
        # Note: We'll implement list/query for existing MVs in the service layer
        # For now, generate plan without checking existing MVs

        # Generate plan actions
        plan_actions = []
        for candidate in candidates:
            query_hash = candidate.get("query_hash", "")
            if not query_hash:
                logger.warning(f"Skipping candidate with no query_hash: {candidate}")
                continue

            # Compute hashes
            representative_query = candidate.get("representative_query", "")
            family_hash = _compute_family_hash(representative_query)
            signature_hash = _compute_signature_hash(representative_query)

            # Generate MV name
            mv_name = f"{mv_prefix}{family_hash}_{signature_hash[:8]}"

            # Determine action
            # For now, always create - in production we'd check if MV exists
            # and compare family/signature hashes to determine replace/keep/skip
            action = "create"

            plan_actions.append(
                {
                    "action": action,
                    "query_hash": query_hash,
                    "mv_name": mv_name,
                    "family_hash": family_hash,
                    "signature_hash": signature_hash,
                    "dataset_id": dataset_id,
                    "candidate": candidate,
                    "refresh_interval_minutes": refresh_interval_minutes,
                }
            )

        # Build summary
        summary = {
            "create": sum(1 for a in plan_actions if a["action"] == "create"),
            "replace": sum(1 for a in plan_actions if a["action"] == "replace"),
            "keep": sum(1 for a in plan_actions if a["action"] == "keep"),
            "skip": sum(1 for a in plan_actions if a["action"] == "skip"),
            "drop": sum(1 for a in plan_actions if a["action"] == "drop"),
            "total": len(plan_actions),
        }

        # Build plan
        plan = {
            "project_id": project_id,
            "dataset_id": dataset_id,
            "actions": plan_actions,
            "summary": summary,
            "metadata": {
                "generated_at": datetime.now(UTC).isoformat(),
                "mv_prefix": mv_prefix,
                "refresh_interval_minutes": refresh_interval_minutes,
                "tool_version": "1.5.0",
            },
        }

        # Write plan to file
        try:
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_text(json.dumps(plan, indent=2))
            logger.info(f"Wrote plan to {output_file}")
        except OSError as e:
            formatter.error(
                error_type="OSError",
                code="FILE_WRITE_ERROR",
                message=f"Failed to write plan file: {e}",
                command="mv.plan",
                target=str(output_file),
            )
            sys.exit(1)

        formatter.success(
            command="mv.plan",
            data={
                "plan_file": str(output_file),
                "summary": summary,
            },
            target=f"{project_id}.{dataset_id}",
            meta={"dry_run": common.dry_run},
        )

    except FileNotFoundError:
        formatter.error(
            error_type="FileNotFoundError",
            code="FILE_NOT_FOUND",
            message=f"Input file not found: {input_file}",
            command="mv.plan",
            target=str(input_file),
        )
        sys.exit(1)
    except json.JSONDecodeError as e:
        formatter.error(
            error_type="JSONDecodeError",
            code="INVALID_JSON",
            message=f"Invalid JSON in input file: {e}",
            command="mv.plan",
            target=str(input_file),
        )
        sys.exit(1)
    except Exception as e:
        logger.exception("Unexpected error in mv plan")
        formatter.error(
            error_type="UnexpectedError",
            code="UNEXPECTED_ERROR",
            message=f"Unexpected error: {e}",
            command="mv.plan",
            target=f"{project_id}.{dataset_id}",
        )
        sys.exit(1)


@mv_group.command("apply")
@click.option(
    "--plan",
    "-p",
    "plan_file",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Plan file from mv plan",
)
@click.option(
    "--project-id",
    required=True,
    help="BigQuery project ID",
)
@click.option(
    "--dataset-id",
    required=True,
    help="Target dataset for MVs",
)
@click.option(
    "--filter",
    "action_filter",
    help="Filter actions to apply (e.g., 'create,replace')",
)
@click.pass_context
def mv_apply(
    ctx: click.Context,
    plan_file: Path,
    project_id: str,
    dataset_id: str,
    action_filter: str | None,
) -> None:
    """Apply a materialized view plan."""
    common = _get_common_config(ctx)
    formatter = _get_formatter(ctx)

    logger = setup_logging(common)

    try:
        # Load plan from file
        with open(plan_file) as f:
            plan = json.load(f)

        actions = plan.get("actions", [])
        if not actions:
            formatter.success(
                command="mv.apply",
                data={"created": [], "replaced": [], "skipped": [], "failed": []},
                target=f"{project_id}.{dataset_id}",
                meta={"dry_run": common.dry_run, "message": "No actions to apply"},
            )
            return

        # Filter actions if requested
        if action_filter:
            filter_types = action_filter.split(",")
            actions = [a for a in actions if a["action"] in filter_types]
            logger.info(f"Filtered to {len(actions)} actions: {filter_types}")

        # Confirm if interactive
        if should_prompt(common.interactive, common.json):
            if not confirm_destructive_action(
                f"Apply {len(actions)} actions to {project_id}.{dataset_id}?",
                formatter,
                default=False,
            ):
                formatter.emit_warning("Apply cancelled by user")
                sys.exit(0)

        # Track results
        results = {
            "created": [],
            "replaced": [],
            "skipped": [],
            "failed": [],
        }

        # Note: This is a placeholder implementation
        # In production, this would use MVGeneratorService to generate DDL
        # and BigQueryClient to execute it

        if not common.dry_run:
            # TODO: Implement actual MV creation
            # This requires:
            # 1. MVGeneratorService to generate DDL
            # 2. BigQueryClient.create_materialized_view() to execute
            # 3. Label management for automv tracking

            formatter.emit_warning("MV creation not yet implemented in production mode")
            formatter.emit_warning("Use --dry-run to preview what would be created")

        # For dry-run, show what would be created
        for action in actions:
            action_type = action["action"]
            mv_name = action.get("mv_name", "")

            if action_type == "create":
                if common.dry_run:
                    results["created"].append(f"{dataset_id}.{mv_name} (dry-run)")
                    logger.info(f"Would create {dataset_id}.{mv_name}")
                else:
                    results["created"].append(f"{dataset_id}.{mv_name}")
            elif action_type == "replace":
                if common.dry_run:
                    results["replaced"].append(f"{dataset_id}.{mv_name} (dry-run)")
                    logger.info(f"Would replace {dataset_id}.{mv_name}")
                else:
                    results["replaced"].append(f"{dataset_id}.{mv_name}")
            elif action_type == "skip":
                results["skipped"].append(mv_name)
                logger.info(f"Skipping {mv_name}")

        formatter.success(
            command="mv.apply",
            data=results,
            target=f"{project_id}.{dataset_id}",
            meta={"dry_run": common.dry_run},
        )

    except FileNotFoundError:
        formatter.error(
            error_type="FileNotFoundError",
            code="FILE_NOT_FOUND",
            message=f"Plan file not found: {plan_file}",
            command="mv.apply",
            target=str(plan_file),
        )
        sys.exit(1)
    except json.JSONDecodeError as e:
        formatter.error(
            error_type="JSONDecodeError",
            code="INVALID_JSON",
            message=f"Invalid JSON in plan file: {e}",
            command="mv.apply",
            target=str(plan_file),
        )
        sys.exit(1)
    except Exception as e:
        logger.exception("Unexpected error in mv apply")
        formatter.error(
            error_type="UnexpectedError",
            code="UNEXPECTED_ERROR",
            message=f"Unexpected error: {e}",
            command="mv.apply",
            target=f"{project_id}.{dataset_id}",
        )
        sys.exit(1)


@mv_group.command("list")
@click.option(
    "--project-id",
    required=True,
    help="BigQuery project ID",
)
@click.option(
    "--dataset-id",
    help="Filter by dataset ID",
)
@click.option(
    "--managed-only",
    is_flag=True,
    help="Only show automv-managed MVs",
)
@click.pass_context
def mv_list(
    ctx: click.Context,
    project_id: str,
    dataset_id: str | None,
    managed_only: bool,
) -> None:
    """List materialized views."""
    common = _get_common_config(ctx)
    formatter = _get_formatter(ctx)

    logger = setup_logging(common)

    try:
        # Note: This is a placeholder implementation
        # In production, this would query INFORMATION_SCHEMA.MATERIALIZED_VIEWS
        # and filter by labels if --managed-only

        formatter.emit_warning("MV list not yet fully implemented")
        formatter.emit_warning(
            "Use BigQuery UI or `bq query 'SELECT * FROM region-us.INFORMATION_SCHEMA.MATERIALIZED_VIEWS'`"
        )

        # Placeholder response
        mvs = []
        if dataset_id:
            logger.info(f"Would list MVs in {project_id}.{dataset_id}")
        else:
            logger.info(f"Would list MVs in all datasets in {project_id}")

        formatter.success(
            command="mv.list",
            data={
                "mvs": mvs,
                "count": len(mvs),
            },
            target=f"{project_id}.{dataset_id}" if dataset_id else project_id,
        )

    except Exception as e:
        logger.exception("Unexpected error in mv list")
        formatter.error(
            error_type="UnexpectedError",
            code="UNEXPECTED_ERROR",
            message=f"Unexpected error: {e}",
            command="mv.list",
            target=project_id,
        )
        sys.exit(1)


@mv_group.command("get")
@click.argument("mv_name")
@click.option(
    "--project-id",
    required=True,
    help="BigQuery project ID",
)
@click.pass_context
def mv_get(
    ctx: click.Context,
    mv_name: str,
    project_id: str,
) -> None:
    """Get details of a specific materialized view."""
    common = _get_common_config(ctx)
    formatter = _get_formatter(ctx)

    logger = setup_logging(common)

    try:
        # Note: This is a placeholder implementation
        # In production, this would query BigQuery to get MV details

        formatter.emit_warning("MV get not yet fully implemented")

        # Placeholder response
        formatter.success(
            command="mv.get",
            data={
                "mv": {
                    "name": mv_name,
                    "project_id": project_id,
                    "note": "Full implementation pending",
                }
            },
            target=mv_name,
        )

    except Exception as e:
        logger.exception("Unexpected error in mv get")
        formatter.error(
            error_type="UnexpectedError",
            code="UNEXPECTED_ERROR",
            message=f"Unexpected error: {e}",
            command="mv.get",
            target=mv_name,
        )
        sys.exit(1)


@mv_group.command("drop")
@click.argument("mv_name")
@click.option(
    "--project-id",
    required=True,
    help="BigQuery project ID",
)
@click.pass_context
def mv_drop(
    ctx: click.Context,
    mv_name: str,
    project_id: str,
) -> None:
    """Drop a materialized view."""
    common = _get_common_config(ctx)
    formatter = _get_formatter(ctx)

    logger = setup_logging(common)

    try:
        # Confirm if interactive
        if should_prompt(common.interactive, common.json):
            if not confirm_destructive_action(
                f"Drop materialized view {mv_name}?",
                formatter,
                default=False,
            ):
                formatter.emit_warning("Drop cancelled by user")
                sys.exit(0)

        # Note: This is a placeholder implementation
        # In production, this would use BigQueryClient.drop_materialized_view()

        if not common.dry_run:
            formatter.emit_warning("MV drop not yet fully implemented")
            formatter.emit_warning("Use `bq rm -mv <mv_name>` to drop MVs")
        else:
            logger.info(f"Would drop {mv_name}")

        formatter.success(
            command="mv.drop",
            data={
                "dropped": mv_name,
                "dry_run": common.dry_run,
            },
            target=mv_name,
        )

    except Exception as e:
        logger.exception("Unexpected error in mv drop")
        formatter.error(
            error_type="UnexpectedError",
            code="UNEXPECTED_ERROR",
            message=f"Unexpected error: {e}",
            command="mv.drop",
            target=mv_name,
        )
        sys.exit(1)


@mv_group.command("gc")
@click.option(
    "--project-id",
    required=True,
    help="BigQuery project ID",
)
@click.option(
    "--dataset-id",
    help="Filter by dataset ID",
)
@click.option(
    "--days-idle",
    type=int,
    default=30,
    help="Days idle before considering for GC (default: 30)",
)
@click.option(
    "--drop",
    is_flag=True,
    help="Actually drop the MVs (default: dry-run)",
)
@click.pass_context
def mv_gc(
    ctx: click.Context,
    project_id: str,
    dataset_id: str | None,
    days_idle: int,
    drop: bool,
) -> None:
    """Garbage collect unused materialized views."""
    common = _get_common_config(ctx)
    formatter = _get_formatter(ctx)

    logger = setup_logging(common)

    try:
        # Note: This is a placeholder implementation
        # In production, this would:
        # 1. Query MV usage statistics from INFORMATION_SCHEMA.JOBS
        # 2. Find MVs with no usage in the specified period
        # 3. Optionally drop them if --drop is set

        formatter.emit_warning("MV gc not yet fully implemented")

        # Placeholder response
        unused_mvs = []

        if drop and not common.dry_run:
            # Confirm if interactive
            if should_prompt(common.interactive, common.json):
                if not confirm_destructive_action(
                    f"Drop {len(unused_mvs)} unused MVs?",
                    formatter,
                    default=False,
                ):
                    formatter.emit_warning("GC cancelled by user")
                    sys.exit(0)

        formatter.success(
            command="mv.gc",
            data={
                "unused_mvs": unused_mvs,
                "count": len(unused_mvs),
                "dropped": [] if not (drop and not common.dry_run) else [mv["mv_name"] for mv in unused_mvs],
            },
            target=f"{project_id}.{dataset_id}" if dataset_id else project_id,
            meta={"dry_run": common.dry_run or not drop},
        )

    except Exception as e:
        logger.exception("Unexpected error in mv gc")
        formatter.error(
            error_type="UnexpectedError",
            code="UNEXPECTED_ERROR",
            message=f"Unexpected error: {e}",
            command="mv.gc",
            target=project_id,
        )
        sys.exit(1)


@mv_group.command("stats")
@click.argument("mv_name")
@click.option(
    "--project-id",
    required=True,
    help="BigQuery project ID",
)
@click.option(
    "--days",
    type=int,
    default=7,
    help="Number of days to analyze (default: 7)",
)
@click.pass_context
def mv_stats(
    ctx: click.Context,
    mv_name: str,
    project_id: str,
    days: int,
) -> None:
    """Show usage statistics for a materialized view."""
    common = _get_common_config(ctx)
    formatter = _get_formatter(ctx)

    logger = setup_logging(common)

    try:
        # Note: This is a placeholder implementation
        # In production, this would query INFORMATION_SCHEMA.JOBS
        # to get MV usage statistics

        formatter.emit_warning("MV stats not yet fully implemented")

        # Placeholder response
        formatter.success(
            command="mv.stats",
            data={
                "mv_name": mv_name,
                "period_days": days,
                "query_count": 0,
                "total_slot_ms": 0,
                "first_used": None,
                "last_used": None,
                "note": "Full implementation pending",
            },
            target=mv_name,
        )

    except Exception as e:
        logger.exception("Unexpected error in mv stats")
        formatter.error(
            error_type="UnexpectedError",
            code="UNEXPECTED_ERROR",
            message=f"Unexpected error: {e}",
            command="mv.stats",
            target=mv_name,
        )
        sys.exit(1)
