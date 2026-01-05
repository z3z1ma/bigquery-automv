"""Candidates command group - manage query candidates for materialized views.

This module provides commands for discovering, listing, and managing
query candidates that could benefit from materialization.
"""

from __future__ import annotations

import asyncio
import csv
import json
import sys
from datetime import date, datetime
from pathlib import Path

import click

from bigquery_automv.cli.app import CommonConfig
from bigquery_automv.lib.config import AnalysisConfig, ImpactScoringConfig
from bigquery_automv.lib.logging import setup_logging
from bigquery_automv.services import AnalysisResult, AnalyzerService
from bigquery_automv.services.bq_client import (
    BigQueryClient,
    BigQueryError,
    NotFoundError,
    PermissionError,
)
from bigquery_automv.services.smart_tuning import SmartTuningService


@click.group(name="candidates")
def candidates_group() -> None:
    """Manage query candidates for materialized views."""


def _get_common_config(ctx: click.Context) -> CommonConfig:
    """Get CommonConfig from click context."""
    return ctx.obj["common"]


def _get_formatter(ctx: click.Context):
    """Get OutputFormatter from click context."""
    return ctx.obj["formatter"]


async def _run_analysis(
    start_date: date,
    end_date: date,
    common: CommonConfig,
    analysis_config: AnalysisConfig,
    impact_config: ImpactScoringConfig,
    include_ineligible: bool = False,
    persist: bool = False,
    persist_dataset: str | None = None,
) -> AnalysisResult:
    """Run the analysis asynchronously."""
    logger = setup_logging(common)

    logger.info(
        "Starting BigQuery query analysis",
        extra={
            "project": common.project,
            "region": common.region,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        },
    )

    # Validate project ID
    if not common.project:
        msg = "Project ID is required. Set --project or GOOGLE_CLOUD_PROJECT environment variable."
        raise ValueError(msg)

    # Create BigQuery client
    async with BigQueryClient(
        project_id=common.project,
        region=common.region,
    ) as client:
        # Create Smart Tuning service for eligibility checks
        smart_tuning_service = SmartTuningService(
            bq_client=client,
            enable_preview_eligibility=False,  # Use stable-only features
        )

        # Create analyzer service
        analyzer = AnalyzerService(
            client=client,
            smart_tuning_service=smart_tuning_service,
            impact_config=impact_config,
            analysis_config=analysis_config,
        )

        # Convert dates to datetime
        start_datetime = datetime.combine(start_date, datetime.min.time())
        end_datetime = datetime.combine(end_date, datetime.max.time())

        # Run analysis
        result = await analyzer.analyze(
            start_date=start_datetime,
            end_date=end_datetime,
            project_id=common.project,
        )

        # Filter candidates based on eligibility if requested
        if not include_ineligible:
            eligible_candidates = [c for c in result.candidates if c.smart_tuning_eligible]
            logger.info(
                "Filtered candidates by eligibility",
                extra={
                    "total_candidates": len(result.candidates),
                    "eligible_candidates": len(eligible_candidates),
                    "ineligible_filtered": len(result.candidates) - len(eligible_candidates),
                },
            )
            # Update result with filtered candidates
            result = AnalysisResult(
                candidates=eligible_candidates,
                metrics=result.metrics,
                start_date=result.start_date,
                end_date=result.end_date,
                project_id=result.project_id,
                region=result.region,
            )

        # Persist candidates if requested
        if persist:
            target_dataset = persist_dataset or common.dataset
            if not target_dataset:
                logger.warning("Cannot persist candidates: no dataset specified (use --dataset or --persist-dataset)")
            else:
                await _persist_candidates(client, result, target_dataset, start_date, end_date)

        return result


async def _persist_candidates(
    client: BigQueryClient,
    result: AnalysisResult,
    dataset_id: str,
    start_date: date,
    end_date: date,
) -> None:
    """Persist analysis candidates to BigQuery table."""
    logger = setup_logging()

    # Initialize candidates table if needed
    try:
        await client.initialize_candidates_table(dataset_id=dataset_id)
        logger.info(f"Initialized candidates table in {dataset_id}")
    except Exception as e:
        logger.error(f"Failed to initialize candidates table: {e}")
        raise

    # Convert dates to datetime for ISO format
    start_datetime = datetime.combine(start_date, datetime.min.time())
    end_datetime = datetime.combine(end_date, datetime.max.time())

    # Persist each candidate
    persisted = 0
    failed = 0

    for candidate in result.candidates:
        try:
            candidate_dict = {
                "query_hash": candidate.query_hash,
                "representative_query": candidate.representative_query,
                "execution_count": candidate.execution_count,
                "bytes_billed_total": candidate.bytes_billed_total,
                "total_bytes_processed": candidate.total_bytes_processed,
                "slot_ms_total": candidate.slot_ms_total,
                "impact_score": candidate.impact_score,
                "dollar_cost_est_on_demand": candidate.dollar_cost_est_on_demand,
                "impact_model_version": candidate.impact_model_version,
                "rulebook_version": candidate.rulebook_version,
                "statement_type": candidate.statement_type,
                "first_seen": candidate.first_seen.isoformat(),
                "last_seen": candidate.last_seen.isoformat(),
                "referenced_tables": [
                    {
                        "project_id": t.project_id,
                        "dataset_id": t.dataset_id,
                        "table_id": t.table_id,
                        "region": t.region,
                        "full_name": t.full_name,
                    }
                    for t in candidate.referenced_tables
                ],
                "smart_tuning_eligible": candidate.smart_tuning_eligible,
                "eligibility_basis": candidate.eligibility_basis,
                "smart_tuning_reasons": candidate.smart_tuning_reasons or [],
                "analysis_start_date": start_datetime.isoformat(),
                "analysis_end_date": end_datetime.isoformat(),
            }

            await client.insert_candidate(dataset_id=dataset_id, candidate=candidate_dict)
            persisted += 1
        except Exception as e:
            logger.warning(f"Failed to persist candidate {candidate.query_hash}: {e}")
            failed += 1

    logger.info(
        f"Persisted {persisted} candidates to {dataset_id}.query_candidates",
        extra={"persisted": persisted, "failed": failed},
    )


def _format_candidate_dict(candidate) -> dict:
    """Format a candidate object as a dictionary for JSON/CSV output."""
    return {
        "query_hash": candidate.query_hash,
        "representative_query": candidate.representative_query,
        "execution_count": candidate.execution_count,
        "bytes_billed_total": candidate.bytes_billed_total,
        "total_bytes_processed": candidate.total_bytes_processed,
        "slot_ms_total": candidate.slot_ms_total,
        "impact_score": round(candidate.impact_score, 2),
        "dollar_cost_est_on_demand": round(candidate.dollar_cost_est_on_demand, 2),
        "impact_model_version": candidate.impact_model_version,
        "rulebook_version": candidate.rulebook_version,
        "statement_type": candidate.statement_type,
        "first_seen": candidate.first_seen.isoformat(),
        "last_seen": candidate.last_seen.isoformat(),
        "referenced_tables": [
            {
                "project_id": t.project_id,
                "dataset_id": t.dataset_id,
                "table_id": t.table_id,
                "region": t.region,
            }
            for t in candidate.referenced_tables
        ],
        "smart_tuning_eligible": candidate.smart_tuning_eligible,
        "eligibility_basis": candidate.eligibility_basis,
    }


@candidates_group.command("discover")
@click.option(
    "--start-date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    required=True,
    help="Start of analysis window (inclusive, YYYY-MM-DD format)",
)
@click.option(
    "--end-date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    default=str(date.today()),
    help="End of analysis window (inclusive, YYYY-MM-DD format)",
)
@click.option(
    "--min-executions",
    default=10,
    help="Minimum execution count per query family",
)
@click.option(
    "--min-bytes",
    default=1073741824,  # 1GB
    help="Minimum bytes processed threshold",
)
@click.option(
    "--min-slot-ms",
    default=0,
    help="Minimum slot milliseconds threshold",
)
@click.option(
    "--max-families",
    default=100,
    help="Maximum number of query families to return",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    help="Write results to file (JSON/CSV)",
)
@click.option(
    "--price-per-tib",
    default=6.25,
    help="BigQuery on-demand price per TiB for impact scoring",
)
@click.option(
    "--slot-weight",
    default=0.25,
    help="Weight for slot component in impact score",
)
@click.option(
    "--slot-ms-per-tib-equivalent",
    default=3.6e9,
    help="Slot-ms to TiB equivalent conversion factor",
)
@click.option(
    "--sample-size-k",
    default=20,
    help="Number of sample queries for MV synthesis",
)
@click.option(
    "--include-ineligible",
    is_flag=True,
    help="Include candidates that are not eligible for Smart Tuning",
)
@click.option(
    "--persist",
    is_flag=True,
    help="Persist candidates to BigQuery table for later use",
)
@click.option(
    "--persist-dataset",
    help="Dataset for persisting candidates (defaults to --dataset if not set)",
)
@click.pass_context
def candidates_discover(
    ctx: click.Context,
    start_date: datetime,
    end_date: datetime,
    min_executions: int,
    min_bytes: int,
    min_slot_ms: int,
    max_families: int,
    output: Path | None,
    price_per_tib: float,
    slot_weight: float,
    slot_ms_per_tib_equivalent: float,
    sample_size_k: int,
    include_ineligible: bool,
    persist: bool,
    persist_dataset: str | None,
) -> None:
    """Analyze query history to discover MV candidates."""
    common = _get_common_config(ctx)
    formatter = _get_formatter(ctx)

    # Set up logging
    logger = setup_logging(common)

    try:
        # Convert click.DateTime to date
        start_date_obj = start_date.date()
        end_date_obj = end_date.date()

        # Validate date range
        if end_date_obj < start_date_obj:
            formatter.error(
                error_type="ValueError",
                code="INVALID_DATE_RANGE",
                message="End date must be on or after start date",
                command="candidates.discover",
                target=common.project,
            )
            sys.exit(1)

        # Create configuration objects
        analysis_config = AnalysisConfig(
            min_executions=min_executions,
            min_bytes=min_bytes,
            min_slot_ms=min_slot_ms,
            max_families=max_families,
            sample_size_k=sample_size_k,
        )

        impact_config = ImpactScoringConfig(
            price_per_tib=price_per_tib,
            slot_weight=slot_weight,
            slot_ms_per_tib_equivalent=slot_ms_per_tib_equivalent,
        )

        logger.info(
            "Starting analysis",
            extra={
                "start_date": start_date_obj.isoformat(),
                "end_date": end_date_obj.isoformat(),
                "min_executions": min_executions,
            },
        )

        # Run analysis asynchronously
        result = asyncio.run(
            _run_analysis(
                start_date=start_date_obj,
                end_date=end_date_obj,
                common=common,
                analysis_config=analysis_config,
                impact_config=impact_config,
                include_ineligible=include_ineligible,
                persist=persist,
                persist_dataset=persist_dataset,
            )
        )

        # Format candidates as dictionaries
        candidates_data = [_format_candidate_dict(c) for c in result.candidates]

        # Build output data
        days_analyzed = (result.end_date - result.start_date).days + 1
        output_data = {
            "candidates": candidates_data,
            "count": len(candidates_data),
            "analysis_period": {
                "start_date": result.start_date.strftime("%Y-%m-%d"),
                "end_date": result.end_date.strftime("%Y-%m-%d"),
                "days_analyzed": days_analyzed,
            },
            "metrics": {
                "total_jobs_scanned": result.metrics.total_jobs_scanned,
                "families_analyzed": result.metrics.families_analyzed,
            },
        }

        # Write to file if specified
        if output:
            try:
                output.parent.mkdir(parents=True, exist_ok=True)

                # Determine format from extension
                if output.suffix.lower() == ".json":
                    output.write_text(json.dumps(output_data, indent=2))
                elif output.suffix.lower() == ".csv":
                    import io

                    output_file = io.StringIO()
                    fieldnames = candidates_data[0].keys() if candidates_data else []
                    writer = csv.DictWriter(output_file, fieldnames=fieldnames)
                    writer.writeheader()
                    for candidate in candidates_data:
                        # Flatten referenced_tables for CSV
                        row = candidate.copy()
                        row["referenced_tables"] = ",".join(t["full_name"] for t in candidate["referenced_tables"])
                        writer.writerow(row)
                    output.write_text(output_file.getvalue())
                else:
                    formatter.error(
                        error_type="ValueError",
                        code="INVALID_OUTPUT_FORMAT",
                        message=f"Unsupported output format: {output.suffix}. Use .json or .csv",
                        command="candidates.discover",
                        target=str(output),
                    )
                    sys.exit(1)

                formatter.emit_warning(f"Results written to {output}")
            except OSError as e:
                formatter.error(
                    error_type="OSError",
                    code="FILE_WRITE_ERROR",
                    message=f"Failed to write output file: {e}",
                    command="candidates.discover",
                    target=str(output),
                )
                sys.exit(1)

        # Emit success
        formatter.success(
            command="candidates.discover",
            data=output_data,
            target=common.project,
            meta={"dry_run": common.dry_run},
        )

        logger.info(
            "Analysis complete",
            extra={
                "total_candidates": len(result.candidates),
                "families_analyzed": result.metrics.families_analyzed,
            },
        )

    except PermissionError as e:
        logger.error("Permission error", extra={"error": str(e)})
        formatter.error(
            error_type="PermissionError",
            code="PERMISSION_DENIED",
            message=f"Permission denied: {e.message}",
            remediation="Check IAM permissions for BigQuery",
            command="candidates.discover",
            target=common.project,
        )
        sys.exit(1)

    except NotFoundError as e:
        logger.error("Not found error", extra={"error": str(e)})
        formatter.error(
            error_type="NotFoundError",
            code="NOT_FOUND",
            message=f"Resource not found: {e.message}",
            remediation="Verify the project and dataset exist",
            command="candidates.discover",
            target=common.project,
        )
        sys.exit(1)

    except BigQueryError as e:
        logger.error("BigQuery error", extra={"error": str(e)})
        formatter.error(
            error_type="BigQueryError",
            code="BQ_ERROR",
            message=f"BigQuery error: {e.message}",
            command="candidates.discover",
            target=common.project,
        )
        sys.exit(1)

    except ValueError as e:
        logger.error("Validation error", extra={"error": str(e)})
        formatter.error(
            error_type="ValueError",
            code="VALIDATION_ERROR",
            message=str(e),
            command="candidates.discover",
            target=common.project,
        )
        sys.exit(1)

    except Exception as e:
        logger.exception("Unexpected error")
        formatter.error(
            error_type="UnexpectedError",
            code="UNEXPECTED_ERROR",
            message=f"Unexpected error: {e}",
            command="candidates.discover",
            target=common.project,
        )
        sys.exit(1)


@candidates_group.command("list")
@click.option(
    "--input",
    "-i",
    "input_file",
    type=click.Path(exists=True, path_type=Path),
    help="Candidates file from discover",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "json"], case_sensitive=False),
    default="table",
    help="Output format",
)
@click.pass_context
def candidates_list(
    ctx: click.Context,
    input_file: Path | None,
    output_format: str,
) -> None:
    """List discovered candidates."""
    formatter = _get_formatter(ctx)

    try:
        # Load candidates from file
        if input_file:
            if input_file.suffix.lower() == ".json":
                with open(input_file) as f:
                    input_data = json.load(f)
                    candidates = input_data.get("candidates", [])
            elif input_file.suffix.lower() == ".csv":
                with open(input_file) as f:
                    reader = csv.DictReader(f)
                    candidates = list(reader)
            else:
                formatter.error(
                    error_type="ValueError",
                    code="INVALID_INPUT_FORMAT",
                    message=f"Unsupported input format: {input_file.suffix}. Use .json or .csv",
                    command="candidates.list",
                    target=str(input_file),
                )
                sys.exit(1)
        else:
            # If no input file, try to query from BigQuery
            # For now, just return an error
            formatter.error(
                error_type="ValueError",
                code="NO_INPUT",
                message="No input file specified. Use --input to provide a candidates file.",
                remediation="Run 'candidates discover -o candidates.json' first",
                command="candidates.list",
                target="",
            )
            sys.exit(1)

        # Determine output format
        use_json = output_format == "json" or ctx.obj["common"].json

        if use_json:
            formatter.success(
                command="candidates.list",
                data={
                    "candidates": candidates,
                    "count": len(candidates),
                },
                target=str(input_file) if input_file else "",
            )
        else:
            # Human-readable table format
            _print_candidates_table(candidates)

    except FileNotFoundError:
        formatter.error(
            error_type="FileNotFoundError",
            code="FILE_NOT_FOUND",
            message=f"Input file not found: {input_file}",
            command="candidates.list",
            target=str(input_file),
        )
        sys.exit(1)
    except json.JSONDecodeError as e:
        formatter.error(
            error_type="JSONDecodeError",
            code="INVALID_JSON",
            message=f"Invalid JSON in input file: {e}",
            command="candidates.list",
            target=str(input_file),
        )
        sys.exit(1)
    except Exception as e:
        formatter.error(
            error_type="UnexpectedError",
            code="UNEXPECTED_ERROR",
            message=f"Unexpected error: {e}",
            command="candidates.list",
            target=str(input_file) if input_file else "",
        )
        sys.exit(1)


@candidates_group.command("get")
@click.argument("query_hash")
@click.option(
    "--input",
    "-i",
    "input_file",
    type=click.Path(exists=True, path_type=Path),
    help="Candidates file from discover",
)
@click.pass_context
def candidates_get(
    ctx: click.Context,
    query_hash: str,
    input_file: Path,
) -> None:
    """Get details of a specific candidate."""
    formatter = _get_formatter(ctx)

    try:
        # Load candidates from file
        if input_file.suffix.lower() == ".json":
            with open(input_file) as f:
                input_data = json.load(f)
                candidates = input_data.get("candidates", [])
        elif input_file.suffix.lower() == ".csv":
            with open(input_file) as f:
                reader = csv.DictReader(f)
                candidates = list(reader)
        else:
            formatter.error(
                error_type="ValueError",
                code="INVALID_INPUT_FORMAT",
                message=f"Unsupported input format: {input_file.suffix}. Use .json or .csv",
                command="candidates.get",
                target=str(input_file),
            )
            sys.exit(1)

        # Find candidate by prefix match on query_hash
        candidate = None
        for c in candidates:
            if c.get("query_hash", "").startswith(query_hash):
                candidate = c
                break

        if not candidate:
            formatter.error(
                error_type="NotFound",
                code="CANDIDATE_NOT_FOUND",
                message=f"Candidate with hash '{query_hash}' not found",
                remediation="Run 'candidates list' to see available candidates",
                command="candidates.get",
                target=query_hash,
            )
            sys.exit(1)

        formatter.success(
            command="candidates.get",
            data={"candidate": candidate},
            target=query_hash,
        )

    except FileNotFoundError:
        formatter.error(
            error_type="FileNotFoundError",
            code="FILE_NOT_FOUND",
            message=f"Input file not found: {input_file}",
            command="candidates.get",
            target=str(input_file),
        )
        sys.exit(1)
    except Exception as e:
        formatter.error(
            error_type="UnexpectedError",
            code="UNEXPECTED_ERROR",
            message=f"Unexpected error: {e}",
            command="candidates.get",
            target=query_hash,
        )
        sys.exit(1)


@candidates_group.command("prune")
@click.option(
    "--input",
    "-i",
    "input_file",
    type=click.Path(exists=True, path_type=Path),
    required=True,
    help="Candidates file from discover",
)
@click.option(
    "--min-impact",
    type=float,
    default=10.0,
    help="Minimum impact score threshold",
)
@click.option(
    "--min-executions",
    type=int,
    help="Minimum execution count threshold",
)
@click.option(
    "--output",
    "-o",
    "output_file",
    type=click.Path(path_type=Path),
    required=True,
    help="Output file for pruned candidates",
)
@click.pass_context
def candidates_prune(
    ctx: click.Context,
    input_file: Path,
    min_impact: float,
    min_executions: int | None,
    output_file: Path,
) -> None:
    """Filter candidates by impact threshold."""
    formatter = _get_formatter(ctx)

    try:
        # Load candidates from file
        if input_file.suffix.lower() == ".json":
            with open(input_file) as f:
                input_data = json.load(f)
                candidates = input_data.get("candidates", [])
                # Preserve metadata if available
                metadata = {k: v for k, v in input_data.items() if k != "candidates"}
        elif input_file.suffix.lower() == ".csv":
            with open(input_file) as f:
                reader = csv.DictReader(f)
                candidates = list(reader)
            metadata = {}
        else:
            formatter.error(
                error_type="ValueError",
                code="INVALID_INPUT_FORMAT",
                message=f"Unsupported input format: {input_file.suffix}. Use .json or .csv",
                command="candidates.prune",
                target=str(input_file),
            )
            sys.exit(1)

        # Filter candidates
        pruned = []
        for c in candidates:
            impact = float(c.get("impact_score", 0))
            executions = int(c.get("execution_count", 0))

            if min_executions is not None:
                if impact >= min_impact and executions >= min_executions:
                    pruned.append(c)
            else:
                if impact >= min_impact:
                    pruned.append(c)

        # Build output
        output_data = metadata.copy()
        output_data["candidates"] = pruned

        # Write output
        try:
            output_file.parent.mkdir(parents=True, exist_ok=True)

            if output_file.suffix.lower() == ".json":
                output_file.write_text(json.dumps(output_data, indent=2))
            elif output_file.suffix.lower() == ".csv":
                import io

                output_str = io.StringIO()
                if pruned:
                    writer = csv.DictWriter(output_str, fieldnames=pruned[0].keys())
                    writer.writeheader()
                    for c in pruned:
                        row = c.copy()
                        if "referenced_tables" in c and isinstance(c["referenced_tables"], list):
                            row["referenced_tables"] = ",".join(
                                t.get("full_name", t) if isinstance(t, dict) else str(t) for t in c["referenced_tables"]
                            )
                        writer.writerow(row)
                output_file.write_text(output_str.getvalue())
            else:
                formatter.error(
                    error_type="ValueError",
                    code="INVALID_OUTPUT_FORMAT",
                    message=f"Unsupported output format: {output_file.suffix}. Use .json or .csv",
                    command="candidates.prune",
                    target=str(output_file),
                )
                sys.exit(1)

        except OSError as e:
            formatter.error(
                error_type="OSError",
                code="FILE_WRITE_ERROR",
                message=f"Failed to write output file: {e}",
                command="candidates.prune",
                target=str(output_file),
            )
            sys.exit(1)

        formatter.success(
            command="candidates.prune",
            data={
                "input_count": len(candidates),
                "output_count": len(pruned),
                "filtered_count": len(candidates) - len(pruned),
                "output_file": str(output_file),
                "filters": {
                    "min_impact": min_impact,
                    "min_executions": min_executions,
                },
            },
            target=str(input_file),
        )

    except FileNotFoundError:
        formatter.error(
            error_type="FileNotFoundError",
            code="FILE_NOT_FOUND",
            message=f"Input file not found: {input_file}",
            command="candidates.prune",
            target=str(input_file),
        )
        sys.exit(1)
    except Exception as e:
        formatter.error(
            error_type="UnexpectedError",
            code="UNEXPECTED_ERROR",
            message=f"Unexpected error: {e}",
            command="candidates.prune",
            target=str(input_file),
        )
        sys.exit(1)


def _print_candidates_table(candidates: list) -> None:
    """Print candidates in human-readable table format."""
    if not candidates:
        print("No candidates found.")
        return

    # Table header
    print(f"{'Hash':<12} {'Execs':<8} {'Bytes Billed':<16} {'Impact':<10} {'Eligible':<10}")
    print("-" * 70)

    # Table rows (show first 20)
    for _i, candidate in enumerate(candidates[:20], 1):
        hash_short = candidate.get("query_hash", "")[:12]
        execs = candidate.get("execution_count", 0)

        # Format bytes
        bytes_billed = candidate.get("bytes_billed_total", 0)
        for unit, divider in [("PB", 1024**5), ("TB", 1024**4), ("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)]:
            if bytes_billed >= divider:
                bytes_str = f"{bytes_billed / divider:.2f} {unit}"
                break
        else:
            bytes_str = f"{bytes_billed} B"

        impact = candidate.get("impact_score", 0)
        eligible = candidate.get("smart_tuning_eligible", False)

        print(f"{hash_short:<12} {execs:<8} {bytes_str:<16} ${impact:<9.2f} {'Yes' if eligible else 'No':<10}")

    if len(candidates) > 20:
        print(f"... and {len(candidates) - 20} more candidates")
