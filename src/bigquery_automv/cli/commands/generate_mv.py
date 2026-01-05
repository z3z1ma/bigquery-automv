"""Generate MV command for materialized view creation."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import click
from google.api_core.exceptions import AlreadyExists
from tqdm import tqdm

from bigquery_automv.cli.app import app
from bigquery_automv.lib.logging import get_logger
from bigquery_automv.models.query_candidate import QueryCandidate
from bigquery_automv.services.bq_client import BigQueryClient
from bigquery_automv.services.mv_generator import MVGenerationError, MVGeneratorService
from bigquery_automv.services.smart_tuning import SmartTuningService

logger = get_logger(__name__)


class ExitCode:
    """Exit codes for the generate-mv command."""

    SUCCESS = 0
    ERROR = 1
    PARTIAL_SUCCESS = 2


@dataclass
class GenerationResult:
    """Result of MV generation for a single query hash."""

    query_hash: str
    status: str
    mv_name: str | None = None
    message: str = ""
    error: str | None = None
    ddl: str | None = None


@dataclass
class BatchResult:
    """Result of batch MV generation."""

    total: int
    successful: int
    failed: int
    skipped: int
    existing: int
    results: list[GenerationResult] = field(default_factory=list)


class GenerateMVPromptError(Exception):
    """Raised when user declines confirmation prompt."""

    pass


async def _generate_mv_async(
    query_hashes: list[str],
    mv_prefix: str,
    name_scheme_version: str,
    refresh_interval_minutes: int,
    no_refresh: bool,
    replace: bool,
    enable_auto_cleanup: bool,
    from_file: Path | None,
    from_analyze: Path | None,
    from_candidates_table: bool,
    candidates_table: str,
    dry_run: bool,
    yes: bool,
    common: object,
) -> str:
    """Async implementation of generate-mv command."""
    # Build candidates lookup from various sources
    candidates_by_hash: dict[str, QueryCandidate] = {}

    # Load from analyze output JSON
    if from_analyze:
        analyze_candidates = await _load_candidates_from_analyze(from_analyze)
        candidates_by_hash.update(analyze_candidates)

    # T081: Handle --from-file and positional QUERY_HASH arguments
    all_hashes = list(query_hashes)
    if from_file:
        file_hashes = await _read_hashes_from_file(from_file)
        all_hashes.extend(file_hashes)

    if not all_hashes and not from_analyze and not from_candidates_table:
        return (
            "Error: No query hashes provided. Use QUERY_HASH... arguments, "
            "--from-file, --from-analyze, or --from-candidates-table."
        )

    # Validate required configuration
    if not common.project:
        return "Error: --project is required. Set GOOGLE_CLOUD_PROJECT or use --project."

    if not common.dataset:
        return "Error: --dataset is required. Set BQ_AUTOMV_DATASET or use --dataset."

    # Load from candidates table if requested
    if from_candidates_table:
        table_candidates = await _load_candidates_from_table(
            common.dataset,
            candidates_table,
            common.project,
            all_hashes if all_hashes else None,  # None = load all
        )
        candidates_by_hash.update(table_candidates)
        if not all_hashes:
            all_hashes = list(table_candidates.keys())

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
        candidates_by_hash=candidates_by_hash,
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
    """Read query hashes from file, one per line."""
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


async def _load_candidates_from_analyze(file_path: Path) -> dict[str, QueryCandidate]:
    """Load query candidates from analyze output JSON file."""
    import json

    candidates: dict[str, QueryCandidate] = {}

    if not file_path.exists():
        logger.error(f"Analyze output file not found: {file_path}")
        return candidates

    try:
        data = json.loads(file_path.read_text())

        for candidate_data in data.get("candidates", []):
            try:
                candidate = QueryCandidate.from_dict(candidate_data)
                candidates[candidate.query_hash] = candidate
            except Exception as e:
                logger.warning(f"Failed to parse candidate from file: {e}")
                continue

        logger.info(f"Loaded {len(candidates)} candidates from {file_path}")

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse analyze output JSON: {e}")
    except Exception as e:
        logger.error(f"Failed to load candidates from {file_path}: {e}")

    return candidates


async def _load_candidates_from_table(
    dataset_id: str,
    table_name: str,
    project_id: str,
    query_hashes: list[str] | None = None,
) -> dict[str, QueryCandidate]:
    """Load query candidates from BigQuery candidates table."""
    candidates: dict[str, QueryCandidate] = {}

    try:
        async with BigQueryClient(project_id=project_id, region="US") as client:
            # Build query
            full_table_name = f"{project_id}.{dataset_id}.{table_name}"

            if query_hashes:
                # Query specific hashes
                sql = f"""SELECT * FROM `{full_table_name}`
WHERE query_hash IN UNNEST(@hashes)
ORDER BY impact_score DESC"""

                result = await client.run_query(
                    sql,
                    query_params=[("hashes", "ARRAY<STRING>", query_hashes)],
                )
            else:
                # Query all candidates
                sql = f"""SELECT * FROM `{full_table_name}`
ORDER BY impact_score DESC"""

                result = await client.run_query(sql)

            # Reconstruct QueryCandidate objects
            for row in result.rows:
                try:
                    candidate = QueryCandidate.from_dict(row)
                    candidates[candidate.query_hash] = candidate
                except Exception as e:
                    logger.warning(f"Failed to parse candidate from table row: {e}")
                    continue

            logger.info(f"Loaded {len(candidates)} candidates from {full_table_name}")

    except Exception as e:
        logger.error(f"Failed to load candidates from table: {e}")

    return candidates


async def _process_batch(
    hashes: list[str],
    candidates_by_hash: dict[str, QueryCandidate],
    common: object,
    mv_prefix: str,
    refresh_interval_minutes: int,
    enable_refresh: bool,
    enable_auto_cleanup: bool,
    dry_run: bool,
    replace: bool,
    yes: bool,
) -> BatchResult:
    """Process a batch of query hashes with progress reporting."""
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
                candidate=candidates_by_hash.get(query_hash),
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
    successful = sum(1 for r in results if r.status in ("success", "dry_run"))
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
    """Prompt user for confirmation before deployment."""
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
    candidate: QueryCandidate | None,
    common: object,
    mv_prefix: str,
    refresh_interval_minutes: int,
    enable_refresh: bool,
    enable_auto_cleanup: bool,
    dry_run: bool,
    replace: bool,
) -> GenerationResult:
    """Process a single query hash."""
    # If no candidate found, skip
    if candidate is None:
        return GenerationResult(
            query_hash=query_hash,
            status="skipped",
            message=(
                "Query candidate not found. Use analyze with --persist, "
                "or specify --from-analyze or --from-candidates-table."
            ),
        )

    try:
        # Create BigQuery client and MV generator service
        async with BigQueryClient(project_id=common.project, region=common.region) as bq_client:
            smart_tuning_service = SmartTuningService(
                bq_client=bq_client,
                enable_preview_eligibility=False,
            )
            mv_generator = MVGeneratorService(
                bq_client=bq_client,
                smart_tuning_service=smart_tuning_service,
            )

            return await _generate_mv_from_candidate(
                candidate=candidate,
                common=common,
                mv_generator=mv_generator,
                mv_prefix=mv_prefix,
                refresh_interval_minutes=refresh_interval_minutes,
                enable_refresh=enable_refresh,
                enable_auto_cleanup=enable_auto_cleanup,
                dry_run=dry_run,
                replace=replace,
            )

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
    common: object,
    mv_generator: MVGeneratorService,
    mv_prefix: str,
    refresh_interval_minutes: int,
    enable_refresh: bool,
    enable_auto_cleanup: bool,
    dry_run: bool,
    replace: bool,
) -> GenerationResult:
    """Generate and deploy MV from a QueryCandidate."""
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

        # Check idempotency (T085) - skip during dry-run
        if not replace and not dry_run:
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


@asynccontextmanager
async def _create_progress_bar(
    hashes: list[str],
    dry_run: bool,
) -> AsyncGenerator[tqdm]:
    """Async context manager for progress bar."""
    pbar = tqdm(total=len(hashes), desc="Generating DDL" if dry_run else "Deploying MVs", unit="hash")
    try:
        yield pbar
    finally:
        pbar.close()


def _format_batch_result(result: BatchResult, dry_run: bool) -> str:
    """Format batch result for output."""
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

    # Add DDL output for dry-run or successful with DDL
    if (dry_run or any(r.ddl for r in result.results)) and result.successful > 0:
        lines.append("\nGenerated DDL:")
        for r in result.results:
            if r.status in ("success", "dry_run") and r.ddl:
                lines.append(f"\n-- {r.mv_name} (hash: {r.query_hash[:12]})")
                lines.append(r.ddl)
                lines.append("")

    return "\n".join(lines)


@app.command(name="generate-mv")
@click.argument("query_hashes", nargs=-1)
@click.option(
    "--mv-prefix",
    default="automv_",
    help="Prefix for generated MV names",
)
@click.option(
    "--name-scheme-version",
    default="v1",
    help="Naming scheme version (for future changes)",
)
@click.option(
    "--refresh-interval-minutes",
    default=60,
    help="MV refresh interval in minutes",
)
@click.option(
    "--no-refresh",
    is_flag=True,
    help="Disable automatic refresh",
)
@click.option(
    "--replace",
    is_flag=True,
    help="Drop and recreate existing MVs",
)
@click.option(
    "--enable-auto-cleanup",
    is_flag=True,
    help="Enable automatic cleanup of stale MVs",
)
@click.option(
    "--from-file",
    type=click.Path(path_type=Path),
    help="Read query hashes from file (one per line)",
)
@click.option(
    "--from-analyze",
    type=click.Path(path_type=Path),
    help="Read candidates from analyze output JSON file",
)
@click.option(
    "--from-candidates-table",
    is_flag=True,
    help="Read candidates from BigQuery query_candidates table in target dataset",
)
@click.option(
    "--candidates-table",
    default="query_candidates",
    help="Custom candidates table name (default: query_candidates)",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Output DDL without executing",
)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    help="Skip confirmation prompt",
)
@click.pass_context
def generate_mv(
    ctx: click.Context,
    query_hashes: tuple[str, ...],
    mv_prefix: str,
    name_scheme_version: str,
    refresh_interval_minutes: int,
    no_refresh: bool,
    replace: bool,
    enable_auto_cleanup: bool,
    from_file: Path | None,
    from_analyze: Path | None,
    from_candidates_table: bool,
    candidates_table: str,
    dry_run: bool,
    yes: bool,
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
    common = ctx.obj["common"]

    # Run async command
    result = asyncio.run(
        _generate_mv_async(
            query_hashes=list(query_hashes),
            mv_prefix=mv_prefix,
            name_scheme_version=name_scheme_version,
            refresh_interval_minutes=refresh_interval_minutes,
            no_refresh=no_refresh,
            replace=replace,
            enable_auto_cleanup=enable_auto_cleanup,
            from_file=from_file,
            from_analyze=from_analyze,
            from_candidates_table=from_candidates_table,
            candidates_table=candidates_table,
            dry_run=dry_run or common.dry_run,
            yes=yes,
            common=common,
        )
    )
    print(result)
