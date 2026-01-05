"""MV command group - manage materialized views.

This module provides commands for planning, applying, and managing
materialized views from query candidates.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import click

from bigquery_automv.cli.app import CommonConfig
from bigquery_automv.cli.utils import confirm_destructive_action, should_prompt
from bigquery_automv.lib.logging import setup_logging
from bigquery_automv.services.bq_client import BigQueryClient as BigQueryClient


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

        # Import services for SQL generation
        from bigquery_automv.models.query_candidate import QueryCandidate
        from bigquery_automv.services.bq_client import BigQueryClient
        from bigquery_automv.services.mv_generator import MVGeneratorService
        from bigquery_automv.services.smart_tuning import SmartTuningService

        # Initialize BigQuery client and services
        client = BigQueryClient(
            project_id=project_id,
            region=common.region,
        )
        smart_tuning_service = SmartTuningService(bq_client=client)
        mv_generator = MVGeneratorService(
            bq_client=client,
            smart_tuning_service=smart_tuning_service,
            tool_version="1.5.0",
        )

        # Generate plan actions with SQL
        plan_actions = []
        for candidate in candidates:
            query_hash = candidate.get("query_hash", "")
            if not query_hash:
                logger.warning(f"Skipping candidate with no query_hash: {candidate}")
                continue

            # Skip ineligible candidates
            smart_tuning_eligible = candidate.get("smart_tuning_eligible", False)
            if not smart_tuning_eligible:
                logger.info(f"Skipping ineligible candidate {query_hash}")
                continue

            try:
                # Build candidate object
                query_candidate = QueryCandidate.from_dict(candidate)

                # Generate MV artifact to get the actual SQL
                mv_artifact = asyncio.run(
                    mv_generator.generate_mv_artifact(
                        candidate=query_candidate,
                        target_dataset=dataset_id,
                        target_project=project_id,
                        mv_prefix=mv_prefix,
                        refresh_interval_minutes=refresh_interval_minutes,
                        enable_refresh=True,
                    )
                )

                # Compute hashes
                representative_query = candidate.get("representative_query", "")
                family_hash = _compute_family_hash(representative_query)
                signature_hash = _compute_signature_hash(representative_query)

                # Determine action
                # For now, always create - in production we'd check if MV exists
                # and compare family/signature hashes to determine replace/keep/skip
                action = "create"

                plan_actions.append(
                    {
                        "action": action,
                        "query_hash": query_hash,
                        "mv_name": mv_artifact.mv_name,
                        "family_hash": family_hash,
                        "signature_hash": signature_hash,
                        "dataset_id": dataset_id,
                        "candidate": candidate,
                        "refresh_interval_minutes": refresh_interval_minutes,
                        # IMPORTANT: Include the actual SQL that will be run
                        "mv_sql": mv_artifact.mv_query,
                        "mv_ddl": mv_artifact.ddl_definition,  # Full DDL for reference
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to generate SQL for candidate {query_hash}: {e}")
                # Still include the action but mark as failed
                representative_query = candidate.get("representative_query", "")
                family_hash = _compute_family_hash(representative_query)
                signature_hash = _compute_signature_hash(representative_query)
                mv_name = f"{mv_prefix}{family_hash}_{signature_hash[:8]}"

                plan_actions.append(
                    {
                        "action": "skip",
                        "query_hash": query_hash,
                        "mv_name": mv_name,
                        "family_hash": family_hash,
                        "signature_hash": signature_hash,
                        "dataset_id": dataset_id,
                        "candidate": candidate,
                        "refresh_interval_minutes": refresh_interval_minutes,
                        "error": str(e),
                    }
                )

        # Build summary
        summary = {
            "create": sum(1 for a in plan_actions if a["action"] == "create"),
            "replace": sum(1 for a in plan_actions if a["action"] == "replace"),
            "keep": sum(1 for a in plan_actions if a["action"] == "keep"),
            "skip": sum(1 for a in plan_actions if a["action"] == "skip"),
            "drop": sum(1 for a in plan_actions if a["action"] == "drop"),
            "error": sum(1 for a in plan_actions if "error" in a),
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
@click.option(
    "--dry-run",
    is_flag=True,
    default=None,
    help="Dry run mode (show what would be done without making changes)",
)
@click.option(
    "--auto-apply",
    is_flag=True,
    default=False,
    help="Automatically apply without interactive confirmation (useful for AI/automation)",
)
@click.pass_context
def mv_apply(
    ctx: click.Context,
    plan_file: Path,
    project_id: str,
    dataset_id: str,
    action_filter: str | None,
    dry_run: bool | None = None,
    auto_apply: bool = False,
) -> None:
    """Apply a materialized view plan."""
    common = _get_common_config(ctx)
    formatter = _get_formatter(ctx)

    # Local --dry-run flag overrides global setting
    if dry_run is not None:
        common.dry_run = dry_run

    # --auto-apply disables interactive mode
    if auto_apply:
        common.interactive = False

    logger = setup_logging(common)

    try:
        # Load plan from file
        with open(plan_file) as f:
            plan = json.load(f)

        actions = plan.get("actions", [])
        # Extract mv_prefix from plan metadata (fallback to "automv_" for backward compatibility)
        mv_prefix = plan.get("metadata", {}).get("mv_prefix", "automv_")
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

        # Process actions
        for action in actions:
            action_type = action["action"]
            mv_name = action.get("mv_name", "")
            candidate = action.get("candidate", {})

            if action_type == "skip":
                results["skipped"].append(mv_name)
                logger.info(f"Skipping {mv_name}")
                continue

            if action_type == "create" or action_type == "replace":
                try:
                    # In dry-run mode, just log what would be done
                    if common.dry_run:
                        if action_type == "create":
                            results["created"].append(f"{dataset_id}.{mv_name} (dry-run)")
                            logger.info(f"Would create {dataset_id}.{mv_name}")
                        else:
                            results["replaced"].append(f"{dataset_id}.{mv_name} (dry-run)")
                            logger.info(f"Would replace {dataset_id}.{mv_name}")
                        continue

                    # Check if action has pre-generated SQL from plan (preferred)
                    # If mv_sql exists in action, use it directly instead of regenerating
                    mv_sql = action.get("mv_sql")
                    if not mv_sql:
                        # Fallback: regenerate SQL if not in plan
                        # This shouldn't happen with new plans, but provides backward compatibility
                        from bigquery_automv.models.query_candidate import QueryCandidate
                        from bigquery_automv.services.bq_client import BigQueryClient
                        from bigquery_automv.services.mv_generator import MVGeneratorService
                        from bigquery_automv.services.smart_tuning import SmartTuningService

                        # Initialize services for regeneration
                        client = BigQueryClient(
                            project_id=project_id,
                            region=common.region,
                        )
                        smart_tuning_service = SmartTuningService(bq_client=client)
                        mv_generator = MVGeneratorService(
                            bq_client=client,
                            smart_tuning_service=smart_tuning_service,
                            tool_version="1.5.0",
                        )

                        query_candidate = QueryCandidate.from_dict(candidate)
                        mv_artifact = asyncio.run(
                            mv_generator.generate_mv_artifact(
                                candidate=query_candidate,
                                target_dataset=dataset_id,
                                target_project=project_id,
                                mv_prefix=mv_prefix,
                                refresh_interval_minutes=action.get("refresh_interval_minutes", 60),
                                enable_refresh=True,
                            )
                        )
                        mv_sql = mv_artifact.mv_query

                    # Capture loop variables BEFORE function definition to avoid closure issues
                    captured_action = action
                    captured_mv_name = mv_name
                    captured_action_type = action_type
                    captured_refresh_interval_minutes = action.get("refresh_interval_minutes", 60)
                    captured_project_id = project_id
                    captured_dataset_id = dataset_id
                    captured_region = common.region

                    # Create async runner
                    async def create_mv(  # noqa: B023
                        captured_action_captured=captured_action,
                        captured_mv_sql_captured=mv_sql,
                        captured_mv_name_captured=captured_mv_name,
                        captured_action_type_captured=captured_action_type,
                        captured_refresh_interval_minutes_captured=captured_refresh_interval_minutes,
                        captured_project_id_captured=captured_project_id,
                        captured_dataset_id_captured=captured_dataset_id,
                        captured_region_captured=captured_region,
                    ):
                        # Import BigQueryClient inside the function to avoid closure issues
                        from bigquery_automv.services.bq_client import BigQueryClient

                        # Initialize BigQuery client
                        client = BigQueryClient(
                            project_id=captured_project_id_captured,
                            region=captured_region_captured,
                        )

                        # Prepare labels
                        labels = {
                            "automv_managed": "true",
                            "automv_family_hash": captured_action_captured.get("family_hash", ""),
                            "automv_signature_hash": captured_action_captured.get(
                                "signature_hash",
                                "",
                            ),
                            "automv_version": "1_5_0",  # BigQuery labels cannot contain dots
                        }

                        # Strip "dataset." prefix from mv_name if present
                        mv_short_name = captured_mv_name_captured.replace(
                            f"{captured_dataset_id_captured}.",
                            "",
                        )

                        # Create or replace MV
                        if captured_action_type_captured == "replace":
                            # Drop existing MV first
                            await client.drop_materialized_view(
                                mv_name=mv_short_name,
                                dataset_id=captured_dataset_id_captured,
                                project_id=captured_project_id_captured,
                                if_exists=True,
                            )

                        # Create MV with labels
                        await client.create_materialized_view(
                            mv_name=mv_short_name,
                            dataset_id=captured_dataset_id_captured,
                            query=captured_mv_sql_captured,  # Use SQL from plan or regenerated
                            project_id=captured_project_id_captured,
                            enable_refresh=True,
                            refresh_interval_minutes=captured_refresh_interval_minutes_captured,
                            labels=labels,
                        )

                        return mv_short_name

                    # Run async function
                    mv_short_name = asyncio.run(create_mv())

                    if action_type == "create":
                        results["created"].append(f"{dataset_id}.{mv_short_name}")
                    else:
                        results["replaced"].append(f"{dataset_id}.{mv_short_name}")

                    action_verb = "created" if action_type == "create" else "replaced"
                    logger.info(
                        f"Successfully {action_verb} {dataset_id}.{mv_short_name}",
                    )

                except Exception as e:
                    logger.exception(f"Failed to {action_type} {mv_name}: {e}")
                    results["failed"].append(f"{dataset_id}.{mv_name}: {str(e)}")
                    formatter.emit_warning(f"Failed to {action_type} {mv_name}: {e}")

                    # In production mode, fail on first error?
                    # For now, continue with remaining actions
                    continue

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
        from bigquery_automv.services.bq_client import BigQueryClient

        # Create BigQuery client
        client = BigQueryClient(
            project_id=project_id,
            region=common.region,
        )

        # Build label filter for automv-managed MVs
        label_filter = {"automv_managed": "true"} if managed_only else None

        # List MVs in the specified dataset(s)
        if dataset_id:
            mvs_data = asyncio.run(
                client.list_materialized_views(
                    dataset_id=dataset_id,
                    project_id=project_id,
                    label_filter=label_filter,
                )
            )
        else:
            # If no dataset specified, we need to list datasets or return error
            # For now, return empty list with message
            mvs_data = []
            formatter.emit_warning("Dataset ID required for listing MVs")

        # Format response
        mvs = [
            {
                "name": mv["table_id"],
                "dataset": mv["dataset_id"],
                "project": mv["project_id"],
                "full_name": mv["full_name"],
                "labels": mv.get("labels", {}),
                "num_bytes": mv.get("num_bytes", 0),
                "num_rows": mv.get("num_rows", 0),
                "last_refresh_time": mv.get("last_refresh_time"),
            }
            for mv in mvs_data
        ]

        logger.info(f"Listed {len(mvs)} materialized views")

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
        from bigquery_automv.services.bq_client import BigQueryClient

        # Parse mv_name to extract dataset and table
        # Expected formats: "dataset.mv_name" or "project.dataset.mv_name" or "mv_name"
        parts = mv_name.split(".")
        if len(parts) == 3:
            # project.dataset.table
            project_id = parts[0]
            dataset_id = parts[1]
            table_id = parts[2]
        elif len(parts) == 2:
            # dataset.table
            dataset_id = parts[0]
            table_id = parts[1]
        else:
            # table only - need dataset from somewhere else
            formatter.error(
                error_type="ValueError",
                code="INVALID_MV_NAME",
                message=f"MV name must be in format 'dataset.mv_name' or 'project.dataset.mv_name', got: {mv_name}",
                command="mv.get",
                target=mv_name,
            )
            sys.exit(1)

        # Create BigQuery client
        client = BigQueryClient(
            project_id=project_id,
            region=common.region,
        )

        # Get MV details
        mv_data = asyncio.run(
            client.get_materialized_view(
                mv_name=table_id,
                dataset_id=dataset_id,
                project_id=project_id,
            )
        )

        formatter.success(
            command="mv.get",
            data={"mv": mv_data},
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
        from bigquery_automv.services.bq_client import BigQueryClient

        # Parse mv_name to extract dataset and table
        parts = mv_name.split(".")
        if len(parts) == 3:
            project_id = parts[0]
            dataset_id = parts[1]
            table_id = parts[2]
        elif len(parts) == 2:
            dataset_id = parts[0]
            table_id = parts[1]
        else:
            formatter.error(
                error_type="ValueError",
                code="INVALID_MV_NAME",
                message=f"MV name must be in format 'dataset.mv_name' or 'project.dataset.mv_name', got: {mv_name}",
                command="mv.drop",
                target=mv_name,
            )
            sys.exit(1)

        # Confirm if interactive
        if should_prompt(common.interactive, common.json):
            if not confirm_destructive_action(
                f"Drop materialized view {mv_name}?",
                formatter,
                default=False,
            ):
                formatter.emit_warning("Drop cancelled by user")
                sys.exit(0)

        if common.dry_run:
            logger.info(f"Would drop {mv_name}")
        else:
            # Create BigQuery client and drop MV
            client = BigQueryClient(
                project_id=project_id,
                region=common.region,
            )
            asyncio.run(
                client.drop_materialized_view(
                    mv_name=table_id,
                    dataset_id=dataset_id,
                    project_id=project_id,
                    if_exists=True,
                )
            )
            logger.info(f"Dropped {mv_name}")

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
        from bigquery_automv.services.bq_client import BigQueryClient
        from bigquery_automv.services.impact import ImpactService

        # Create BigQuery client and Impact service
        client = BigQueryClient(
            project_id=project_id,
            region=common.region,
        )
        impact_service = ImpactService(client=client)

        # Get all MVs in the dataset
        if not dataset_id:
            formatter.error(
                error_type="ValueError",
                code="MISSING_DATASET",
                message="Dataset ID is required for garbage collection",
                command="mv.gc",
                target=project_id,
            )
            sys.exit(1)

        # List MVs in dataset
        mvs_data = asyncio.run(
            client.list_materialized_views(
                dataset_id=dataset_id,
                project_id=project_id,
            )
        )

        # Find unused MVs (no usage in the specified period)
        unused_mvs = []

        for mv in mvs_data:
            mv_full_name = mv["full_name"]
            # Get usage stats for this MV
            stats = asyncio.run(
                impact_service.get_mv_usage_stats(
                    mv_name=mv_full_name,
                    days=days_idle,
                )
            )

            # If no queries in the period, consider it unused
            if stats["query_count"] == 0:
                unused_mvs.append(
                    {
                        "mv_name": mv_full_name,
                        "days_since_last_use": None,  # No last_used data
                    }
                )

        dropped_mvs = []
        if drop and not common.dry_run and unused_mvs:
            # Confirm if interactive
            if should_prompt(common.interactive, common.json):
                if not confirm_destructive_action(
                    f"Drop {len(unused_mvs)} unused MVs?",
                    formatter,
                    default=False,
                ):
                    formatter.emit_warning("GC cancelled by user")
                    sys.exit(0)

            # Drop unused MVs
            for mv in unused_mvs:
                parts = mv["mv_name"].split(".")
                if len(parts) >= 3:
                    mv_project_id = parts[0]
                    mv_dataset_id = parts[1]
                    mv_table_id = parts[2]
                elif len(parts) == 2:
                    mv_project_id = project_id
                    mv_dataset_id = parts[0]
                    mv_table_id = parts[1]
                else:
                    continue

                try:
                    asyncio.run(
                        client.drop_materialized_view(
                            mv_name=mv_table_id,
                            dataset_id=mv_dataset_id,
                            project_id=mv_project_id,
                            if_exists=True,
                        )
                    )
                    dropped_mvs.append(mv["mv_name"])
                    logger.info(f"Dropped unused MV: {mv['mv_name']}")
                except Exception as e:
                    logger.warning(f"Failed to drop {mv['mv_name']}: {e}")

        formatter.success(
            command="mv.gc",
            data={
                "unused_mvs": unused_mvs,
                "count": len(unused_mvs),
                "dropped": dropped_mvs,
            },
            target=f"{project_id}.{dataset_id}",
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
        from bigquery_automv.services.bq_client import BigQueryClient
        from bigquery_automv.services.impact import ImpactService

        # Create BigQuery client and Impact service
        client = BigQueryClient(
            project_id=project_id,
            region=common.region,
        )
        impact_service = ImpactService(client=client)

        # Get usage stats for the MV
        stats = asyncio.run(
            impact_service.get_mv_usage_stats(
                mv_name=mv_name,
                days=days,
            )
        )

        formatter.success(
            command="mv.stats",
            data={
                "mv_name": mv_name,
                "period_days": days,
                "query_count": stats["query_count"],
                "total_slot_ms": stats["total_slot_ms"],
                "total_bytes_processed": stats["total_bytes_processed"],
                "first_used": stats["first_used"],
                "last_used": stats["last_used"],
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
