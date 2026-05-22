"""Structlog configuration helpers for Jobfeed services and CLI commands."""

from __future__ import annotations

import logging
import sys
from typing import Protocol, cast

import structlog
from structlog.typing import Processor


class JobfeedLogger(Protocol):
    """Small logger protocol used by services without importing structlog APIs."""

    def info(self, event: str, **kwargs: object) -> object:
        """Emit an informational event.

        Args:
            event: Stable event name or short message.
            kwargs: Structured event attributes.

        Returns:
            Logger-specific return value.
        """
        ...

    def error(self, event: str, **kwargs: object) -> object:
        """Emit an error event.

        Args:
            event: Stable event name or short message.
            kwargs: Structured event attributes.

        Returns:
            Logger-specific return value.
        """
        ...

    def warning(self, event: str, **kwargs: object) -> object:
        """Emit a warning event.

        Args:
            event: Stable event name or short message.
            kwargs: Structured event attributes.

        Returns:
            Logger-specific return value.
        """
        ...

    def debug(self, event: str, **kwargs: object) -> object:
        """Emit a debug event.

        Args:
            event: Stable event name or short message.
            kwargs: Structured event attributes.

        Returns:
            Logger-specific return value.
        """
        ...


def configure_logging(log_level: str = "info", log_format: str = "human") -> None:
    """Configure structlog for Phase 0 command and service output.

    Args:
        log_level: Minimum level name such as "info" or "debug".
        log_format: "json" for JSON lines or "human" for console output.

    Raises:
        ValueError: If `log_level` or `log_format` is unsupported.
    """
    level = _level_from_name(log_level)
    renderer = _renderer_for_format(log_format)
    structlog.contextvars.clear_contextvars()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=False,
    )


def bind_run_id(run_id: str) -> None:
    """Bind a pipeline run identifier into future log events.

    Args:
        run_id: Pipeline run identifier to attach to structlog context.
    """
    structlog.contextvars.bind_contextvars(run_id=run_id)


def get_logger() -> JobfeedLogger:
    """Return the configured application logger.

    Returns:
        Structlog logger narrowed to the service-facing protocol.
    """
    return cast(JobfeedLogger, structlog.get_logger("jobfeed"))


def _level_from_name(log_level: str) -> int:
    normalized = log_level.upper()
    level = logging.getLevelName(normalized)
    if not isinstance(level, int):
        raise ValueError(f"unsupported log level: {log_level}")
    return level


def _renderer_for_format(log_format: str) -> Processor:
    if log_format == "json":
        return structlog.processors.JSONRenderer()
    if log_format == "human":
        return structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())
    raise ValueError(f"unsupported log format: {log_format}")


__all__ = [
    "JobfeedLogger",
    "bind_run_id",
    "configure_logging",
    "get_logger",
]
