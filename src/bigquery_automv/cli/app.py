"""CLI application entry point for bq-automv."""

import os
from dataclasses import dataclass

import click

# Version metadata
__version__ = "0.1.0"


@dataclass
class CommonConfig:
    """Shared parameters for all commands."""

    project: str
    region: str
    dataset: str
    dry_run: bool
    verbose: bool
    json: bool
    enable_preview_eligibility: bool
    include_user_email: bool
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
    help="Dry run mode",
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
    help="JSON output",
)
@click.option(
    "--enable-preview-eligibility",
    is_flag=True,
    help="Enable preview features",
)
@click.option(
    "--include-user-email",
    is_flag=True,
    help="Include user email in output",
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
    enable_preview_eligibility: bool,
    include_user_email: bool,
) -> None:
    """BigQuery materialized view automation tool"""
    ctx.ensure_object(dict)
    ctx.obj["common"] = CommonConfig(
        project=project or os.getenv("GOOGLE_CLOUD_PROJECT") or "",
        region=region,
        dataset=dataset or "",
        dry_run=dry_run,
        verbose=verbose,
        json=json,
        enable_preview_eligibility=enable_preview_eligibility,
        include_user_email=include_user_email,
    )


def main() -> None:
    """Main entry point for the CLI."""
    # Import commands here to register them with the app
    import bigquery_automv.cli.commands as _commands  # noqa: F401

    app()
