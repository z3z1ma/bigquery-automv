"""Run command for interactive analysis and MV generation."""

import asyncio
import sys
from datetime import date, datetime
from typing import Annotated

from cyclopts import Parameter

from bigquery_automv.cli.app import CommonConfig, app

# Import format_bytes from analyze command to reuse
from bigquery_automv.cli.commands.analyze import format_bytes
from bigquery_automv.lib.config import AnalysisConfig, ImpactScoringConfig
from bigquery_automv.lib.logging import setup_logging
from bigquery_automv.models.query_candidate import QueryCandidate
from bigquery_automv.services.analyzer import AnalyzerService
from bigquery_automv.services.bq_client import BigQueryClient
from bigquery_automv.services.mv_generator import MVGeneratorService
from bigquery_automv.services.smart_tuning import SmartTuningService


@app.command
def run(
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
    max_families: Annotated[
        int,
        Parameter(
            name="--max-families",
            help="Maximum number of query families to return",
        ),
    ] = 20,
    mv_prefix: Annotated[
        str,
        Parameter(
            name="--mv-prefix",
            help="Prefix for generated MV names",
        ),
    ] = "automv_",
    refresh_interval_minutes: Annotated[
        int,
        Parameter(
            name="--refresh-interval-minutes",
            help="MV refresh interval in minutes",
        ),
    ] = 60,
    enable_refresh: Annotated[
        bool,
        Parameter(
            name="--enable-refresh",
            help="Enable automatic refresh for MVs",
            negative="--no-refresh",
        ),
    ] = True,
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
    """Interactive workflow: Analyze queries and generate MVs.

    This command combines analysis and generation into a single interactive flow:
    1. Analyzes query history to find candidates.
    2. Presents a list of top candidates.
    3. Prompts for selection.
    4. Generates and deploys MVs for selected candidates.

    Example:
        ```bash
        bq-automv run --start-date 2024-01-01 --min-executions 50
        ```
    """
    if common is None:
        common = CommonConfig()

    logger = setup_logging(common)

    try:
        asyncio.run(
            _run_interactive(
                start_date=start_date,
                end_date=end_date,
                common=common,
                min_executions=min_executions,
                min_bytes=min_bytes,
                max_families=max_families,
                mv_prefix=mv_prefix,
                refresh_interval_minutes=refresh_interval_minutes,
                enable_refresh=enable_refresh,
                yes=yes,
            )
        )
    except Exception as e:
        logger.exception("Unexpected error during run")
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


async def _run_interactive(
    start_date: date,
    end_date: date,
    common: CommonConfig,
    min_executions: int,
    min_bytes: int,
    max_families: int,
    mv_prefix: str,
    refresh_interval_minutes: int,
    enable_refresh: bool,
    yes: bool,
) -> None:
    """Run the interactive workflow."""
    if not common.project:
        print("Error: Project ID is required. Set --project or GOOGLE_CLOUD_PROJECT.")
        return

    # Step 1: Analyze
    print(f"\nAnalyzing query history from {start_date} to {end_date}...")
    print("This may take a minute depending on job history size.\n")

    analysis_config = AnalysisConfig(
        min_executions=min_executions,
        min_bytes=min_bytes,
        max_families=max_families,
    )
    impact_config = ImpactScoringConfig()  # Use defaults

    async with BigQueryClient(project_id=common.project, region=common.region) as client:
        smart_tuning_service = SmartTuningService(bq_client=client)
        analyzer = AnalyzerService(
            client=client,
            smart_tuning_service=smart_tuning_service,
            analysis_config=analysis_config,
            impact_config=impact_config,
        )

        start_datetime = datetime.combine(start_date, datetime.min.time())
        end_datetime = datetime.combine(end_date, datetime.max.time())

        result = await analyzer.analyze(
            start_date=start_datetime,
            end_date=end_datetime,
            project_id=common.project,
        )

        # Filter eligible candidates
        eligible_candidates = [c for c in result.candidates if c.smart_tuning_eligible]

        if not eligible_candidates:
            print("No eligible candidates found matching criteria.")
            return

        # Step 2: Present Candidates
        _print_candidates_table(eligible_candidates)

        # Step 3: Prompt Selection
        selected_candidates = _prompt_selection(eligible_candidates, yes)
        if not selected_candidates:
            print("No candidates selected. Exiting.")
            return

        # Step 4: Generate MVs
        print(f"\nGenerating {len(selected_candidates)} MVs...")

        mv_generator = MVGeneratorService(
            bq_client=client,
            smart_tuning_service=smart_tuning_service,
        )

        for candidate in selected_candidates:
            await _generate_single_mv(
                candidate=candidate,
                mv_generator=mv_generator,
                common=common,
                mv_prefix=mv_prefix,
                refresh_interval_minutes=refresh_interval_minutes,
                enable_refresh=enable_refresh,
            )


def _print_candidates_table(candidates: list[QueryCandidate]) -> None:
    """Print candidates in a numbered table."""
    print(f"{'#':<4} {'Hash':<12} {'Execs':<8} {'Bytes Billed':<12} {'Impact':<10} {'Basis':<8}")
    print("-" * 60)
    for i, c in enumerate(candidates, 1):
        hash_short = c.query_hash[:12]
        bytes_fmt = format_bytes(c.bytes_billed_total)
        impact_fmt = f"${c.impact_score:.2f}"
        print(
            f"{i:<4} {hash_short:<12} {c.execution_count:<8} {bytes_fmt:<12} {impact_fmt:<10} {c.eligibility_basis:<8}"
        )
    print("-" * 60)


def _prompt_selection(candidates: list[QueryCandidate], yes: bool) -> list[QueryCandidate]:
    """Prompt user to select candidates."""
    if yes:
        return candidates

    print("\nEnter selection (e.g., '1', '1,3,5', '1-3', 'all', 'none'):")
    while True:
        try:
            choice = input("> ").strip().lower()
        except EOFError:
            return []

        if choice in ("n", "no", "none", "q", "quit"):
            return []
        if choice == "all":
            return candidates
        if not choice:
            continue

        try:
            indices = set()
            parts = choice.split(",")
            for part in parts:
                part = part.strip()
                if "-" in part:
                    start, end = map(int, part.split("-"))
                    indices.update(range(start, end + 1))
                else:
                    indices.add(int(part))

            selected = []
            for idx in sorted(indices):
                if 1 <= idx <= len(candidates):
                    selected.append(candidates[idx - 1])
                else:
                    print(f"Warning: Index {idx} out of range, ignoring.")

            if selected:
                return selected
            else:
                print("No valid selection. Try again.")
        except ValueError:
            print("Invalid format. Use numbers, ranges, 'all', or 'none'.")


async def _generate_single_mv(
    candidate: QueryCandidate,
    mv_generator: MVGeneratorService,
    common: CommonConfig,
    mv_prefix: str,
    refresh_interval_minutes: int,
    enable_refresh: bool,
) -> None:
    """Generate and deploy a single MV."""
    print(f"\nProcessing {candidate.query_hash[:12]}...")
    try:
        # Generate artifact
        artifact = await mv_generator.generate_mv_artifact(
            candidate=candidate,
            target_dataset=common.dataset,
            target_project=common.project,
            mv_prefix=mv_prefix,
            refresh_interval_minutes=refresh_interval_minutes,
            enable_refresh=enable_refresh,
        )

        # Check existence
        exists = await mv_generator.check_mv_exists(
            artifact.mv_name,
            artifact.dataset_id,
            project_id=artifact.project_id,
        )
        if exists:
            print(f"  MV {artifact.mv_name} already exists. Skipping.")
            return

        # Deploy
        result = await mv_generator.deploy_mv(artifact, replace=False)
        if result["status"] == "created":
            print(f"  ✓ Created {artifact.mv_name}")
        else:
            print(f"  - {result['message']}")

    except Exception as e:
        print(f"  ✗ Failed: {e}")
