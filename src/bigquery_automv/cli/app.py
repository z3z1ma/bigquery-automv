"""CLI application entry point for bq-automv."""

import os
from dataclasses import dataclass

import click

from bigquery_automv.cli.io import OutputFormatter

# Version metadata
__version__ = "1.5.0"


@dataclass
class CommonConfig:
    """Shared parameters for all commands."""

    project: str
    region: str
    dataset: str
    dry_run: bool
    verbose: bool
    json: bool
    interactive: bool | None
    include_query_text: bool = False


@click.group()
@click.version_option(__version__)
@click.option(
    "--project",
    envvar=["GOOGLE_CLOUD_PROJECT", "BQ_AUTOMV_PROJECT"],
    help="GCP project ID",
)
@click.option(
    "--region",
    envvar="BQ_AUTOMV_REGION",
    default="US",
    help="BigQuery region",
)
@click.option(
    "--dataset",
    envvar="BQ_AUTOMV_DATASET",
    help="BigQuery dataset",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Dry run mode (show what would be done without making changes)",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Verbose logging",
)
@click.option(
    "--json",
    is_flag=True,
    help="JSON envelope output (stdout = JSON only, stderr = logs)",
)
@click.option(
    "--interactive/--non-interactive",
    default=None,
    help="Force interactive/non-interactive mode (default: auto-detect)",
)
@click.pass_context
def app(
    ctx: click.Context,
    project: str | None,
    region: str,
    dataset: str | None,
    dry_run: bool,
    verbose: bool,
    json: bool,
    interactive: bool | None,
) -> None:
    """BigQuery materialized view automation tool.

    Global options:
    """
    ctx.ensure_object(dict)

    # Create CommonConfig
    common = CommonConfig(
        project=project or os.getenv("GOOGLE_CLOUD_PROJECT") or "",
        region=region,
        dataset=dataset or "",
        dry_run=dry_run,
        verbose=verbose,
        json=json,
        interactive=interactive,
    )
    ctx.obj["common"] = common

    # Create OutputFormatter for JSON envelope output
    ctx.obj["formatter"] = OutputFormatter(json_mode=json, version=__version__)


def main() -> None:
    """Main entry point for the CLI."""
    # Import command groups here to register them with the app
    from bigquery_automv.cli.commands import (  # noqa: F401
        candidates,
        mv,
        spec,
    )

    # Add command groups to the app
    app.add_command(candidates.candidates_group)
    app.add_command(mv.mv_group)
    app.add_command(spec.spec_command)

    app()
