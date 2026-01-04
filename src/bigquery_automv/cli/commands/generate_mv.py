"""Generate MV command for materialized view creation."""

import asyncio
import sys
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

from cyclopts import Parameter
from google.api_core.exceptions import AlreadyExists
from tqdm import tqdm

from bigquery_automv.cli.app import CommonConfig, app
from bigquery_automv.lib.logging import get_logger
from bigquery_automv.models.query_candidate import QueryCandidate
from bigquery_automv.services.bq_client import BigQueryClient
from bigquery_automv.services.mv_generator import MVGenerationError, MVGeneratorService

logger = get_logger(__name__)


class ExitCode:
    """Exit codes for the generate-mv command."""

    SUCCESS = 0
    ERROR = 1
    PARTIAL_SUCCESS = 2


@dataclass
class GenerationResult:
    """Result of MV generation for a single query hash.

    Attributes:
        query_hash: Query family hash
        status: Generation status (success, failed, skipped, exists)
        mv_name: Generated MV name (if applicable)
        message: Status message
        error: Error message (if failed)
        ddl: DDL statement (if dry_run)
    """

    query_hash: str
    status: str
    mv_name: str | None = None
    message: str = ""
    error: str | None = None
    ddl: str | None = None


@dataclass
class BatchResult:
    """Result of batch MV generation.

    Attributes:
        total: Total number of query hashes processed
        successful: Number of successful generations
        failed: Number of failed generations
        skipped: Number of skipped generations
        existing: Number of already-existing MVs (no-op)
        results: Individual results per query hash
    """

    total: int
    successful: int
    failed: int
    skipped: int
    existing: int
    results: list[GenerationResult] = field(default_factory=list)


class GenerateMVPromptError(Exception):
    """Raised when user declines confirmation prompt."""

    pass


@app.command
def generate_mv(
    query_hashes: Annotated[
        list[str],
        Parameter(
            name="QUERY_HASH",
            help="Query family hashes to generate MVs for",
        ),
    ],
    mv_prefix: Annotated[
        str,
        Parameter(
            name="--mv-prefix",
            help="Prefix for generated MV names",
        ),
    ] = "automv_",
    name_scheme_version: Annotated[
        str,
        Parameter(
            name="--name-scheme-version",
            help="Naming scheme version (for future changes)",
        ),
    ] = "v1",
    refresh_interval_minutes: Annotated[
        int,
        Parameter(
            name="--refresh-interval-minutes",
            help="MV refresh interval in minutes",
        ),
    ] = 60,
    no_refresh: Annotated[
        bool,
        Parameter(
            name="--no-refresh",
            help="Disable automatic refresh",
            negative="",
        ),
    ] = False,
    replace: Annotated[
        bool,
        Parameter(
            name="--replace",
            help="Drop and recreate existing MVs",
            negative="",
        ),
    ] = False,
    enable_auto_cleanup: Annotated[
        bool,
        Parameter(
            name="--enable-auto-cleanup",
            help="Enable automatic cleanup of stale MVs",
            negative="",
        ),
    ] = False,
    from_file: Annotated[
        Path | None,
        Parameter(
            name="--from-file",
            help="Read query hashes from file (one per line)",
            parse=lambda p: Path(p) if p else None,
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        Parameter(
            name="--dry-run",
            help="Output DDL without executing",
            negative="",
        ),
    ] = False,
    yes: Annotated[
        bool,
        Parameter(
            name=["--yes", "-y"],
            help="Skip confirmation prompt",
            negative="",
        ),
    ] = False,
    *,
    common: Annotated[
        CommonConfig | None,
        Parameter(
            name="*",
            help="Common configuration options",
        ),
    ] = None,
) -> None:
    """Generate and deploy materialized views for eligible queries.

    Creates optimized materialized views based on query analysis. Supports
    dry-run mode for previewing DDL without execution.

    Examples:
        # Generate MV for a single query hash (dry run)
        bq-automv generate-mv abc123 --dry-run

        # Generate and deploy MV for multiple hashes
        bq-automv generate-mv abc123 def456 --yes

        # Read hashes from file
        bq-automv generate-mv --from-file hashes.txt --yes

        # Replace existing MV with new definition
        bq-automv generate-mv abc123 --replace --yes
    """
    if common is None:
        common = CommonConfig()

    # Run async command
    return asyncio.run(
        _generate_mv_async(
            query_hashes=query_hashes,
            mv_prefix=mv_prefix,
            name_scheme_version=name_scheme_version,
            refresh_interval_minutes=refresh_interval_minutes,
            no_refresh=no_refresh,
            replace=replace,
            enable_auto_cleanup=enable_auto_cleanup,
            from_file=from_file,
            dry_run=dry_run,
            yes=yes,
            common=common,
        )
    )


async def _generate_mv_async(
    query_hashes: list[str],
    mv_prefix: str,
    name_scheme_version: str,
    refresh_interval_minutes: int,
    no_refresh: bool,
    replace: bool,
    enable_auto_cleanup: bool,
    from_file: Path | None,
    dry_run: bool,
    yes: bool,
    common: CommonConfig,
) -> str:
    """Async implementation of generate-mv command."""
    # T081: Handle --from-file and positional QUERY_HASH arguments
    all_hashes = list(query_hashes)
    if from_file:
        file_hashes = await _read_hashes_from_file(from_file)
        all_hashes.extend(file_hashes)

    if not all_hashes:
        return "Error: No query hashes provided. Use QUERY_HASH... arguments or --from-file."

    # Validate required configuration
    if not common.project:
        return "Error: --project is required. Set GOOGLE_CLOUD_PROJECT or use --project."

    if not common.dataset:
        return "Error: --dataset is required. Set BQ_AUTOMV_DATASET or use --dataset."

    # Validate target dataset exists (T095)
    logger.info(f"Validating target dataset: {common.dataset}")
    try:
        async with BigQueryClient(project_id=common.project, region=common.region) as bq_client:
            dataset_exists = await bq_client.dataset_exists(common.dataset)
            if not dataset_exists:
                return f"Error: Target dataset '{common.dataset}' does not exist in project '{common.project}'."

            # Initialize metadata table if needed (T096)
            try:
                await bq_client.initialize_metadata_table(
                    dataset_id=common.dataset,
                    table_name="automv_metadata",
                )
                logger.info(f"Initialized metadata table in {common.dataset}")
            except AlreadyExists:
                # Metadata table already exists, that's fine
                pass

    except Exception as e:
        logger.error(f"Failed to validate target dataset: {e}")
        return f"Error: Failed to validate target dataset: {e}"

    # T090: Process all hashes with continue-on-error
    batch_result = await _process_batch(
        hashes=all_hashes,
        common=common,
        mv_prefix=mv_prefix,
        refresh_interval_minutes=refresh_interval_minutes,
        enable_refresh=not no_refresh,
        enable_auto_cleanup=enable_auto_cleanup,
        dry_run=dry_run,
        replace=replace,
        yes=yes,
    )

    # Return summary
    return _format_batch_result(batch_result, dry_run=dry_run)


async def _read_hashes_from_file(file_path: Path) -> list[str]:
    """Read query hashes from file, one per line.

    Args:
        file_path: Path to file containing hashes

    Returns:
        List of query hashes (non-empty lines, stripped)
    """
    if not file_path.exists():
        logger.error(f"Hash file not found: {file_path}")
        return []

    hashes: list[str] = []
    try:
        with file_path.open("r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):  # Skip empty lines and comments
                    hashes.append(line)

        logger.info(f"Read {len(hashes)} hashes from {file_path}")
    except Exception as e:
        logger.error(f"Failed to read hash file {file_path}: {e}")

    return hashes


async def _process_batch(
    hashes: list[str],
    common: CommonConfig,
    mv_prefix: str,
    refresh_interval_minutes: int,
    enable_refresh: bool,
    enable_auto_cleanup: bool,
    dry_run: bool,
    replace: bool,
    yes: bool,
) -> BatchResult:
    """Process a batch of query hashes with progress reporting.

    Args:
        hashes: List of query hashes to process
        common: Common configuration
        mv_prefix: MV name prefix
        refresh_interval_minutes: MV refresh interval
        enable_refresh: Enable automatic refresh
        enable_auto_cleanup: Enable automatic cleanup
        dry_run: Dry run mode
        replace: Replace existing MVs
        yes: Skip confirmation

    Returns:
        BatchResult with summary and individual results
    """
    results: list[GenerationResult] = []

    # T091: Confirmation prompt (unless --yes)
    if not dry_run and not yes:
        if not await _confirm_deployment(hashes, replace, common.dataset):
            return BatchResult(
                total=len(hashes),
                successful=0,
                failed=0,
                skipped=len(hashes),
                existing=0,
                results=[],
            )

    # T093: Batch operations with progress reporting
    async with _create_progress_bar(hashes, dry_run=dry_run) as pbar:
        for query_hash in hashes:
            result = await _process_single_hash(
                query_hash=query_hash,
                common=common,
                mv_prefix=mv_prefix,
                refresh_interval_minutes=refresh_interval_minutes,
                enable_refresh=enable_refresh,
                enable_auto_cleanup=enable_auto_cleanup,
                dry_run=dry_run,
                replace=replace,
            )
            results.append(result)
            pbar.update(1)
            pbar.set_postfix_str(f"Status: {result.status}")

    # Aggregate results
    successful = sum(1 for r in results if r.status == "success")
    failed = sum(1 for r in results if r.status == "failed")
    skipped = sum(1 for r in results if r.status == "skipped")
    existing = sum(1 for r in results if r.status == "exists")

    return BatchResult(
        total=len(hashes),
        successful=successful,
        failed=failed,
        skipped=skipped,
        existing=existing,
        results=results,
    )


async def _confirm_deployment(hashes: list[str], replace: bool, dataset: str) -> bool:
    """Prompt user for confirmation before deployment.

    Args:
        hashes: List of query hashes to process
        replace: Whether replacing existing MVs
        dataset: Target dataset

    Returns:
        True if user confirms, False otherwise
    """
    print(f"\nAbout to generate {len(hashes)} materialized view(s) in dataset '{dataset}'")

    if replace:
        print("WARNING: --replace flag is set. Existing MVs will be dropped and recreated.")

    print("\nQuery hashes to process:")
    for h in hashes[:10]:  # Show first 10
        print(f"  - {h}")
    if len(hashes) > 10:
        print(f"  ... and {len(hashes) - 10} more")

    response = input("\nProceed? [y/N] ").strip().lower()
    return response in ("y", "yes")


async def _process_single_hash(
    query_hash: str,
    common: CommonConfig,
    mv_prefix: str,
    refresh_interval_minutes: int,
    enable_refresh: bool,
    enable_auto_cleanup: bool,
    dry_run: bool,
    replace: bool,
) -> GenerationResult:
    """Process a single query hash.

    Args:
        query_hash: Query family hash
        common: Common configuration
        mv_prefix: MV name prefix
        refresh_interval_minutes: MV refresh interval
        enable_refresh: Enable automatic refresh
        enable_auto_cleanup: Enable automatic cleanup
        dry_run: Dry run mode
        replace: Replace existing MVs

    Returns:
        GenerationResult for this hash
    """
    try:
        # For MVP, we need to construct a QueryCandidate from just the hash
        # In production, this would query a metadata table or query history
        # For now, we'll create a minimal candidate

        # TODO: Fetch actual query details from metadata table
        # For now, we'll fail gracefully
        return GenerationResult(
            query_hash=query_hash,
            status="skipped",
            message="Query candidate lookup not yet implemented. Use analyze command to populate candidates.",
        )

        # When metadata table is implemented:
        # 1. Query metadata table for query_hash
        # 2. Reconstruct QueryCandidate
        # 3. Use MVGeneratorService to generate artifact
        # 4. Deploy artifact

    except MVGenerationError as e:
        logger.error(f"MV generation failed for {query_hash}: {e}")
        return GenerationResult(
            query_hash=query_hash,
            status="failed",
            error=str(e),
        )
    except Exception as e:
        logger.error(f"Unexpected error processing {query_hash}: {e}")
        return GenerationResult(
            query_hash=query_hash,
            status="failed",
            error=f"Unexpected error: {e}",
        )


async def _generate_mv_from_candidate(
    candidate: QueryCandidate,
    common: CommonConfig,
    mv_generator: MVGeneratorService,
    mv_prefix: str,
    refresh_interval_minutes: int,
    enable_refresh: bool,
    enable_auto_cleanup: bool,
    dry_run: bool,
    replace: bool,
) -> GenerationResult:
    """Generate and deploy MV from a QueryCandidate.

    Args:
        candidate: Query candidate with representative SQL
        common: Common configuration
        mv_generator: MV generator service
        mv_prefix: MV name prefix
        refresh_interval_minutes: MV refresh interval
        enable_refresh: Enable automatic refresh
        enable_auto_cleanup: Enable automatic cleanup
        dry_run: Dry run mode
        replace: Replace existing MVs

    Returns:
        GenerationResult
    """
    query_hash = candidate.query_hash

    try:
        # Generate MV artifact (includes eligibility check - T094)
        artifact = await mv_generator.generate_mv_artifact(
            candidate=candidate,
            target_dataset=common.dataset,
            target_project=common.project,
            mv_prefix=mv_prefix,
            refresh_interval_minutes=refresh_interval_minutes,
            enable_refresh=enable_refresh,
            enable_auto_cleanup=enable_auto_cleanup,
        )

        # Check idempotency (T085)
        if not replace:
            exists = await mv_generator.check_mv_exists(
                artifact.mv_name,
                artifact.dataset_id,
                project_id=artifact.project_id,
            )
            if exists:
                # Check signature hash
                existing = await mv_generator.get_existing_mv_signature(
                    artifact.mv_name,
                    artifact.dataset_id,
                    project_id=artifact.project_id,
                )
                if existing:
                    # In production, compare signature hashes
                    # For now, treat as exists
                    return GenerationResult(
                        query_hash=query_hash,
                        status="exists",
                        mv_name=artifact.mv_name,
                        message="MV already exists with matching signature (no-op)",
                    )

        # Deploy MV (T083, T082 for dry-run)
        deployment_result = await mv_generator.deploy_mv(
            artifact=artifact,
            dry_run=dry_run,
            replace=replace,
        )

        return GenerationResult(
            query_hash=query_hash,
            status=deployment_result["status"],
            mv_name=artifact.mv_name,
            message=deployment_result.get("message", ""),
            ddl=artifact.ddl_definition if dry_run else None,
        )

    except MVGenerationError as e:
        return GenerationResult(
            query_hash=query_hash,
            status="failed",
            error=str(e),
        )
    except Exception as e:
        return GenerationResult(
            query_hash=query_hash,
            status="failed",
            error=f"Unexpected error: {e}",
        )


async def _create_progress_bar(
    hashes: list[str],
    dry_run: bool,
) -> AsyncGenerator[tqdm]:
    """Async context manager for progress bar.

    Args:
        hashes: List of hashes being processed
        dry_run: Whether in dry-run mode

    Yields:
        tqdm progress bar
    """
    pbar = tqdm(total=len(hashes), desc="Generating DDL" if dry_run else "Deploying MVs", unit="hash")
    try:
        yield pbar
    finally:
        pbar.close()


def _format_batch_result(result: BatchResult, dry_run: bool) -> str:
    """Format batch result for output.

    Args:
        result: Batch processing result
        dry_run: Whether in dry-run mode

    Returns:
        Formatted output string
    """
    lines = [
        f"\n{'=' * 60}",
        f"MV Generation Summary ({'DRY RUN' if dry_run else 'LIVE'})",
        f"{'=' * 60}",
        f"Total hashes processed: {result.total}",
        f"  Successful: {result.successful}",
        f"  Failed: {result.failed}",
        f"  Skipped: {result.skipped}",
        f"  Already exists: {result.existing}",
        f"{'=' * 60}",
    ]

    # Add details for failed/skipped hashes
    if result.failed > 0 or result.skipped > 0:
        lines.append("\nDetails:")
        for r in result.results:
            if r.status in ("failed", "skipped"):
                lines.append(f"  [{r.status.upper()}] {r.query_hash}")
                if r.error:
                    lines.append(f"    Error: {r.error}")
                if r.message:
                    lines.append(f"    Message: {r.message}")

    # Add DDL output for dry-run
    if dry_run and result.successful > 0:
        lines.append("\nGenerated DDL:")
        for r in result.results:
            if r.status == "success" and r.ddl:
                lines.append(f"\n-- {r.mv_name} (hash: {r.query_hash})")
                lines.append(r.ddl)
                lines.append("")

    return "\n".join(lines)


# Example query candidate (for testing)
# In production, this would be fetched from a metadata table
async def _example_query_candidate(query_hash: str) -> QueryCandidate | None:
    """Create an example query candidate for testing.

    This is a placeholder. In production, query candidates would be
    fetched from a metadata table populated by the analyze command.
    """
    # Return None to indicate not implemented
    return None


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
            "error": "GenerateMVError",
            "message": message,
        }
        if suggestion:
            error_data["suggestion"] = suggestion
        print(json.dumps(error_data), file=sys.stderr)
    else:
        print(f"Error: {message}", file=sys.stderr)
        if suggestion:
            print(f"Suggestion: {suggestion}", file=sys.stderr)
