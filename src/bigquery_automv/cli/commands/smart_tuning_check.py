"""Smart tuning check command for eligibility verification."""

import json
import sys
from pathlib import Path
from typing import Annotated

from cyclopts import Parameter

from bigquery_automv.cli.app import CommonConfig, app
from bigquery_automv.lib.logging import setup_logging
from bigquery_automv.services.bq_client import BigQueryClient
from bigquery_automv.services.smart_tuning import SmartTuningCheckResult, SmartTuningService


class ExitCode:
    """Exit codes for the smart-tuning-check command."""

    SUCCESS = 0
    ERROR = 1
    NOT_ELIGIBLE = 2


@app.command
def smart_tuning_check(
    query_hash: Annotated[
        str | None,
        Parameter(
            name="query_hash",
            help="Query hash to check (use --sql or --from-file instead)",
            show_default=False,
        ),
    ] = None,
    sql: Annotated[
        str | None,
        Parameter(
            name="--sql",
            help="Direct SQL string to analyze",
        ),
    ] = None,
    from_file: Annotated[
        Path | None,
        Parameter(
            name="--from-file",
            help="Read SQL from file",
            parse=lambda p: Path(p) if p else None,
        ),
    ] = None,
    rulebook_version: Annotated[
        str | None,
        Parameter(
            name="--rulebook-version",
            help="Rulebook version for eligibility checks (default: v1.0)",
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
    """Check if queries are eligible for BigQuery Smart Tuning.

    Analyzes query patterns to determine if they can benefit from automatic
    materialized view rerouting. Provides detailed eligibility reasoning and
    recommended MV structure when eligible.

    \b
    Input Methods (one required):
      1. query_hash: Hash from analysis results (requires BigQuery access)
      2. --sql: Direct SQL string
      3. --from-file: Path to file containing SQL

    \b
    Exit Codes:
      0: Eligible for Smart Tuning
      1: Fatal error (parse failure, invalid input)
      2: Not eligible for Smart Tuning

    \b
    Examples:
      # Check SQL from string
      bq-automv smart-tuning-check --sql "SELECT count(*) FROM table"

      # Check SQL from file
      bq-automv smart-tuning-check --from-file query.sql

      # Verbose output with detailed analysis
      bq-automv smart-tuning-check --sql "SELECT..." --verbose

      # JSON output
      bq-automv smart-tuning-check --sql "SELECT..." --json
    """
    if common is None:
        common = CommonConfig()

    # Setup logging
    logger = setup_logging(common)

    # Validate input (exactly one input method required)
    inputs_provided = sum(
        [
            query_hash is not None,
            sql is not None,
            from_file is not None,
        ]
    )

    if inputs_provided == 0:
        logger.error("No input provided")
        _print_error("One of query_hash, --sql, or --from-file is required", common.json)
        sys.exit(ExitCode.ERROR)

    if inputs_provided > 1:
        logger.error("Multiple inputs provided")
        _print_error("Only one of query_hash, --sql, or --from-file can be specified", common.json)
        sys.exit(ExitCode.ERROR)

    # Get SQL string
    sql_to_check: str | None = None

    if sql:
        sql_to_check = sql
    elif from_file:
        try:
            sql_to_check = from_file.read_text()
        except Exception as e:
            logger.error(f"Error reading file {from_file}: {e}")
            _print_error(f"Error reading file {from_file}: {e}", common.json)
            sys.exit(ExitCode.ERROR)
    elif query_hash:
        # Query hash requires fetching from BigQuery (T049)
        # For now, return error as this requires integration with metadata table
        logger.error("query_hash lookup not yet implemented")
        _print_error("query_hash lookup is not yet implemented. Use --sql or --from-file.", common.json)
        sys.exit(ExitCode.ERROR)

    if not sql_to_check:
        logger.error("No SQL to analyze")
        _print_error("No SQL to analyze", common.json)
        sys.exit(ExitCode.ERROR)

    # Initialize Smart Tuning service
    service = SmartTuningService(
        enable_preview_eligibility=common.enable_preview_eligibility,
    )

    # Initialize BigQuery client if target dataset/project specified
    bq_client: BigQueryClient | None = None
    if common.project and common.dataset:
        try:
            import asyncio

            async def get_client():
                return BigQueryClient(
                    project_id=common.project,
                    region=common.region,
                )

            bq_client = asyncio.run(get_client())
            service.bq_client = bq_client
        except Exception as e:
            if common.verbose:
                logger.warning(f"Failed to initialize BigQuery client: {e}")
                _print_error(f"Warning: Failed to initialize BigQuery client: {e}", common.json)
                # Continue without metadata validation

    # Perform eligibility check
    import asyncio

    async def check():
        return await service.check_elibility(
            sql=sql_to_check,
            query_hash=query_hash or "direct",
            target_dataset=common.dataset if common.dataset else None,
            target_project=common.project if common.project else None,
        )

    try:
        result = asyncio.run(check())
    except Exception as e:
        logger.error(f"Error during eligibility check: {e}")
        _print_error(f"Failed to analyze query: {e}" if common.verbose else "Failed to analyze query", common.json)
        sys.exit(ExitCode.ERROR)
    finally:
        # Close client if it was created
        if bq_client:
            bq_client.close()

    # Generate output
    if common.json:
        print(format_result_json(result, common.verbose))
    else:
        print(format_result_text(result, common.verbose))

    # Exit with appropriate code
    if result.eligible:
        sys.exit(ExitCode.SUCCESS)
    else:
        sys.exit(ExitCode.NOT_ELIGIBLE)


def format_result_json(result: SmartTuningCheckResult, verbose: bool) -> str:
    """Format result as JSON.

    Args:
        result: Smart tuning check result
        verbose: Include verbose details

    Returns:
        JSON string
    """
    output = {
        "query_hash": result.query_hash,
        "eligible": result.eligible,
        "eligibility_basis": result.eligibility_basis,
        "rulebook_version": result.rulebook_version,
    }

    if result.eligible:
        # Eligible - show recommendations
        output["recommendations"] = {
            "mv_select": result.recommended_mv_select,
            "mv_group_by": result.recommended_mv_group_by,
            "mv_filters": result.recommended_mv_filters,
        }
        output["analysis"] = {
            "liftable_columns": result.liftable_columns,
            "non_liftable_filters": result.non_liftable_filters,
        }
    else:
        # Not eligible - show reasons
        output["disqualification_reasons"] = result.disqualification_reasons
        output["unsupported_features"] = result.unsupported_features

    if verbose:
        output["detailed_analysis"] = {
            "aggregation_functions": result.aggregation_functions,
            "join_types": result.join_types,
            "has_ctes": result.has_ctes,
            "subquery_types": result.subquery_types,
            "cross_project_references": result.cross_project_references,
            "region_mismatch": result.region_mismatch,
        }

    return json.dumps(output, indent=2)


def format_result_text(result: SmartTuningCheckResult, verbose: bool) -> str:
    """Format result as human-readable text.

    Args:
        result: Smart tuning check result
        verbose: Include verbose details

    Returns:
        Formatted text string
    """
    lines: list[str] = []

    # Header
    lines.append("BigQuery Smart Tuning Eligibility Check")
    lines.append("=" * 50)
    lines.append("")

    # Eligibility status
    if result.eligible:
        basis_label = f" ({result.eligibility_basis})" if result.eligibility_basis == "preview" else ""
        lines.append(f"✓ ELIGIBLE for Smart Tuning{basis_label}")
        lines.append("")
    else:
        lines.append("✗ NOT ELIGIBLE for Smart Tuning")
        lines.append("")

    # Disqualification reasons
    if not result.eligible:
        lines.append("Disqualification Reasons:")
        for reason in result.disqualification_reasons:
            lines.append(f"  • {reason}")
        lines.append("")

    # Unsupported features
    if result.unsupported_features:
        lines.append("Unsupported Features:")
        for feature in result.unsupported_features:
            lines.append(f"  • {feature}")
        lines.append("")

    # Recommendations (if eligible)
    if result.eligible:
        lines.append("Recommended Materialized View Structure:")
        lines.append("")

        if result.recommended_mv_select:
            lines.append("  SELECT:")
            for col in result.recommended_mv_select:
                lines.append(f"    {col}")
            lines.append("")

        if result.recommended_mv_group_by:
            lines.append("  GROUP BY:")
            for col in result.recommended_mv_group_by:
                lines.append(f"    {col}")
            lines.append("")

        if result.recommended_mv_filters:
            lines.append("  WHERE (shared predicates):")
            for filt in result.recommended_mv_filters:
                lines.append(f"    {filt}")
            lines.append("")

        # Predicate lifting analysis
        if result.liftable_columns:
            lines.append("  Liftable Columns (can be filtered at query time):")
            for col in result.liftable_columns:
                lines.append(f"    {col}")
            lines.append("")

        if result.non_liftable_filters:
            lines.append("  Non-Liftable Filters (must match exactly in queries):")
            for filt in result.non_liftable_filters:
                lines.append(f"    {filt}")
            lines.append("")

    # Verbose details
    if verbose:
        lines.append("Detailed Analysis:")
        lines.append("")

        lines.append(f"  Query Hash: {result.query_hash}")
        lines.append(f"  Rulebook Version: {result.rulebook_version}")
        lines.append(f"  Eligibility Basis: {result.eligibility_basis}")
        lines.append("")

        if result.aggregation_functions:
            lines.append(f"  Aggregation Functions: {', '.join(result.aggregation_functions)}")
        else:
            lines.append("  Aggregation Functions: None")

        if result.join_types:
            lines.append(f"  Join Types: {', '.join(result.join_types)}")
        else:
            lines.append("  Join Types: None")

        lines.append(f"  Has CTEs: {result.has_ctes}")

        if result.subquery_types:
            lines.append(f"  Subquery Types: {', '.join(result.subquery_types)}")
        else:
            lines.append("  Subquery Types: None")

        lines.append(f"  Cross-Project References: {result.cross_project_references}")
        lines.append(f"  Region Mismatch: {result.region_mismatch}")
        lines.append("")

    return "\n".join(lines)


def _print_error(message: str, as_json: bool, suggestion: str | None = None) -> None:
    """Print error message to stderr.

    Args:
        message: Error message
        as_json: Whether to format as JSON
        suggestion: Optional suggestion for fixing the error
    """
    if as_json:
        error_data = {
            "error": "SmartTuningCheckError",
            "message": message,
        }
        if suggestion:
            error_data["suggestion"] = suggestion
        print(json.dumps(error_data), file=sys.stderr)
    else:
        print(f"Error: {message}", file=sys.stderr)
        if suggestion:
            print(f"Suggestion: {suggestion}", file=sys.stderr)
