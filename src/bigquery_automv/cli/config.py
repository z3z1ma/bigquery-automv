"""Configuration classes for CLI.

This module contains configuration dataclasses used by the CLI.
It's separated from app.py to avoid circular imports.
"""

from dataclasses import dataclass


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
