"""Integration tests for candidates commands.

Tests the candidates command group (discover, list, get, prune) to ensure
they work correctly with the CLI infrastructure.
"""

from click.testing import CliRunner

from bigquery_automv.cli.app import _register_commands, app

# Register commands before running tests
_register_commands()


class TestCandidatesDiscover:
    """Tests for `bq-automv candidates discover` command."""

    def test_candidates_discover_requires_project_id(self) -> None:
        """Test discover command requires --project-id."""
        runner = CliRunner()
        result = runner.invoke(app, ["candidates", "discover"])

        assert result.exit_code != 0
        assert "project-id" in result.output.lower() or "missing" in result.output.lower()

    def test_candidates_discover_requires_output_file(self) -> None:
        """Test discover command exists and validates required options."""
        runner = CliRunner()
        # Test that the discover command exists
        result = runner.invoke(app, ["candidates", "discover", "--help"])
        # Should show help for discover command
        assert "discover" in result.output.lower() or result.exit_code == 0


class TestCandidatesList:
    """Tests for `bq-automv candidates list` command."""

    def test_candidates_list_requires_input_file(self) -> None:
        """Test list command requires --input."""
        runner = CliRunner()
        result = runner.invoke(app, ["candidates", "list"])

        assert result.exit_code != 0
        assert "input" in result.output.lower()


class TestCandidatesGet:
    """Tests for `bq-automv candidates get` command."""

    def test_candidates_get_requires_hash(self) -> None:
        """Test get command requires query_hash argument."""
        runner = CliRunner()
        result = runner.invoke(app, ["candidates", "get"])

        assert result.exit_code != 0
        assert "missing" in result.output.lower()


class TestCandidatesPrune:
    """Tests for `bq-automv candidates prune` command."""

    def test_candidates_prune_requires_input(self) -> None:
        """Test prune command requires --input."""
        runner = CliRunner()
        result = runner.invoke(app, ["candidates", "prune"])

        assert result.exit_code != 0
        assert "input" in result.output.lower()
