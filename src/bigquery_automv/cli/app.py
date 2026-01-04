"""CLI application entry point for bq-automv."""

import os
from dataclasses import dataclass, field
from typing import Annotated

from cyclopts import App, Parameter

# Version metadata
__version__ = "0.1.0"


@Parameter(name="*")  # Flatten namespace so all commands inherit these parameters
@dataclass(kw_only=True)
class CommonConfig:
    """Shared parameters for all commands."""

    project: Annotated[str, Parameter(name="--project", env_var=["GOOGLE_CLOUD_PROJECT", "BQ_AUTOMV_PROJECT"])] = field(
        default_factory=lambda: os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("BQ_AUTOMV_PROJECT") or ""
    )
    region: Annotated[str, Parameter(name="--region", env_var=["BQ_AUTOMV_REGION"])] = "US"
    dataset: Annotated[str, Parameter(name="--dataset", env_var=["BQ_AUTOMV_DATASET"])] = field(
        default_factory=lambda: os.getenv("BQ_AUTOMV_DATASET") or ""
    )
    dry_run: Annotated[bool, Parameter(name="--dry-run", negative="")] = False
    verbose: Annotated[bool, Parameter(name=["--verbose", "-v"], negative="")] = False
    json: Annotated[bool, Parameter(name="--json", negative="")] = False
    enable_preview_eligibility: Annotated[bool, Parameter(name="--enable-preview-eligibility", negative="")] = False
    include_user_email: Annotated[bool, Parameter(name="--include-user-email", negative="")] = False


app = App(
    name="bq-automv",
    help="BigQuery materialized view automation tool",
    version=f"bq-automv {__version__}",
)


def main() -> None:
    """Main entry point for the CLI."""
    # Import commands here to register them with the app
    import bigquery_automv.cli.commands as _commands  # noqa: F401

    app()
