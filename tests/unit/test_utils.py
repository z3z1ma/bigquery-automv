"""Unit tests for CLI utilities.

Tests interactivity detection, prompting behavior, and progress bar logic.
"""

import sys

from bigquery_automv.cli.utils import (
    confirm_destructive_action,
    enable_progress_bar,
    is_interactive,
    safe_prompt,
    should_prompt,
)


class TestIsInteractive:
    """Tests for is_interactive()."""

    def test_is_interactive_true(self, monkeypatch: object) -> None:
        """Test is_interactive() returns True when both stdin and stdout are TTYs."""
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

        assert is_interactive() is True

    def test_is_interactive_false_stdin_not_tty(self, monkeypatch: object) -> None:
        """Test is_interactive() returns False when stdin is not a TTY."""
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

        assert is_interactive() is False

    def test_is_interactive_false_stdout_not_tty(self, monkeypatch: object) -> None:
        """Test is_interactive() returns False when stdout is not a TTY."""
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: False)

        assert is_interactive() is False

    def test_is_interactive_false_both_not_tty(self, monkeypatch: object) -> None:
        """Test is_interactive() returns False when neither is a TTY."""
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: False)

        assert is_interactive() is False


class TestShouldPrompt:
    """Tests for should_prompt()."""

    def test_explicit_interactive_true(self, monkeypatch: object) -> None:
        """Test should_prompt() returns True when explicitly set to interactive."""
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: False)

        assert should_prompt(interactive_flag=True) is True

    def test_explicit_non_interactive(self, monkeypatch: object) -> None:
        """Test should_prompt() returns False when explicitly set to non-interactive."""
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

        assert should_prompt(interactive_flag=False) is False

    def test_json_mode_never_prompts(self, monkeypatch: object) -> None:
        """Test should_prompt() returns False in JSON mode."""
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

        assert should_prompt(interactive_flag=None, json_mode=True) is False

    def test_explicit_interactive_overrides_json_mode(self, monkeypatch: object) -> None:
        """Test explicit interactive flag takes precedence over JSON mode."""
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

        # Explicit interactive flag takes precedence
        assert should_prompt(interactive_flag=True, json_mode=True) is True

    def test_explicit_non_interactive_overrides_json_mode(self, monkeypatch: object) -> None:
        """Test explicit non-interactive flag takes precedence."""
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

        # Explicit non-interactive flag takes precedence
        assert should_prompt(interactive_flag=False, json_mode=False) is False

    def test_non_tty_never_prompts(self, monkeypatch: object) -> None:
        """Test should_prompt() returns False when stdin is not a TTY."""
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

        assert should_prompt(interactive_flag=None, json_mode=False) is False

    def test_default_interactive_when_tty(self, monkeypatch: object) -> None:
        """Test should_prompt() returns True by default in interactive terminal."""
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

        assert should_prompt(interactive_flag=None, json_mode=False) is True


class MockFormatter:
    """Mock OutputFormatter for testing."""

    def __init__(self, json_mode: bool = False) -> None:
        self.json_mode = json_mode
        self.warnings: list[str] = []

    def emit_warning(self, message: str) -> None:
        """Emit a warning."""
        self.warnings.append(message)


class TestConfirmDestructiveAction:
    """Tests for confirm_destructive_action()."""

    def test_json_mode_returns_default(self, monkeypatch: object) -> None:
        """Test confirm_destructive_action() returns default in JSON mode."""
        formatter = MockFormatter(json_mode=True)

        # Should return default (True) without prompting
        result = confirm_destructive_action("Drop MV?", formatter, default=True)

        assert result is True
        assert len(formatter.warnings) == 1
        assert "Non-interactive mode: proceeding with" in formatter.warnings[0]

    def test_json_mode_returns_false_default(self, monkeypatch: object) -> None:
        """Test confirm_destructive_action() returns False default in JSON mode."""
        formatter = MockFormatter(json_mode=True)

        result = confirm_destructive_action("Drop MV?", formatter, default=False)

        assert result is False
        assert "Non-interactive mode: skipping" in formatter.warnings[0]

    def test_non_tty_returns_default(self, monkeypatch: object) -> None:
        """Test confirm_destructive_action() returns default when stdin is not TTY."""
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
        formatter = MockFormatter(json_mode=False)

        result = confirm_destructive_action("Drop MV?", formatter, default=True)

        assert result is True

    def test_interactive_prompts(self, monkeypatch: object) -> None:
        """Test confirm_destructive_action() prompts in interactive mode."""
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

        formatter = MockFormatter(json_mode=False)

        # Mock click.confirm to return True
        def mock_confirm(message: str, default: bool = False) -> bool:
            return True

        monkeypatch.setattr("click.confirm", mock_confirm)

        result = confirm_destructive_action("Drop MV?", formatter)

        assert result is True


class TestEnableProgressBar:
    """Tests for enable_progress_bar()."""

    def test_json_mode_never_shows_progress(self, monkeypatch: object) -> None:
        """Test enable_progress_bar() returns False in JSON mode."""
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

        assert enable_progress_bar(json_mode=True, verbose=True) is False

    def test_non_interactive_never_shows_progress(self, monkeypatch: object) -> None:
        """Test enable_progress_bar() returns False when not interactive."""
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: False)

        assert enable_progress_bar(json_mode=False, verbose=True) is False

    def test_verbose_required(self, monkeypatch: object) -> None:
        """Test enable_progress_bar() requires verbose=True."""
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

        assert enable_progress_bar(json_mode=False, verbose=False) is False

    def test_shows_when_interactive_and_verbose(self, monkeypatch: object) -> None:
        """Test enable_progress_bar() returns True when interactive and verbose."""
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

        assert enable_progress_bar(json_mode=False, verbose=True) is True


class TestSafePrompt:
    """Tests for safe_prompt()."""

    def test_json_mode_returns_default(self, monkeypatch: object) -> None:
        """Test safe_prompt() returns default in JSON mode."""
        result = safe_prompt("Enter value", default="default_value", json_mode=True)

        assert result == "default_value"

    def test_non_tty_returns_default(self, monkeypatch: object) -> None:
        """Test safe_prompt() returns default when stdin is not TTY."""
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)

        result = safe_prompt("Enter value", default="default_value", json_mode=False)

        assert result == "default_value"

    def test_no_default_returns_none_in_json_mode(self) -> None:
        """Test safe_prompt() returns None when no default in JSON mode."""
        result = safe_prompt("Enter value", default=None, json_mode=True)

        assert result is None

    def test_interactive_prompts_with_default(self, monkeypatch: object) -> None:
        """Test safe_prompt() prompts with default value shown."""
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

        # Mock input to return empty string (should use default)
        monkeypatch.setattr("builtins.input", lambda x: "")

        result = safe_prompt("Enter value", default="default_value", json_mode=False)

        assert result == "default_value"

    def test_interactive_prompts_no_default(self, monkeypatch: object) -> None:
        """Test safe_prompt() prompts without default value."""
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

        # Mock input to return user input
        monkeypatch.setattr("builtins.input", lambda x: "user_input")

        result = safe_prompt("Enter value", default=None, json_mode=False)

        assert result == "user_input"

    def test_interactive_user_input_overrides_default(self, monkeypatch: object) -> None:
        """Test safe_prompt() uses user input instead of default."""
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

        # Mock input to return non-empty string
        monkeypatch.setattr("builtins.input", lambda x: "user_value")

        result = safe_prompt("Enter value", default="default_value", json_mode=False)

        assert result == "user_value"

    def test_interactive_strips_input(self, monkeypatch: object) -> None:
        """Test safe_prompt() strips whitespace from input."""
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

        # Mock input to return input with spaces
        monkeypatch.setattr("builtins.input", lambda x: "  user_value  ")

        result = safe_prompt("Enter value", default=None, json_mode=False)

        assert result == "user_value"

    def test_interactive_eof_returns_none(self, monkeypatch: object) -> None:
        """Test safe_prompt() returns None on EOFError."""
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

        # Mock input to raise EOFError
        def mock_input(prompt: str) -> str:
            raise EOFError

        monkeypatch.setattr("builtins.input", mock_input)

        result = safe_prompt("Enter value", default=None, json_mode=False)

        assert result is None
