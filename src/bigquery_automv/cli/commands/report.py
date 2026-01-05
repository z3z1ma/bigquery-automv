"""Report command for cost optimization reporting."""

import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path

import click

from bigquery_automv.cli.app import app
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


async def _run_analysis(
    query_hashes: list[str],
    start_date: datetime,
    end_date: datetime,
    *,
    common: object,
    price_per_tib: float,
) -> tuple[list[CostAnalysisResult], object]:
    """Run cost analysis for query hashes."""
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


@app.command()
@click.argument("query_hashes", nargs=-1)
@click.option(
    "--format",
    type=click.Choice(["markdown", "json", "csv"]),
    default="markdown",
    help="Output format",
)
@click.option(
    "--price-per-tib",
    default=6.25,
    help="BigQuery on-demand pricing per TiB (default: $6.25)",
)
@click.option(
    "--from-file",
    type=click.Path(path_type=Path),
    help="Read query hashes from file (one per line)",
)
@click.option(
    "--days",
    default=30,
    help="Number of days to look back for analysis (default: 30)",
)
@click.option(
    "--start-date",
    help="Start date for analysis (YYYY-MM-DD format). Overrides --days.",
)
@click.option(
    "--end-date",
    help="End date for analysis (YYYY-MM-DD format, default: today)",
)
@click.option(
    "--output",
    type=click.Path(path_type=Path),
    help="Write report to file instead of stdout",
)
@click.pass_context
def report(
    ctx: click.Context,
    query_hashes: tuple[str, ...],
    format: str,
    price_per_tib: float,
    from_file: Path | None,
    days: int,
    start_date: str | None,
    end_date: str | None,
    output: Path | None,
) -> None:
    """Generate cost optimization report for specific query candidates.

    Provides detailed cost analysis and projected savings for materialized view
    candidates. Supports markdown, JSON, and CSV output formats.

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
    common = ctx.obj["common"]

    # Set up logging
    logger = setup_logging(common)
    exit_code = ExitCode.SUCCESS

    try:
        # Read query hashes from file if specified (T066)
        if from_file:
            file_hashes = _read_query_hashes_from_file(from_file, common.json)
            # Combine positional and file hashes
            all_hashes = list(set(list(query_hashes) + file_hashes))
        else:
            all_hashes = list(query_hashes)

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
        logger.error("Permission error", extra={"error": str(e)})
        _print_error(f"Permission denied: {e.message}", common.json, suggestion="Check IAM permissions for BigQuery")
        sys.exit(ExitCode.ERROR)

    except NotFoundError as e:
        logger.error("Not found error", extra={"error": str(e)})
        _print_error(f"Resource not found: {e.message}", common.json, suggestion="Verify the project and dataset exist")
        sys.exit(ExitCode.ERROR)

    except BigQueryError as e:
        logger.error("BigQuery error", extra={"error": str(e)})
        _print_error(f"BigQuery error: {e.message}", common.json)
        sys.exit(ExitCode.ERROR)

    except ValueError as e:
        logger.error("Validation error", extra={"error": str(e)})
        _print_error(f"Invalid input: {e}", common.json)
        sys.exit(ExitCode.ERROR)

    except Exception as e:
        logger.exception("Unexpected error")
        _print_error(f"Unexpected error: {e}", common.json)
        sys.exit(ExitCode.ERROR)

    sys.exit(exit_code)


def _read_query_hashes_from_file(file_path: Path, as_json: bool) -> list[str]:
    """Read query hashes from file (T066)."""
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
    """Calculate analysis date range."""
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


def _format_output(
    format: str,
    results: list[CostAnalysisResult],
    summary,
) -> str:
    """Format analysis results (T062)."""
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
    """Write output to file or stdout."""
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
    """Print error message to stderr."""
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
