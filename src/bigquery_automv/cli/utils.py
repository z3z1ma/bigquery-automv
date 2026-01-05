"""Utilities for CLI behavior, especially interactivity detection.

This module provides utilities to determine whether the CLI should behave
interactively (showing prompts, progress bars, etc.) or non-interactively
(suitable for automation, scripting, and agent workflows).
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from bigquery_automv.cli.io import OutputFormatter


def is_interactive() -> bool:
    """Determine if the current environment is interactive.

    Returns True only if:
    - stdin is a TTY (not piped/file)
    - stdout is a TTY (not redirected)

    This is useful for deciding whether to show progress bars, colors, etc.

    Returns:
        True if running in an interactive terminal, False otherwise
    """
    return sys.stdin.isatty() and sys.stdout.isatty()


def should_prompt(
    interactive_flag: bool | None = None,
    json_mode: bool = False,
) -> bool:
    """Determine if prompts should be shown.

    Rules (in order of precedence):
    1. If --interactive explicitly provided: use that value
    2. If --json mode: never prompt (JSON output must be parseable)
    3. If stdin not TTY: never prompt (non-interactive environment)
    4. Otherwise: prompt (default interactive behavior)

    Args:
        interactive_flag: Explicit --interactive/--non-interactive flag value
        json_mode: Whether JSON output mode is enabled

    Returns:
        True if prompts should be shown, False otherwise
    """
    # Explicit flag takes precedence
    if interactive_flag is not None:
        return interactive_flag

    # Never prompt in JSON mode (breaks parsing)
    if json_mode:
        return False

    # Never prompt if stdin is not a TTY (piped input)
    if not sys.stdin.isatty():
        return False

    # Default to prompting
    return True


def confirm_destructive_action(
    message: str,
    formatter: OutputFormatter,
    default: bool = False,
) -> bool:
    """Prompt for confirmation of a destructive action.

    This function handles confirmation prompts with proper handling for
    non-interactive environments.

    Args:
        message: Confirmation message
        formatter: OutputFormatter instance
        default: Default value if non-interactive (True = proceed, False = abort)

    Returns:
        True if user confirmed or should proceed automatically, False otherwise
    """
    # In JSON mode, never prompt - use default
    if formatter.json_mode:
        if default:
            formatter.emit_warning(f"Non-interactive mode: proceeding with {message}")
        else:
            formatter.emit_warning(f"Non-interactive mode: skipping {message}")
        return default

    # Non-interactive TTY (shouldn't happen but handle it)
    if not sys.stdin.isatty():
        if default:
            formatter.emit_warning(f"Non-interactive mode: proceeding with {message}")
        else:
            formatter.emit_warning(f"Non-interactive mode: skipping {message}")
        return default

    # Interactive prompt
    try:
        return click.confirm(message, default=default)
    except (EOFError, KeyboardInterrupt):
        # User aborted
        return False


def enable_progress_bar(
    json_mode: bool = False,
    verbose: bool = False,
) -> bool:
    """Determine if progress bars should be shown.

    Progress bars should only be shown in interactive mode with verbose output.

    Args:
        json_mode: Whether JSON output mode is enabled
        verbose: Whether verbose output is enabled

    Returns:
        True if progress bars should be shown, False otherwise
    """
    # Never show in JSON mode
    if json_mode:
        return False

    # Only show if interactive and verbose
    return is_interactive() and verbose


def safe_prompt(
    prompt_text: str,
    default: str | None = None,
    json_mode: bool = False,
) -> str | None:
    """Safely prompt for user input.

    Returns None in non-interactive environments instead of hanging.

    Args:
        prompt_text: Text to display to user
        default: Default value if non-interactive
        json_mode: Whether JSON output mode is enabled

    Returns:
        User input, default value, or None
    """
    # In JSON mode, never prompt
    if json_mode:
        return default

    # Non-interactive TTY
    if not sys.stdin.isatty():
        return default

    # Interactive prompt
    try:
        if default is not None:
            prompt_text = f"{prompt_text} [{default}]: "
            response = input(prompt_text).strip()
            return response if response else default
        else:
            return input(f"{prompt_text}: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
