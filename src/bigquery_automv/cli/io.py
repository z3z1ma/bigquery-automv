"""Standardized I/O contract for agent-friendly CLI output.

This module provides the JSON envelope format and output handling for all CLI commands.
It ensures consistent output structure across all commands and proper separation of
stdout (JSON data) and stderr (logs, warnings, errors).

Key contract:
- When --json is set: stdout contains ONLY JSON envelope, stderr contains logs/warnings
- When --json is NOT set: stdout contains human-readable output, stderr contains logs/warnings
- Exit codes: 0 = success, 1 = any failure
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

# Schema version for envelope format
SCHEMA_VERSION = "1.0.0"


@dataclass
class SuccessEnvelope:
    """Standard success response envelope.

    All successful command responses should use this envelope format
    when --json flag is set.
    """

    schema_version: str = SCHEMA_VERSION
    ok: bool = True
    command: str = ""
    target: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert envelope to dictionary for JSON serialization."""
        return {
            "schema_version": self.schema_version,
            "ok": self.ok,
            "command": self.command,
            "target": self.target,
            "data": self.data,
            "warnings": self.warnings,
            "meta": self.meta,
        }


@dataclass
class ErrorEnvelope:
    """Standard error response envelope.

    All error responses should use this envelope format when --json flag is set.
    """

    schema_version: str = SCHEMA_VERSION
    ok: bool = False
    error_type: str = ""
    code: str = ""
    message: str = ""
    remediation: str | None = None
    command: str = ""
    target: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert envelope to dictionary for JSON serialization."""
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "ok": self.ok,
            "error": {
                "type": self.error_type,
                "code": self.code,
                "message": self.message,
            },
            "command": self.command,
            "target": self.target,
        }
        if self.remediation:
            result["error"]["remediation"] = self.remediation
        return result


class OutputFormatter:
    """Handles output formatting and routing for CLI commands.

    This class ensures consistent output format across all commands and proper
    separation of stdout (data) and stderr (logs/warnings).

    Usage:
        formatter = OutputFormatter(json_mode=True)

        # Success
        formatter.success(
            command="candidates.discover",
            data={"candidates": [...], "count": 10},
            target="my-project"
        )

        # Error
        formatter.error(
            error_type="NotFoundError",
            code="DATASET_NOT_FOUND",
            message="Dataset not found",
            remediation="Create the dataset first",
            command="mv.plan",
            target="my-project.my_dataset"
        )

        # Warnings (accumulated until next success/error call)
        formatter.warn("Query hash abc123 has low execution count")
    """

    def __init__(self, json_mode: bool = False, version: str = "1.5.0") -> None:
        """Initialize the output formatter.

        Args:
            json_mode: If True, output JSON envelopes; otherwise human-readable
            version: Tool version for metadata
        """
        self.json_mode = json_mode
        self.version = version
        self._warnings: list[str] = []

    def warn(self, message: str) -> None:
        """Add a warning to be included in next output envelope.

        Args:
            message: Warning message
        """
        self._warnings.append(message)

    def emit_warning(self, message: str) -> None:
        """Emit a warning immediately to stderr (not included in envelope).

        Use this for warnings that should be shown immediately rather than
        accumulated in the next envelope.

        Args:
            message: Warning message
        """
        print(f"WARNING: {message}", file=sys.stderr)

    def success(
        self,
        command: str,
        data: dict[str, Any],
        target: str = "",
        meta: dict[str, Any] | None = None,
    ) -> None:
        """Emit a success response.

        In JSON mode, outputs a JSON envelope to stdout.
        In human mode, outputs human-readable text to stdout.

        Args:
            command: Command name (e.g., "candidates.discover")
            data: Command result data
            target: Target identifier (e.g., "project.dataset")
            meta: Optional metadata (e.g., dry_run status)
        """
        # Combine explicit meta with auto-generated metadata
        combined_meta: dict[str, Any] = {
            "tool_version": self.version,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        if meta:
            combined_meta.update(meta)

        if self.json_mode:
            envelope = SuccessEnvelope(
                command=command,
                target=target,
                data=data,
                warnings=self._warnings.copy(),
                meta=combined_meta,
            )
            print(json.dumps(envelope.to_dict(), indent=2))
        else:
            # Emit warnings to stderr in human mode
            for warning in self._warnings:
                self.emit_warning(warning)

            # Output data in human-readable format
            # The caller can provide a custom formatter via data
            # If data has a special key, use it
            if "_human_format" in data:
                print(data["_human_format"])
            else:
                # Default human formatting
                self._print_human_success(command, data, target)

        # Clear warnings after emitting
        self._warnings.clear()

    def error(
        self,
        error_type: str,
        message: str,
        code: str = "",
        remediation: str | None = None,
        command: str = "",
        target: str = "",
    ) -> None:
        """Emit an error response.

        In JSON mode, outputs a JSON envelope to stdout.
        In human mode, outputs error message to stderr.

        Args:
            error_type: Error type (e.g., "BigQueryError", "NotFoundError")
            message: Error message
            code: Error code (e.g., "DATASET_NOT_FOUND")
            remediation: Optional suggestion for fixing the error
            command: Command name
            target: Target identifier
        """
        if self.json_mode:
            envelope = ErrorEnvelope(
                error_type=error_type,
                code=code,
                message=message,
                remediation=remediation,
                command=command,
                target=target,
            )
            print(json.dumps(envelope.to_dict(), indent=2))
        else:
            # Emit warnings first
            for warning in self._warnings:
                self.emit_warning(warning)

            # Human-readable error
            print(f"Error: {message}", file=sys.stderr)
            if code:
                print(f"  Code: {code}", file=sys.stderr)
            if remediation:
                print(f"  Suggestion: {remediation}", file=sys.stderr)

        # Clear warnings after emitting
        self._warnings.clear()

    def _print_human_success(self, command: str, data: dict[str, Any], target: str) -> None:
        """Print human-readable success output.

        Args:
            command: Command name
            data: Result data
            target: Target identifier
        """
        # Simple default formatting - can be overridden per command
        if target:
            print(f"{command} completed successfully for {target}")
        else:
            print(f"{command} completed successfully")

        # Print key data items
        for key, value in data.items():
            if key.startswith("_"):
                continue  # Skip private keys
            if isinstance(value, list):
                print(f"  {key}: {len(value)} items")
            elif isinstance(value, dict):
                print(f"  {key}: {len(value)} keys")
            else:
                print(f"  {key}: {value}")


def format_result_json(
    result: Any,
    command: str = "",
    target: str = "",
    warnings: list[str] | None = None,
    meta: dict[str, Any] | None = None,
) -> str:
    """Format a result as a JSON envelope.

    This is a convenience function for simple cases where you don't need
    the full OutputFormatter class.

    Args:
        result: Result object or dict
        command: Command name
        target: Target identifier
        warnings: Optional list of warnings
        meta: Optional metadata

    Returns:
        JSON string
    """
    envelope = SuccessEnvelope(
        command=command,
        target=target,
        data={"result": result} if not isinstance(result, dict) else result,
        warnings=warnings or [],
        meta=meta or {},
    )
    return json.dumps(envelope.to_dict(), indent=2)


def format_error_json(
    error_type: str,
    message: str,
    code: str = "",
    remediation: str | None = None,
    command: str = "",
    target: str = "",
) -> str:
    """Format an error as a JSON envelope.

    This is a convenience function for simple cases where you don't need
    the full OutputFormatter class.

    Args:
        error_type: Error type
        message: Error message
        code: Error code
        remediation: Optional remediation
        command: Command name
        target: Target identifier

    Returns:
        JSON string
    """
    envelope = ErrorEnvelope(
        error_type=error_type,
        code=code,
        message=message,
        remediation=remediation,
        command=command,
        target=target,
    )
    return json.dumps(envelope.to_dict(), indent=2)
