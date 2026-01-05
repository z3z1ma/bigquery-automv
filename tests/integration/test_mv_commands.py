"""Integration tests for mv commands.

Tests the mv command group (plan, apply, list, get, drop, gc, stats) to ensure
they work correctly with the CLI infrastructure.
"""

from click.testing import CliRunner

from bigquery_automv.cli.app import _register_commands, app

# Register commands before running tests
_register_commands()


class TestMVPlan:
    """Tests for `bq-automv mv plan` command."""

    def test_mv_plan_requires_project_id(self) -> None:
        """Test plan command requires --project-id."""
        runner = CliRunner()
        result = runner.invoke(app, ["mv", "plan"])

        assert result.exit_code != 0
        assert "project-id" in result.output.lower() or "missing" in result.output.lower()


class TestMVApply:
    """Tests for `bq-automv mv apply` command."""

    def test_mv_apply_requires_plan(self) -> None:
        """Test apply command requires --plan."""
        runner = CliRunner()
        result = runner.invoke(app, ["mv", "apply"])

        assert result.exit_code != 0
        assert "plan" in result.output.lower()


class TestMVList:
    """Tests for `bq-automv mv list` command."""

    def test_mv_list_requires_project_id(self) -> None:
        """Test list command requires --project-id."""
        runner = CliRunner()
        result = runner.invoke(app, ["mv", "list"])

        assert result.exit_code != 0
        assert "project-id" in result.output.lower() or "missing" in result.output.lower()


class TestMVGet:
    """Tests for `bq-automv mv get` command."""

    def test_mv_get_requires_mv_name(self) -> None:
        """Test get command requires mv_name argument."""
        runner = CliRunner()
        result = runner.invoke(app, ["mv", "get"])

        assert result.exit_code != 0
        assert "missing" in result.output.lower()


class TestMVDrop:
    """Tests for `bq-automv mv drop` command."""

    def test_mv_drop_requires_mv_name(self) -> None:
        """Test drop command requires mv_name argument."""
        runner = CliRunner()
        result = runner.invoke(app, ["mv", "drop"])

        assert result.exit_code != 0
        assert "missing" in result.output.lower()


class TestMVGC:
    """Tests for `bq-automv mv gc` command."""

    def test_mv_gc_requires_project_id(self) -> None:
        """Test gc command requires --project-id."""
        runner = CliRunner()
        result = runner.invoke(app, ["mv", "gc"])

        assert result.exit_code != 0
        assert "project-id" in result.output.lower() or "missing" in result.output.lower()


class TestMVStats:
    """Tests for `bq-automv mv stats` command."""

    def test_mv_stats_requires_mv_name(self) -> None:
        """Test stats command requires mv_name argument."""
        runner = CliRunner()
        result = runner.invoke(app, ["mv", "stats"])

        assert result.exit_code != 0
        assert "missing" in result.output.lower()
