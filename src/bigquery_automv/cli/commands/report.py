"""Report command for cost optimization reporting.

This module implements the report CLI command for generating cost optimization
reports for query candidates.

Tasks implemented:
- T061: Report command with QUERY_HASH positional and --from-file support
- T062: Output formatters (markdown, json, csv)
- T063: Custom pricing support via --price-per-tib
- T064: Integration with AnalyzerService
- T066: Query hash file reading
- T067: Summary section
- T068: Graceful handling of missing query hashes
"""

import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated

from cyclopts import Parameter

from bigquery_automv.cli.app import CommonConfig, app
from bigquery_automv.lib.logging import get_logger, setup_logging
from bigquery_automv.models.cost_analysis import CostAnalysisResult
from bigquery_automv.services.analyzer import AnalyzerService
from bigquery_automv.services.bq_client import (
    BigQueryClient,
    BigQueryError,
    NotFoundError,
    PermissionError,
)
from bigquery_automv.services.reporter import ReporterService

logger = get_logger("cli")


class ExitCode:
    """Exit codes for the report command."""

    SUCCESS = 0
    ERROR = 1
    PARTIAL_SUCCESS = 2


@app.command
def report(
    query_hashes: list[str],
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
            help="BigQuery on-demand pricing per TiB (default: $6.25)",
        ),
    ] = 6.25,
    from_file: Annotated[
        Path | None,
        Parameter(
            name="--from-file",
            help="Read query hashes from file (one per line)",
            parse=lambda p: Path(p) if p else None,
        ),
    ] = None,
    days: Annotated[
        int,
        Parameter(
            name="--days",
            help="Number of days to look back for analysis (default: 30)",
        ),
    ] = 30,
    start_date: Annotated[
        str | None,
        Parameter(
            name="--start-date",
            help="Start date for analysis (YYYY-MM-DD format). Overrides --days.",
        ),
    ] = None,
    end_date: Annotated[
        str | None,
        Parameter(
            name="--end-date",
            help="End date for analysis (YYYY-MM-DD format, default: today)",
        ),
    ] = None,
    output: Annotated[
        Path | None,
        Parameter(
            name="--output",
            help="Write report to file instead of stdout",
            parse=lambda p: Path(p) if p else None,
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
    """Generate cost optimization report for specific query candidates.

    Provides detailed cost analysis and projected savings for materialized view
    candidates. Supports markdown, JSON, and CSV output formats.

    Args:
        query_hashes: One or more query hashes to analyze
        format: Output format (markdown, json, csv)
        price_per_tib: Custom price per TiB in USD
        from_file: Path to file containing query hashes (one per line)
        days: Number of days to look back for analysis
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
        output: Optional output file path
        common: Common configuration options

    Examples:
        # Report on specific query hashes
        bq-automv report abc123 def456

        # Read hashes from file
        bq-automv report --from-file hashes.txt

        # Custom pricing and JSON output
        bq-automv report abc123 --price-per-tib 5.00 --format json

        # Specify date range
        bq-automv report abc123 --start-date 2024-01-01 --end-date 2024-01-31
    """
    if common is None:
        common = CommonConfig()

    # Set up logging
    logger = setup_logging(common)
    exit_code = ExitCode.SUCCESS

    try:
        # Validate format
        valid_formats = {"markdown", "json", "csv"}
        if format not in valid_formats:
            logger.error(f"Invalid format '{format}'. Must be one of: {', '.join(valid_formats)}")
            _print_error(f"Invalid format '{format}'. Must be one of: {', '.join(valid_formats)}", common.json)
            sys.exit(ExitCode.ERROR)

        # Read query hashes from file if specified (T066)
        if from_file:
            file_hashes = _read_query_hashes_from_file(from_file, common.json)
            # Combine positional and file hashes
            all_hashes = list(set(query_hashes + file_hashes))
        else:
            all_hashes = query_hashes

        if not all_hashes:
            logger.error("No query hashes provided. Use QUERY_HASH... or --from-file.")
            _print_error("No query hashes provided. Use QUERY_HASH... or --from-file.", common.json)
            sys.exit(ExitCode.ERROR)

        # Calculate date range
        start, end = _calculate_date_range(days, start_date, end_date, common.json)

        # Validate price_per_tib
        if price_per_tib <= 0:
            logger.error(f"Invalid price_per_tib: {price_per_tib}. Must be positive.")
            _print_error(f"Invalid price_per_tib: {price_per_tib}. Must be positive.", common.json)
            sys.exit(ExitCode.ERROR)

        # Run analysis asynchronously
        results, summary = asyncio.run(
            _run_analysis(
                all_hashes,
                start,
                end,
                common=common,
                price_per_tib=price_per_tib,
            )
        )

        # Check if we got results (T068: handle missing hashes gracefully)
        if not results:
            logger.error("No valid query hashes found in the specified time range.")
            _print_error("No valid query hashes found in the specified time range.", common.json)
            sys.exit(ExitCode.ERROR)

        # Log warnings for skipped hashes
        if len(results) < len(all_hashes):
            skipped = len(all_hashes) - len(results)
            logger.warning(f"{skipped} query hash(es) not found in analysis period")
            exit_code = ExitCode.PARTIAL_SUCCESS

        # Format output
        output_text = _format_output(format, results, summary)

        # Write to file or stdout
        _write_output(output_text, output, common.json)

    except PermissionError as e:
        logger.error("Permission error", message=str(e))
        _print_error(f"Permission denied: {e.message}", common.json, suggestion="Check IAM permissions for BigQuery")
        sys.exit(ExitCode.ERROR)

    except NotFoundError as e:
        logger.error("Not found error", message=str(e))
        _print_error(f"Resource not found: {e.message}", common.json, suggestion="Verify the project and dataset exist")
        sys.exit(ExitCode.ERROR)

    except BigQueryError as e:
        logger.error("BigQuery error", message=str(e))
        _print_error(f"BigQuery error: {e.message}", common.json)
        sys.exit(ExitCode.ERROR)

    except ValueError as e:
        logger.error("Validation error", message=str(e))
        _print_error(f"Invalid input: {e}", common.json)
        sys.exit(ExitCode.ERROR)

    except Exception as e:
        logger.exception("Unexpected error")
        _print_error(f"Unexpected error: {e}", common.json)
        sys.exit(ExitCode.ERROR)

    sys.exit(exit_code)


def _read_query_hashes_from_file(file_path: Path, as_json: bool) -> list[str]:
    """Read query hashes from file (T066).

    Args:
        file_path: Path to file containing query hashes (one per line)
        as_json: Whether to format errors as JSON

    Returns:
        List of query hashes

    Raises:
        SystemExit: If file cannot be read
    """
    try:
        content = file_path.read_text()
        # Split by lines and filter empty/comments
        hashes = [line.strip() for line in content.splitlines() if line.strip() and not line.strip().startswith("#")]
        logger.info(f"Read {len(hashes)} query hashes from {file_path}")
        return hashes
    except FileNotFoundError:
        logger.error(f"File not found: {file_path}")
        _print_error(f"File not found: {file_path}", as_json)
        raise SystemExit(ExitCode.ERROR) from None
    except PermissionError:
        logger.error(f"Permission denied reading file: {file_path}")
        _print_error(f"Permission denied reading file: {file_path}", as_json)
        raise SystemExit(ExitCode.ERROR) from None
    except Exception as e:
        logger.error(f"Error reading file {file_path}: {e}")
        _print_error(f"Error reading file {file_path}: {e}", as_json)
        raise SystemExit(ExitCode.ERROR) from None


def _calculate_date_range(
    days: int,
    start_date: str | None,
    end_date: str | None,
    as_json: bool,
) -> tuple[datetime, datetime]:
    """Calculate analysis date range.

    Args:
        days: Number of days to look back
        start_date: Start date in YYYY-MM-DD format (overrides days)
        end_date: End date in YYYY-MM-DD format
        as_json: Whether to format errors as JSON

    Returns:
        Tuple of (start_datetime, end_datetime)

    Raises:
        SystemExit: If date format is invalid
    """
    try:
        if end_date:
            end = datetime.strptime(end_date, "%Y-%m-%d")
        else:
            end = datetime.now()

        if start_date:
            start = datetime.strptime(start_date, "%Y-%m-%d")
        else:
            start = end - timedelta(days=days)

        # Validate date range
        if start >= end:
            logger.error("Start date must be before end date")
            _print_error("Start date must be before end date", as_json)
            raise SystemExit(ExitCode.ERROR)

        return start, end
    except ValueError as e:
        logger.error(f"Invalid date format: {e}. Use YYYY-MM-DD format.")
        _print_error(f"Invalid date format: {e}. Use YYYY-MM-DD format.", as_json)
        raise SystemExit(ExitCode.ERROR) from None


async def _run_analysis(
    query_hashes: list[str],
    start_date: datetime,
    end_date: datetime,
    *,
    common: CommonConfig,
    price_per_tib: float,
) -> tuple[list[CostAnalysisResult], object]:
    """Run cost analysis for query hashes.

    Args:
        query_hashes: List of query hashes to analyze
        start_date: Start of analysis period
        end_date: End of analysis period
        common: Common configuration
        price_per_tib: Price per TiB in USD

    Returns:
        Tuple of (results list, summary object)
    """
    logger.info(f"Analyzing {len(query_hashes)} query hash(es)...")
    logger.info(f"Analysis period: {start_date.date()} to {end_date.date()}")
    logger.info(f"Project: {common.project}")

    # Initialize services
    async with BigQueryClient(
        project_id=common.project if common.project else None,
        region=common.region,
    ) as client:
        analyzer = AnalyzerService(client)
        reporter = ReporterService(analyzer)

        results = await reporter.analyze_multiple_queries(
            query_hashes,
            start_date,
            end_date,
            project_id=common.project,
            price_per_tib=price_per_tib,
        )

        logger.info(f"Successfully analyzed {len(results)} query hash(es)")

        # Generate summary
        summary = reporter.generate_summary(results)

        return results, summary


def _format_output(
    format: str,
    results: list[CostAnalysisResult],
    summary,
) -> str:
    """Format analysis results (T062).

    Args:
        format: Output format (markdown, json, csv)
        results: List of CostAnalysisResult objects
        summary: ReportSummary object

    Returns:
        Formatted output string

    Raises:
        ValueError: If format is invalid
    """
    from bigquery_automv.services.reporter import ReporterService

    # Create a dummy reporter for formatting (stateless)
    dummy_reporter = ReporterService(None)  # type: ignore[arg-type]

    if format == "markdown":
        return dummy_reporter.format_markdown(results, summary)
    if format == "json":
        return dummy_reporter.format_json(results, summary)
    if format == "csv":
        return dummy_reporter.format_csv(results, summary)

    # Should not reach here due to earlier validation
    raise ValueError(f"Unsupported format: {format}")


def _write_output(output_text: str, output_path: Path | None, as_json: bool) -> None:
    """Write output to file or stdout.

    Args:
        output_text: Formatted output text
        output_path: Optional output file path
        as_json: Whether to format errors as JSON

    Raises:
        SystemExit: If file write fails
    """
    if output_path:
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(output_text)
            print(f"Report written to {output_path}", file=sys.stderr)
        except OSError as e:
            logger.error(f"Failed to write output file: {e}")
            _print_error(f"Failed to write output file: {e}", as_json)
            raise SystemExit(ExitCode.ERROR) from None
    else:
        print(output_text)


def _print_error(message: str, as_json: bool, suggestion: str | None = None) -> None:
    """Print error message to stderr.

    Args:
        message: Error message
        as_json: Whether to format as JSON
        suggestion: Optional suggestion for fixing the error
    """
    import json

    if as_json:
        error_data = {
            "error": "ReportError",
            "message": message,
        }
        if suggestion:
            error_data["suggestion"] = suggestion
        print(json.dumps(error_data), file=sys.stderr)
    else:
        print(f"Error: {message}", file=sys.stderr)
        if suggestion:
            print(f"Suggestion: {suggestion}", file=sys.stderr)
