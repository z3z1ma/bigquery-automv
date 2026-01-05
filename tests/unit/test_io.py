"""Unit tests for CLI I/O contract.

Tests the SuccessEnvelope, ErrorEnvelope, and OutputFormatter classes
to ensure they produce correct JSON output and handle edge cases.
"""

import json

from bigquery_automv.cli.io import (
    ErrorEnvelope,
    OutputFormatter,
    SuccessEnvelope,
    format_error_json,
    format_result_json,
)

SCHEMA_VERSION = "1.0.0"


class TestSuccessEnvelope:
    """Tests for SuccessEnvelope dataclass."""

    def test_default_values(self) -> None:
        """Test SuccessEnvelope with default values."""
        envelope = SuccessEnvelope()

        assert envelope.schema_version == SCHEMA_VERSION
        assert envelope.ok is True
        assert envelope.command == ""
        assert envelope.target == ""
        assert envelope.data == {}
        assert envelope.warnings == []
        assert envelope.meta == {}

    def test_with_values(self) -> None:
        """Test SuccessEnvelope with custom values."""
        envelope = SuccessEnvelope(
            command="mv.apply",
            target="my-project.my_dataset",
            data={"created": ["mv1"], "replaced": []},
            warnings=["Warning 1"],
            meta={"dry_run": True},
        )

        assert envelope.schema_version == SCHEMA_VERSION
        assert envelope.ok is True
        assert envelope.command == "mv.apply"
        assert envelope.target == "my-project.my_dataset"
        assert envelope.data == {"created": ["mv1"], "replaced": []}
        assert envelope.warnings == ["Warning 1"]
        assert envelope.meta == {"dry_run": True}

    def test_to_dict(self) -> None:
        """Test SuccessEnvelope.to_dict() produces correct structure."""
        envelope = SuccessEnvelope(
            command="mv.apply",
            data={"count": 5},
        )

        result = envelope.to_dict()

        assert result == {
            "schema_version": SCHEMA_VERSION,
            "ok": True,
            "command": "mv.apply",
            "target": "",
            "data": {"count": 5},
            "warnings": [],
            "meta": {},
        }


class TestErrorEnvelope:
    """Tests for ErrorEnvelope dataclass."""

    def test_default_values(self) -> None:
        """Test ErrorEnvelope with default values."""
        envelope = ErrorEnvelope()

        assert envelope.schema_version == SCHEMA_VERSION
        assert envelope.ok is False
        assert envelope.error_type == ""
        assert envelope.code == ""
        assert envelope.message == ""
        assert envelope.remediation is None
        assert envelope.command == ""
        assert envelope.target == ""

    def test_with_values(self) -> None:
        """Test ErrorEnvelope with custom values."""
        envelope = ErrorEnvelope(
            error_type="BigQueryError",
            code="NOT_FOUND",
            message="Dataset not found",
            remediation="Create the dataset first",
            command="mv.plan",
            target="my-project.my_dataset",
        )

        assert envelope.schema_version == SCHEMA_VERSION
        assert envelope.ok is False
        assert envelope.error_type == "BigQueryError"
        assert envelope.code == "NOT_FOUND"
        assert envelope.message == "Dataset not found"
        assert envelope.remediation == "Create the dataset first"
        assert envelope.command == "mv.plan"
        assert envelope.target == "my-project.my_dataset"

    def test_to_dict(self) -> None:
        """Test ErrorEnvelope.to_dict() produces correct structure."""
        envelope = ErrorEnvelope(
            error_type="NotFoundError",
            code="MV_NOT_FOUND",
            message="Materialized view not found",
        )

        result = envelope.to_dict()

        assert result == {
            "schema_version": SCHEMA_VERSION,
            "ok": False,
            "error": {
                "type": "NotFoundError",
                "code": "MV_NOT_FOUND",
                "message": "Materialized view not found",
            },
            "command": "",
            "target": "",
        }

    def test_to_dict_with_remediation(self) -> None:
        """Test ErrorEnvelope.to_dict() includes remediation when provided."""
        envelope = ErrorEnvelope(
            error_type="BigQueryError",
            code="REGION_MISMATCH",
            message="Regions don't match",
            remediation="Ensure all resources are in the same region",
        )

        result = envelope.to_dict()

        assert result["error"]["remediation"] == "Ensure all resources are in the same region"


class TestOutputFormatter:
    """Tests for OutputFormatter class."""

    def test_json_mode_success(self, capsys: object) -> None:
        """Test OutputFormatter.success() in JSON mode."""
        formatter = OutputFormatter(json_mode=True)

        formatter.success(
            command="candidates.discover",
            data={"candidates": [], "count": 0},
            target="my-project",
            meta={"dry_run": False},
        )

        captured = capsys.readouterr()
        output = json.loads(captured.out)

        assert output["ok"] is True
        assert output["command"] == "candidates.discover"
        assert output["target"] == "my-project"
        assert output["data"]["count"] == 0
        assert "timestamp" in output["meta"]
        assert "tool_version" in output["meta"]

    def test_json_mode_error(self, capsys: object) -> None:
        """Test OutputFormatter.error() in JSON mode."""
        formatter = OutputFormatter(json_mode=True)

        formatter.error(
            error_type="BigQueryError",
            code="NOT_FOUND",
            message="Dataset not found",
            remediation="Create the dataset first",
            command="mv.plan",
            target="my-project.my_dataset",
        )

        captured = capsys.readouterr()
        output = json.loads(captured.out)

        assert output["ok"] is False
        assert output["error"]["type"] == "BigQueryError"
        assert output["error"]["code"] == "NOT_FOUND"
        assert output["error"]["message"] == "Dataset not found"
        assert output["error"]["remediation"] == "Create the dataset first"
        assert output["command"] == "mv.plan"

    def test_json_mode_warnings_accumulated(self, capsys: object) -> None:
        """Test that warnings are accumulated and included in next output."""
        formatter = OutputFormatter(json_mode=True)

        formatter.warn("Warning 1")
        formatter.warn("Warning 2")

        formatter.success(
            command="mv.apply",
            data={"created": ["mv1"]},
        )

        captured = capsys.readouterr()
        output = json.loads(captured.out)

        assert output["warnings"] == ["Warning 1", "Warning 2"]

    def test_json_mode_warnings_cleared_after_output(self, capsys: object) -> None:
        """Test that warnings are cleared after being emitted."""
        formatter = OutputFormatter(json_mode=True)

        formatter.warn("Warning 1")
        formatter.success(command="test", data={})

        # Capture first output
        captured1 = capsys.readouterr()
        output1 = json.loads(captured1.out)
        assert output1["warnings"] == ["Warning 1"]

        # Second output should have no warnings
        formatter.success(command="test2", data={})
        captured2 = capsys.readouterr()
        output2 = json.loads(captured2.out)
        assert output2["warnings"] == []

    def test_human_mode_success(self, capsys: object) -> None:
        """Test OutputFormatter.success() in human mode."""
        formatter = OutputFormatter(json_mode=False)

        formatter.success(
            command="mv.apply",
            data={"created": ["mv1"], "replaced": []},
            target="my-project.my_dataset",
        )

        captured = capsys.readouterr()
        output = captured.out

        assert "mv.apply completed successfully" in output
        assert "my-project.my_dataset" in output

    def test_human_mode_warnings_to_stderr(self, capsys: object) -> None:
        """Test that warnings go to stderr in human mode."""
        formatter = OutputFormatter(json_mode=False)

        formatter.warn("This is a warning")
        formatter.success(command="test", data={})

        captured = capsys.readouterr()
        assert "WARNING: This is a warning" in captured.err

    def test_human_mode_error(self, capsys: object) -> None:
        """Test OutputFormatter.error() in human mode."""
        formatter = OutputFormatter(json_mode=False)

        formatter.error(
            error_type="NotFoundError",
            message="MV not found",
            code="MV_NOT_FOUND",
            remediation="Check the MV name",
        )

        captured = capsys.readouterr()
        error_output = captured.err

        assert "Error: MV not found" in error_output
        assert "Code: MV_NOT_FOUND" in error_output
        assert "Suggestion: Check the MV name" in error_output

    def test_emit_warning(self, capsys: object) -> None:
        """Test emit_warning() writes directly to stderr."""
        formatter = OutputFormatter(json_mode=False)

        formatter.emit_warning("Immediate warning")

        captured = capsys.readouterr()
        assert "WARNING: Immediate warning" in captured.err

    def test_human_format_custom(self, capsys: object) -> None:
        """Test custom human format via _human_format key."""
        formatter = OutputFormatter(json_mode=False)

        formatter.success(
            command="test",
            data={"_human_format": "Custom output message"},
        )

        captured = capsys.readouterr()
        assert "Custom output message" in captured.out


class TestConvenienceFunctions:
    """Tests for convenience functions."""

    def test_format_result_json(self) -> None:
        """Test format_result_json() produces valid JSON."""
        result = format_result_json(
            result={"count": 5},
            command="mv.list",
            target="my-project",
            warnings=["Warning 1"],
            meta={"dry_run": True},
        )

        output = json.loads(result)

        assert output["ok"] is True
        assert output["command"] == "mv.list"
        assert output["target"] == "my-project"
        assert output["data"]["count"] == 5
        assert output["warnings"] == ["Warning 1"]
        assert output["meta"]["dry_run"] is True

    def test_format_result_json_with_dict_result(self) -> None:
        """Test format_result_json() with dict result."""
        result = format_result_json(
            result={"items": ["a", "b"], "total": 2},
            command="test",
        )

        output = json.loads(result)

        assert output["data"]["items"] == ["a", "b"]
        assert output["data"]["total"] == 2

    def test_format_error_json(self) -> None:
        """Test format_error_json() produces valid JSON."""
        result = format_error_json(
            error_type="BigQueryError",
            message="Query failed",
            code="QUERY_ERROR",
            remediation="Fix the query syntax",
            command="mv.apply",
            target="my-project",
        )

        output = json.loads(result)

        assert output["ok"] is False
        assert output["error"]["type"] == "BigQueryError"
        assert output["error"]["code"] == "QUERY_ERROR"
        assert output["error"]["message"] == "Query failed"
        assert output["error"]["remediation"] == "Fix the query syntax"
        assert output["command"] == "mv.apply"
        assert output["target"] == "my-project"

    def test_format_error_json_minimal(self) -> None:
        """Test format_error_json() with minimal parameters."""
        result = format_error_json(
            error_type="Error",
            message="Something went wrong",
        )

        output = json.loads(result)

        assert output["ok"] is False
        assert output["error"]["type"] == "Error"
        assert output["error"]["message"] == "Something went wrong"
        assert "remediation" not in output["error"]
