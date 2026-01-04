"""Analyze command for BigQuery query pattern analysis."""

import asyncio
import csv
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Annotated

from cyclopts import Parameter

from bigquery_automv.cli.app import CommonConfig, app
from bigquery_automv.lib.config import AnalysisConfig, ImpactScoringConfig
from bigquery_automv.lib.logging import setup_logging
from bigquery_automv.services.analyzer import AnalysisResult, AnalyzerService
from bigquery_automv.services.bq_client import (
    BigQueryClient,
    BigQueryError,
    NotFoundError,
    PermissionError,
)
from bigquery_automv.services.smart_tuning import SmartTuningService


class ExitCode:
    """Exit codes for the analyze command."""

    SUCCESS = 0
    ERROR = 1
    PARTIAL_SUCCESS = 2


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
    """Run the analysis asynchronously.

    Args:
        start_date: Start of analysis window
        end_date: End of analysis window
        common: Common configuration
        analysis_config: Analysis configuration
        impact_config: Impact scoring configuration
        include_ineligible: Include candidates that are not eligible for Smart Tuning
        persist: Persist candidates to BigQuery table
        persist_dataset: Dataset for persisting candidates (defaults to common.dataset)

    Returns:
        AnalysisResult

    Raises:
        ValueError: If configuration is invalid
        BigQueryError: If query fails
    """
    # Setup logging (T035)
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
    """Persist analysis candidates to BigQuery table.

    Args:
        client: BigQuery client
        result: Analysis result with candidates
        dataset_id: Target dataset ID
        start_date: Analysis start date
        end_date: Analysis end date
    """

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


@app.command
def analyze(
    start_date: Annotated[
        date,
        Parameter(
            name="--start-date",
            help="Start of analysis window (inclusive, YYYY-MM-DD format)",
        ),
    ],
    end_date: Annotated[
        date,
        Parameter(
            name="--end-date",
            help="End of analysis window (inclusive, YYYY-MM-DD format)",
        ),
    ] = date.today(),  # noqa: B008
    min_executions: Annotated[
        int,
        Parameter(
            name="--min-executions",
            help="Minimum execution count per query family",
        ),
    ] = 10,
    min_bytes: Annotated[
        int,
        Parameter(
            name="--min-bytes",
            help="Minimum bytes processed threshold",
        ),
    ] = 1073741824,  # 1GB
    min_slot_ms: Annotated[
        int,
        Parameter(
            name="--min-slot-ms",
            help="Minimum slot milliseconds threshold",
        ),
    ] = 0,
    max_families: Annotated[
        int,
        Parameter(
            name="--max-families",
            help="Maximum number of query families to return",
        ),
    ] = 100,
    output: Annotated[
        Path | None,
        Parameter(
            name="--output",
            help="Write results to file (JSON/CSV)",
            parse=lambda p: Path(p) if p else None,
        ),
    ] = None,
    price_per_tib: Annotated[
        float,
        Parameter(
            name="--price-per-tib",
            help="BigQuery on-demand price per TiB for impact scoring",
        ),
    ] = 6.25,
    slot_weight: Annotated[
        float,
        Parameter(
            name="--slot-weight",
            help="Weight for slot component in impact score",
        ),
    ] = 0.25,
    slot_ms_per_tib_equivalent: Annotated[
        float,
        Parameter(
            name="--slot-ms-per-tib-equivalent",
            help="Slot-ms to TiB equivalent conversion factor",
        ),
    ] = 3.6e9,
    sample_size_k: Annotated[
        int,
        Parameter(
            name="--sample-size-k",
            help="Number of sample queries for MV synthesis",
        ),
    ] = 20,
    rulebook_version: Annotated[
        str,
        Parameter(
            name="--rulebook-version",
            help="Smart Tuning rulebook version for eligibility checks",
        ),
    ] = "latest",
    include_ineligible: Annotated[
        bool,
        Parameter(
            name="--include-ineligible",
            help="Include candidates that are not eligible for Smart Tuning",
            negative=False,
        ),
    ] = False,
    persist: Annotated[
        bool,
        Parameter(
            name="--persist",
            help="Persist candidates to BigQuery table for later use with generate-mv",
            negative=False,
        ),
    ] = False,
    persist_dataset: Annotated[
        str | None,
        Parameter(
            name="--persist-dataset",
            help="Dataset for persisting candidates (defaults to --dataset if not set)",
        ),
    ] = None,
    *,
    common: Annotated[
        CommonConfig | None,
        Parameter(
            name="*",
            help="Common configuration options",
        ),
    ] = None,
) -> None:
    """Analyze BigQuery INFORMATION_SCHEMA.JOBS to identify expensive query patterns.

    This command analyzes historical query patterns to find candidates for
    materialized views that can leverage BigQuery Smart Tuning for automatic
    query rerouting.

    Features:
        - Pagination support for >1M jobs (T031)
        - Exclusion filters (SCRIPT, cache hits, NULL bytes_billed with warnings) (T032, T036)
        - Region validation (T033)
        - CommonConfig integration (T034)
        - Structured logging with runtime metrics (T035)

    Example:
        ```bash
        bq-automv analyze \\
          --start-date 2024-01-01 \\
          --end-date 2024-01-31 \\
          --min-executions 50 \\
          --output results.json
        ```
    """
    if common is None:
        common = CommonConfig()

    # Set up logging
    logger = setup_logging(common)
    exit_code = ExitCode.SUCCESS

    try:
        # Validate date range
        if end_date < start_date:
            logger.error("End date must be on or after start date")
            print_error("Invalid date range: end_date must be on or after start_date", common.json)
            sys.exit(ExitCode.ERROR)

        # Create configuration objects (T034)
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
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "min_executions": min_executions,
                "min_bytes": min_bytes,
                "min_slot_ms": min_slot_ms,
                "max_families": max_families,
            },
        )

        # Run analysis asynchronously
        result = asyncio.run(
            _run_analysis(
                start_date=start_date,
                end_date=end_date,
                common=common,
                analysis_config=analysis_config,
                impact_config=impact_config,
                include_ineligible=include_ineligible,
                persist=persist,
                persist_dataset=persist_dataset,
            )
        )

        # Format and output results
        format_and_output(result, output, common.json)

        logger.info(
            "Analysis complete",
            extra={
                "total_candidates": len(result.candidates),
                "families_analyzed": result.metrics.families_analyzed,
                "total_jobs_scanned": result.metrics.total_jobs_scanned,
            },
        )

    except PermissionError as e:
        logger.error("Permission error", extra={"error": str(e)})
        print_error(f"Permission denied: {e.message}", common.json, suggestion="Check IAM permissions for BigQuery")
        sys.exit(ExitCode.ERROR)

    except NotFoundError as e:
        logger.error("Not found error", extra={"error": str(e)})
        print_error(f"Resource not found: {e.message}", common.json, suggestion="Verify the project and dataset exist")
        sys.exit(ExitCode.ERROR)

    except BigQueryError as e:
        logger.error("BigQuery error", extra={"error": str(e)})
        print_error(f"BigQuery error: {e.message}", common.json)
        sys.exit(ExitCode.ERROR)

    except ValueError as e:
        logger.error("Validation error", extra={"error": str(e)})
        print_error(f"Invalid input: {e}", common.json)
        sys.exit(ExitCode.ERROR)

    except Exception as e:
        logger.exception("Unexpected error")
        print_error(f"Unexpected error: {e}", common.json)
        sys.exit(ExitCode.ERROR)

    sys.exit(exit_code)


def format_and_output(result: AnalysisResult, output: Path | None, as_json: bool) -> None:
    """Format and output analysis results.

    Args:
        result: AnalysisResult from analyzer
        output: Optional output file path
        as_json: Whether to use JSON format (vs human-readable table)
    """
    # Determine output format
    if output:
        # Auto-detect format from file extension
        output_format = detect_format_from_path(output)
    else:
        # Use JSON flag or default to table
        output_format = "json" if as_json else "table"

    # Format output
    if output_format == "json":
        output_data = format_json(result)
    elif output_format == "csv":
        output_data = format_csv(result)
    else:
        output_data = format_table(result)

    # Write to file or stdout
    if output:
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(output_data)
            print(f"Results written to {output}", file=sys.stderr)
        except OSError as e:
            print_error(f"Failed to write output file: {e}", as_json)
            sys.exit(ExitCode.ERROR)
    else:
        print(output_data)


def detect_format_from_path(path: Path) -> str:
    """Detect output format from file extension.

    Args:
        path: File path

    Returns:
        Format string: "json", "csv", or "table"

    Raises:
        ValueError: If extension is not supported
    """
    ext = path.suffix.lower()
    if ext == ".json":
        return "json"
    if ext == ".csv":
        return "csv"
    msg = f"Unsupported output format: {ext}. Use .json or .csv"
    raise ValueError(msg)


def format_json(result: AnalysisResult) -> str:
    """Format analysis result as JSON.

    Args:
        result: AnalysisResult from analyzer

    Returns:
        JSON string
    """
    days_analyzed = (result.end_date - result.start_date).days + 1

    # Format candidates as dictionaries
    candidates = []
    for candidate in result.candidates:
        candidate_dict = {
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

        if candidate.smart_tuning_reasons:
            candidate_dict["smart_tuning_reasons"] = candidate.smart_tuning_reasons

        candidates.append(candidate_dict)

    output = {
        "meta": {
            "version": "1.0.0",
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "command": "analyze",
            "impact_model_version": "v1.0",
            "project": result.project_id,
            "region": result.region,
        },
        "analysis_period": {
            "start_date": result.start_date.strftime("%Y-%m-%d"),
            "end_date": result.end_date.strftime("%Y-%m-%d"),
            "days_analyzed": days_analyzed,
        },
        "metrics": {
            "total_jobs_scanned": result.metrics.total_jobs_scanned,
            "families_analyzed": result.metrics.families_analyzed,
            "skipped_with_reasons": result.metrics.skipped_with_reasons,
            "runtime_per_phase": result.metrics.runtime_per_phase,
        },
        "candidates": candidates,
    }

    return json.dumps(output, indent=2)


def format_csv(result: AnalysisResult) -> str:
    """Format analysis result as CSV.

    Args:
        result: AnalysisResult from analyzer

    Returns:
        CSV string
    """
    import io

    output = io.StringIO()
    writer = csv.writer(output)

    # Write metadata as comment
    writer.writerow(
        [
            f"# Generated: {datetime.utcnow().isoformat()}",
            f"# Project: {result.project_id}",
            f"# Region: {result.region}",
            f"# Period: {result.start_date.strftime('%Y-%m-%d')} to {result.end_date.strftime('%Y-%m-%d')}",
        ]
    )
    writer.writerow([])  # Empty row after comments

    # Write header
    header = [
        "query_hash",
        "representative_query",
        "execution_count",
        "total_bytes_billed",
        "smart_tuning_eligible",
        "eligibility_basis",
        "impact_score",
        "dollar_cost_est_on_demand",
        "referenced_tables",
    ]

    writer.writerow(header)

    # Write candidates
    for candidate in result.candidates:
        row = [
            candidate.query_hash,
            candidate.representative_query,
            candidate.execution_count,
            candidate.bytes_billed_total,
            candidate.smart_tuning_eligible,
            candidate.eligibility_basis,
            round(candidate.impact_score, 2),
            round(candidate.dollar_cost_est_on_demand, 2),
            ",".join(t.full_name for t in candidate.referenced_tables),
        ]

        writer.writerow(row)

    return output.getvalue()


def format_table(result: AnalysisResult) -> str:
    """Format analysis result as human-readable table.

    Args:
        result: AnalysisResult from analyzer

    Returns:
        Table string
    """
    lines = []

    # Header
    lines.append("BigQuery Query Analysis Report")
    lines.append("=" * 60)
    lines.append("")

    # Metadata
    lines.append(f"Generated: {datetime.utcnow().isoformat()}")
    lines.append(f"Project: {result.project_id}")
    lines.append(f"Region: {result.region}")
    lines.append("")

    # Analysis Period
    days_analyzed = (result.end_date - result.start_date).days + 1
    lines.append("Analysis Period:")
    lines.append(f"  Start: {result.start_date.strftime('%Y-%m-%d')}")
    lines.append(f"  End: {result.end_date.strftime('%Y-%m-%d')}")
    lines.append(f"  Days: {days_analyzed}")
    lines.append("")

    # Metrics (T035: structured logging output)
    lines.append("Metrics:")
    lines.append(f"  Jobs scanned: {result.metrics.total_jobs_scanned:,}")
    lines.append(f"  Families analyzed: {result.metrics.families_analyzed:,}")
    if result.metrics.skipped_with_reasons:
        lines.append("  Skipped queries:")
        for reason, count in result.metrics.skipped_with_reasons.items():
            lines.append(f"    {reason}: {count:,}")
    if result.metrics.runtime_per_phase:
        lines.append("  Runtime per phase:")
        for phase, runtime in result.metrics.runtime_per_phase.items():
            lines.append(f"    {phase}: {runtime:.2f}s")
    lines.append("")

    # Candidates
    if result.candidates:
        lines.append("Top Candidates:")
        lines.append("")

        # Table header
        lines.append(f"{'Rank':<6} {'Hash':<12} {'Execs':<8} {'Bytes Billed':<16} {'Eligible':<10} {'Impact':<10}")
        lines.append("-" * 80)

        # Table rows
        for i, candidate in enumerate(result.candidates[:10], 1):
            hash_short = candidate.query_hash[:12]
            execs = candidate.execution_count
            bytes_billed = format_bytes(candidate.bytes_billed_total)
            eligible = "Yes" if candidate.smart_tuning_eligible else "No"
            impact = f"${candidate.impact_score:.2f}"

            lines.append(f"{i:<6} {hash_short:<12} {execs:<8} {bytes_billed:<16} {eligible:<10} {impact:<10}")

            # Always show truncated query preview
            query_preview = (
                candidate.representative_query[:80] + "..."
                if len(candidate.representative_query) > 80
                else candidate.representative_query
            )
            # Remove newlines for table compactness
            query_preview = query_preview.replace("\n", " ")
            lines.append(f"       Query: {query_preview}")

        if len(result.candidates) > 10:
            lines.append(f"... and {len(result.candidates) - 10} more candidates")
    else:
        lines.append("No candidates found matching criteria.")

    return "\n".join(lines)


def format_bytes(byte_count: int) -> str:
    """Format byte count as human-readable string.

    Args:
        byte_count: Number of bytes

    Returns:
        Formatted string (e.g., "1.23 GB")
    """
    for unit, divider in [
        ("PB", 1024**5),
        ("TB", 1024**4),
        ("GB", 1024**3),
        ("MB", 1024**2),
        ("KB", 1024),
    ]:
        if byte_count >= divider:
            return f"{byte_count / divider:.2f} {unit}"
    return f"{byte_count} B"


def print_error(message: str, as_json: bool, suggestion: str | None = None) -> None:
    """Print error message to stderr.

    Args:
        message: Error message
        as_json: Whether to format as JSON
        suggestion: Optional suggestion for fixing the error
    """
    if as_json:
        error_data = {
            "error": "BigQueryError",
            "message": message,
        }
        if suggestion:
            error_data["suggestion"] = suggestion
        print(json.dumps(error_data), file=sys.stderr)
    else:
        print(f"Error: {message}", file=sys.stderr)
        if suggestion:
            print(f"Suggestion: {suggestion}", file=sys.stderr)
