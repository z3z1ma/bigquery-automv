"""Structured logging configuration for bq-automv."""

import json
import logging
import sys
from datetime import datetime
from typing import Any

from bigquery_automv.cli.config import CommonConfig


class JSONFormatter(logging.Formatter):
    """JSON formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Add extra fields from record
        if hasattr(record, "extra"):
            log_data.update(record.extra)

        return json.dumps(log_data)


class TextFormatter(logging.Formatter):
    """Text formatter with color support for console output."""

    # ANSI color codes
    COLORS = {
        "DEBUG": "\033[36m",  # Cyan
        "INFO": "\033[32m",  # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",  # Red
        "CRITICAL": "\033[35m",  # Magenta
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as colored text."""
        level_color = self.COLORS.get(record.levelname, "")
        level_reset = self.RESET if level_color else ""

        log_format = f"{level_color}%(levelname)-8s{level_reset} %(asctime)s %(name)s: %(message)s"

        # Apply format
        formatter = logging.Formatter(log_format, datefmt="%Y-%m-%d %H:%M:%S")
        result = formatter.format(record)

        # Add exception info if present
        if record.exc_info:
            result += "\n" + self.formatException(record.exc_info)

        return result


def setup_logging(config: CommonConfig | None = None) -> logging.Logger:
    """
    Set up structured logging for the application.

    Args:
        config: CommonConfig with verbose and json flags. If None, creates defaults.

    Returns:
        Configured logger instance.
    """
    if config is None:
        config = CommonConfig(
            project="",
            region="US",
            dataset="",
            dry_run=False,
            verbose=False,
            json=False,
            interactive=None,
        )

    # Determine log level
    log_level = logging.DEBUG if config.verbose else logging.INFO

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Remove existing handlers to avoid duplicates
    root_logger.handlers.clear()

    # Create handler based on output format
    if config.json:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JSONFormatter())
    else:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(TextFormatter())

    handler.setLevel(log_level)
    root_logger.addHandler(handler)

    return logging.getLogger("bigquery_automv")


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger instance for a module.

    Args:
        name: Logger name (typically __name__ from the calling module).

    Returns:
        Logger instance.
    """
    return logging.getLogger(f"bigquery_automv.{name}")


class LogContext:
    """Context manager for adding structured context to log records."""

    def __init__(self, logger: logging.Logger, **context: Any):
        """
        Initialize log context.

        Args:
            logger: Logger instance to add context to.
            **context: Key-value pairs to add to log records.
        """
        self.logger = logger
        self.context = context
        self.old_factory = logging.getLogRecordFactory()

    def record_factory(
        self, name: str, level: int, fn: str, lno: int, msg: str, args: Any, exc_info: Any
    ) -> logging.LogRecord:
        """Custom log record factory that injects context."""
        record = self.old_factory(name, level, fn, lno, msg, args, exc_info)
        # Add context as extra field (mypy doesn't recognize dynamic attributes)
        if not hasattr(record, "extra"):
            record.extra = {}
        record.extra.update(self.context)  # type: ignore[attr-defined]
        return record

    def __enter__(self) -> LogContext:
        """Enter context and install custom record factory."""
        logging.setLogRecordFactory(self.record_factory)
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit context and restore original record factory."""
        logging.setLogRecordFactory(self.old_factory)


def log_with_context(logger: logging.Logger, level: str, message: str, **context: Any) -> None:
    """
    Log a message with additional structured context.

    Args:
        logger: Logger instance.
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        message: Log message.
        **context: Additional structured context to include.
    """
    log_func = getattr(logger, level.lower(), logger.info)

    if context:
        with LogContext(logger, **context):
            log_func(message)
    else:
        log_func(message)
