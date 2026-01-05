"""Command modules for bq-automv CLI."""

# New command groups
from bigquery_automv.cli.commands import candidates, mv, spec

__all__ = [
    "candidates",
    "mv",
    "spec",
]
