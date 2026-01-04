"""Impact command for materialized view usage reporting."""

import asyncio
import csv
import json
import sys
from datetime import date
from pathlib import Path
from typing import Annotated

from cyclopts import Parameter

from bigquery_automv.cli.app import CommonConfig, app
from bigquery_automv.lib.config import ImpactScoringConfig
from bigquery_automv.lib.logging import setup_logging
from bigquery_automv.models.impact_report import ImpactReport
from bigquery_automv.services.bq_client import (
    BigQueryClient,
    BigQueryError,
    NotFoundError,
    PermissionError,
)
from bigquery_automv.services.impact import ImpactService


class ExitCode:
    """Exit codes for the impact command."""

    SUCCESS = 0
    ERROR = 1
    NO_USAGE = 2


async def _run_impact_analysis(
    start_date: date,
    end_date: date,
    baseline_mode: str,
    baseline_start: date | None,
    baseline_end: date | None,
    mv_name_filter: str | None,
    common: CommonConfig,
    impact_config: ImpactScoringConfig,
) -> ImpactReport:
    """Run the impact analysis asynchronously.

    Args:
        start_date: Start of analysis window
        end_date: End of analysis window
        baseline_mode: Baseline comparison mode
        baseline_start: Baseline start date (for explicit_range mode)
        baseline_end: Baseline end date (for explicit_range mode)
        mv_name_filter: Optional MV name filter
        common: Common configuration
        impact_config: Impact scoring configuration

    Returns:
        ImpactReport

    Raises:
        ValueError: If configuration is invalid
        BigQueryError: If query fails
    """
    # Setup logging
    logger = setup_logging(common)

    logger.info(
        "Starting BigQuery impact analysis",
        extra={
            "project": common.project,
            "region": common.region,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "baseline_mode": baseline_mode,
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
        # Create impact service
        impact_service = ImpactService(
            client=client,
            impact_config=impact_config,
        )

        # Run impact analysis
        result = await impact_service.generate_impact_report(
            start_date=start_date,
            end_date=end_date,
            baseline_mode=baseline_mode,
            baseline_start=baseline_start,
            baseline_end=baseline_end,
            mv_name_filter=mv_name_filter,
        )

        return result


@app.command
def impact(
    start_date: Annotated[
        date,
        Parameter(
            name="--start-date",
            help="Start of impact analysis period (YYYY-MM-DD format)",
        ),
    ],
    end_date: Annotated[
        date,
        Parameter(
            name="--end-date",
            help="End of impact analysis period (YYYY-MM-DD format)",
        ),
    ] = date.today(),  # noqa: B008
    baseline_mode: Annotated[
        str,
        Parameter(
            name="--baseline-mode",
            help='Baseline mode: "none", "previous_period", "explicit_range"',
        ),
    ] = "none",
    baseline_start: Annotated[
        date | None,
        Parameter(
            name="--baseline-start",
            help="Baseline start (required when baseline_mode=explicit_range)",
        ),
    ] = None,
    baseline_end: Annotated[
        date | None,
        Parameter(
            name="--baseline-end",
            help="Baseline end (required when baseline_mode=explicit_range)",
        ),
    ] = None,
    mv_name: Annotated[
        str | None,
        Parameter(
            name="--mv-name",
            help="Filter to specific MV name",
        ),
    ] = None,
    output: Annotated[
        Path | None,
        Parameter(
            name="--output",
            help="Write results to file (JSON/Markdown/CSV)",
            parse=lambda p: Path(p) if p else None,
        ),
    ] = None,
    format: Annotated[
        str,
        Parameter(
            name="--format",
            help='Output format: "markdown", "json", "csv"',
        ),
    ] = "markdown",
    price_per_tib: Annotated[
        float,
        Parameter(
            name="--price-per-tib",
            help="BigQuery on-demand price per TiB for savings calculation",
        ),
    ] = 6.25,
    *,
    common: Annotated[
        CommonConfig | None,
        Parameter(
            name="*",
            help="Common configuration options",
        ),
    ] = None,
) -> str:
    """Report on materialized view usage and savings.

    Analyzes the effectiveness of deployed materialized views by tracking
    Smart Tuning usage patterns and cost savings. Supports baseline
    comparisons for before/after analysis.

    Features:
        - MV usage detection via materialized_view_statistics
        - Matching executions query
        - Baseline mode support (none, previous_period, explicit_range)
        - Savings calculation (bytes_saved, slot_ms_saved, dollars_saved)
        - Per-MV breakdown
        - Unused MV detection
        - Output formats: markdown, json, csv

    Example:
        ```bash
        bq-automv impact \\
          --start-date 2024-01-01 \\
          --end-date 2024-01-31 \\
          --baseline-mode previous_period \\
          --format markdown
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

        # Validate baseline mode
        valid_baseline_modes = ["none", "previous_period", "explicit_range"]
        if baseline_mode not in valid_baseline_modes:
            logger.error("Invalid baseline mode")
            print_error(
                f"Invalid baseline_mode: {baseline_mode}. Must be one of: {', '.join(valid_baseline_modes)}",
                common.json,
            )
            sys.exit(ExitCode.ERROR)

        # Validate explicit range parameters
        if baseline_mode == "explicit_range":
            if not baseline_start or not baseline_end:
                logger.error("baseline_start and baseline_end required for explicit_range mode")
                print_error(
                    "baseline_start and baseline_end are required when baseline_mode is 'explicit_range'",
                    common.json,
                )
                sys.exit(ExitCode.ERROR)

            if baseline_end < baseline_start:
                logger.error("Baseline end date must be on or after baseline start date")
                print_error(
                    "Invalid baseline date range: baseline_end must be on or after baseline_start",
                    common.json,
                )
                sys.exit(ExitCode.ERROR)

        # Validate output format
        valid_formats = ["markdown", "json", "csv"]
        if format not in valid_formats:
            logger.error("Invalid output format")
            print_error(
                f"Invalid format: {format}. Must be one of: {', '.join(valid_formats)}",
                common.json,
            )
            sys.exit(ExitCode.ERROR)

        # Create configuration objects
        impact_config = ImpactScoringConfig(
            price_per_tib=price_per_tib,
        )

        logger.info(
            "Starting impact analysis",
            extra={
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "baseline_mode": baseline_mode,
                "mv_name_filter": mv_name,
                "format": format,
            },
        )

        # Run impact analysis asynchronously
        result = asyncio.run(
            _run_impact_analysis(
                start_date=start_date,
                end_date=end_date,
                baseline_mode=baseline_mode,
                baseline_start=baseline_start,
                baseline_end=baseline_end,
                mv_name_filter=mv_name,
                common=common,
                impact_config=impact_config,
            )
        )

        # T112: Handle no-usage scenario gracefully
        if not result.mv_impacts and not result.unused_mvs:
            logger.info("No MV usage found in period")
            print_no_usage_warning(result, common.json, format)
            sys.exit(ExitCode.NO_USAGE)

        # Format and output results
        format_and_output(result, output, format, common.json)

        logger.info(
            "Impact analysis complete",
            extra={
                "total_mvs_analyzed": len(result.mv_impacts),
                "unused_mvs": len(result.unused_mvs),
                "total_bytes_saved": result.total_bytes_saved,
                "total_dollars_saved": result.total_dollars_saved_on_demand_equiv,
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


def format_and_output(result: ImpactReport, output: Path | None, output_format: str, as_json: bool) -> None:
    """Format and output impact report results.

    Args:
        result: ImpactReport from impact service
        output: Optional output file path
        output_format: Output format (markdown, json, csv)
        as_json: Whether to use JSON format (legacy parameter)
    """
    # Use output_format parameter, falling back to as_json for backward compatibility
    if output_format == "json" or (as_json and output_format == "markdown"):
        output_format = "json"

    # Format output
    if output_format == "json":
        output_data = format_json(result)
    elif output_format == "csv":
        output_data = format_csv(result)
    else:  # markdown
        output_data = format_markdown(result)

    # Write to file or stdout
    if output:
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(output_data)
            print(f"Results written to {output}", file=sys.stderr)
        except OSError as e:
            print_error(f"Failed to write output file: {e}", False)
            sys.exit(ExitCode.ERROR)
    else:
        print(output_data)


def format_json(result: ImpactReport) -> str:
    """T108: Format impact report as JSON.

    Args:
        result: ImpactReport from impact service

    Returns:
        JSON string
    """
    # Format per-MV impacts
    mv_impacts = []
    for impact in result.mv_impacts:
        mv_impacts.append(
            {
                "mv_name": impact.mv_name,
                "source_query_hash": impact.source_query_hash,
                "baseline": {
                    "avg_bytes_processed": impact.baseline_avg_bytes_processed,
                    "avg_slot_ms": impact.baseline_avg_slot_ms,
                    "execution_count": impact.baseline_execution_count,
                }
                if impact.baseline_execution_count is not None
                else None,
                "current": {
                    "avg_bytes_processed": impact.current_avg_bytes_processed,
                    "avg_slot_ms": impact.current_avg_slot_ms,
                    "execution_count": impact.current_execution_count,
                },
                "savings": {
                    "bytes_saved_per_execution": impact.bytes_saved_per_execution,
                    "slot_ms_saved_per_execution": impact.slot_ms_saved_per_execution,
                    "total_bytes_saved": impact.total_bytes_saved,
                    "total_slot_ms_saved": impact.total_slot_ms_saved,
                    "dollars_saved_on_demand_equiv": round(impact.dollars_saved_on_demand_equiv, 2),
                },
                "usage": {
                    "matching_executions_count": impact.matching_executions_count,
                    "smart_tuning_usage_count": impact.smart_tuning_usage_count,
                    "direct_query_count": impact.direct_query_count,
                    "usage_percentage": round(impact.usage_percentage, 2),
                },
                "attribution_method": impact.attribution_method,
                "status": impact.status.value,
            }
        )

    output = {
        "meta": {
            "version": "1.0.0",
            "generated_at": result.generated_at.isoformat(),
            "command": "impact",
            "report_id": result.report_id,
        },
        "measurement_period": {
            "start_date": result.period_start.strftime("%Y-%m-%d"),
            "end_date": result.period_end.strftime("%Y-%m-%d"),
        },
        "baseline_period": (
            {
                "start_date": result.baseline_period_start.strftime("%Y-%m-%d")
                if result.baseline_period_start
                else None,
                "end_date": result.baseline_period_end.strftime("%Y-%m-%d") if result.baseline_period_end else None,
                "mode": result.baseline_mode,
            }
            if result.baseline_mode != "none"
            else None
        ),
        "summary": {
            "total_bytes_saved": result.total_bytes_saved,
            "total_slot_ms_saved": result.total_slot_ms_saved,
            "total_dollars_saved_on_demand_equiv": round(result.total_dollars_saved_on_demand_equiv, 2),
            "total_queries_accelerated": result.total_queries_accelerated,
            "matching_executions_count": result.matching_executions_count,
            "total_mvs_analyzed": len(result.mv_impacts),
            "unused_mvs_count": len(result.unused_mvs),
        },
        "mv_impacts": mv_impacts,
        "unused_mvs": result.unused_mvs,
    }

    return json.dumps(output, indent=2)


def format_csv(result: ImpactReport) -> str:
    """T108: Format impact report as CSV.

    Args:
        result: ImpactReport from impact service

    Returns:
        CSV string
    """
    import io

    output = io.StringIO()
    writer = csv.writer(output)

    # Write metadata as comments
    measurement_period_str = f"{result.period_start.strftime('%Y-%m-%d')} to {result.period_end.strftime('%Y-%m-%d')}"
    writer.writerow(
        [
            f"# Generated: {result.generated_at.isoformat()}",
            f"# Report ID: {result.report_id}",
            f"# Measurement Period: {measurement_period_str}",
        ]
    )

    if result.baseline_mode != "none" and result.baseline_period_start and result.baseline_period_end:
        writer.writerow(
            [
                f"# Baseline Period: {result.baseline_period_start.strftime('%Y-%m-%d')} to "
                f"{result.baseline_period_end.strftime('%Y-%m-%d')}",
                f"# Baseline Mode: {result.baseline_mode}",
            ]
        )

    writer.writerow([])  # Empty row after comments

    # Write summary
    writer.writerow(["Summary"])
    writer.writerow(["Total Bytes Saved", result.total_bytes_saved])
    writer.writerow(["Total Slot MS Saved", result.total_slot_ms_saved])
    writer.writerow(["Total Dollars Saved", f"${result.total_dollars_saved_on_demand_equiv:.2f}"])
    writer.writerow(["Total Queries Accelerated", result.total_queries_accelerated])
    writer.writerow(["Total MVs Analyzed", len(result.mv_impacts)])
    writer.writerow(["Unused MVs Count", len(result.unused_mvs)])
    writer.writerow([])

    # Write per-MV impacts
    writer.writerow(["Per-MV Impact"])
    header = [
        "MV Name",
        "Baseline Avg Bytes",
        "Current Avg Bytes",
        "Bytes Saved/Exec",
        "Total Bytes Saved",
        "Slot MS Saved/Exec",
        "Total Slot MS Saved",
        "Dollars Saved",
        "Smart Tuning Usage",
        "Matching Executions",
        "Usage %",
        "Status",
    ]
    writer.writerow(header)

    for impact in result.mv_impacts:
        row = [
            impact.mv_name,
            impact.baseline_avg_bytes_processed if impact.baseline_avg_bytes_processed else "N/A",
            impact.current_avg_bytes_processed,
            impact.bytes_saved_per_execution,
            impact.total_bytes_saved,
            impact.slot_ms_saved_per_execution,
            impact.total_slot_ms_saved,
            f"${impact.dollars_saved_on_demand_equiv:.2f}",
            impact.smart_tuning_usage_count,
            impact.matching_executions_count,
            f"{impact.usage_percentage:.1f}%",
            impact.status.value,
        ]
        writer.writerow(row)

    # Write unused MVs
    if result.unused_mvs:
        writer.writerow([])
        writer.writerow(["Unused MVs"])
        for mv_name in result.unused_mvs:
            writer.writerow([mv_name])

    return output.getvalue()


def format_markdown(result: ImpactReport) -> str:
    """T108: Format impact report as Markdown.

    Args:
        result: ImpactReport from impact service

    Returns:
        Markdown string
    """
    lines = []

    # T109: Summary section
    lines.append("# Materialized View Impact Report")
    lines.append("")
    lines.append(f"**Generated:** {result.generated_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    lines.append(f"**Report ID:** `{result.report_id}`")
    lines.append("")

    # Measurement period
    lines.append("## Measurement Period")
    lines.append("")
    lines.append(f"- **Start:** {result.period_start.strftime('%Y-%m-%d')}")
    lines.append(f"- **End:** {result.period_end.strftime('%Y-%m-%d')}")
    lines.append("")

    # Baseline period
    if result.baseline_mode != "none" and result.baseline_period_start and result.baseline_period_end:
        lines.append("## Baseline Period")
        lines.append("")
        lines.append(f"- **Mode:** {result.baseline_mode}")
        lines.append(f"- **Start:** {result.baseline_period_start.strftime('%Y-%m-%d')}")
        lines.append(f"- **End:** {result.baseline_period_end.strftime('%Y-%m-%d')}")
        lines.append("")

    # Summary statistics
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- **Total Bytes Saved:** {format_bytes(result.total_bytes_saved)}")
    lines.append(f"- **Total Slot MS Saved:** {format_number(result.total_slot_ms_saved)}")
    lines.append(f"- **Total Dollars Saved:** ${result.total_dollars_saved_on_demand_equiv:.2f}")
    lines.append(f"- **Total Queries Accelerated:** {result.total_queries_accelerated:,}")
    lines.append(f"- **Total MVs Analyzed:** {len(result.mv_impacts)}")
    lines.append(f"- **Unused MVs:** {len(result.unused_mvs)}")
    lines.append("")

    # T110: Per-MV breakdown table
    if result.mv_impacts:
        lines.append("## Per-MV Impact")
        lines.append("")
        table_header = (
            "| MV Name | Baseline Avg Bytes | Current Avg Bytes | Bytes Saved/Exec | "
            "Total Bytes Saved | Dollars Saved | Smart Tuning Usage | Matching Execs | "
            "Usage % | Status |"
        )
        lines.append(table_header)
        table_separator = (
            "|---------|-------------------|-------------------|------------------|"
            "-------------------|---------------|-------------------|----------------|"
            "---------|--------|"
        )
        lines.append(table_separator)

        for impact in result.mv_impacts:
            baseline_bytes = (
                format_bytes(impact.baseline_avg_bytes_processed) if impact.baseline_avg_bytes_processed else "N/A"
            )
            current_bytes = format_bytes(impact.current_avg_bytes_processed)
            bytes_saved_exec = format_bytes(impact.bytes_saved_per_execution)
            total_bytes_saved = format_bytes(impact.total_bytes_saved)
            dollars_saved = f"${impact.dollars_saved_on_demand_equiv:.2f}"
            smart_tuning_usage = f"{impact.smart_tuning_usage_count:,}"
            matching_execs = f"{impact.matching_executions_count:,}"
            usage_pct = f"{impact.usage_percentage:.1f}%"
            status = impact.status.value

            row_str = (
                f"| {impact.mv_name} | {baseline_bytes} | {current_bytes} | "
                f"{bytes_saved_exec} | {total_bytes_saved} | {dollars_saved} | "
                f"{smart_tuning_usage} | {matching_execs} | {usage_pct} | {status} |"
            )
            lines.append(row_str)

        lines.append("")

    # T111: Unused MVs section
    if result.unused_mvs:
        lines.append("## Unused Materialized Views")
        lines.append("")
        lines.append("The following materialized views had zero Smart Tuning usage during the measurement period:")
        lines.append("")

        for mv_name in result.unused_mvs:
            lines.append(f"- `{mv_name}`")

        lines.append("")

    return "\n".join(lines)


def format_bytes(byte_count: int) -> str:
    """Format byte count as human-readable string.

    Args:
        byte_count: Number of bytes

    Returns:
        Formatted string (e.g., "1.23 GB")
    """
    for unit, divider in [("PB", 1024**5), ("TB", 1024**4), ("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)]:
        if byte_count >= divider:
            return f"{byte_count / divider:.2f} {unit}"
    return f"{byte_count} B"


def format_number(count: int) -> str:
    """Format number with thousands separator.

    Args:
        count: Number to format

    Returns:
        Formatted string
    """
    return f"{count:,}"


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


def print_no_usage_warning(result: ImpactReport, as_json: bool, output_format: str) -> None:
    """T112: Handle no-usage scenario gracefully.

    Args:
        result: ImpactReport with no usage
        as_json: Whether to format as JSON
        output_format: Output format
    """
    if output_format == "json" or as_json:
        warning_data = {
            "warning": "No MV usage found",
            "message": "No materialized view usage was detected during the measurement period",
            "report_id": result.report_id,
            "measurement_period": {
                "start_date": result.period_start.strftime("%Y-%m-%d"),
                "end_date": result.period_end.strftime("%Y-%m-%d"),
            },
        }
        print(json.dumps(warning_data, indent=2), file=sys.stderr)
    else:
        print(
            f"Warning: No materialized view usage was detected during the measurement period "
            f"({result.period_start.strftime('%Y-%m-%d')} to {result.period_end.strftime('%Y-%m-%d')})",
            file=sys.stderr,
        )
        print("This could mean:", file=sys.stderr)
        print("  - No materialized views have been created yet", file=sys.stderr)
        print("  - Materialized views exist but haven't been used by Smart Tuning", file=sys.stderr)
        print("  - The measurement period doesn't contain any query activity", file=sys.stderr)
