"""Structlog configuration, OTel tracing, and Sentry init for Jobfeed."""

from __future__ import annotations

import logging
import sys
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Protocol, cast

import structlog
from structlog.typing import EventDict, Processor, WrappedLogger

if TYPE_CHECKING:
    from collections.abc import Iterator

    from jobfeed.config import ObservabilitySettings


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


class SpanWrapper(Protocol):
    """Tracer facade so services never import opentelemetry directly."""

    def start_as_current_span(self, name: str) -> Any:
        """Return a context manager wrapping a named trace span.

        Args:
            name: Human-readable span name.

        Returns:
            Context manager yielding the active span (or nothing for no-op).
        """
        ...


class _NoOpSpanWrapper:
    """Span wrapper that does nothing when OTel is disabled."""

    @staticmethod
    @contextmanager
    def start_as_current_span(_name: str) -> Iterator[None]:
        yield


class _OTelSpanWrapper:
    """Span wrapper delegating to a real OTel tracer."""

    def __init__(self, tracer: Any) -> None:
        self._tracer = tracer

    def start_as_current_span(self, name: str) -> Any:
        return self._tracer.start_as_current_span(name)


_otel_initialized: bool = False
_tracers: dict[str, SpanWrapper] = {}


def get_tracer(name: str) -> SpanWrapper:
    """Return a SpanWrapper for the given component name.

    Args:
        name: Logical component name (e.g. ``"jobfeed.scan"``).

    Returns:
        SpanWrapper usable without importing opentelemetry.
    """
    cached = _tracers.get(name)
    if cached is not None:
        return cached
    if not _otel_initialized:
        wrapper: SpanWrapper = _NoOpSpanWrapper()
    else:
        from opentelemetry import trace  # noqa: PLC0415

        wrapper = _OTelSpanWrapper(trace.get_tracer(name))
    _tracers[name] = wrapper
    return wrapper


def init_otel(settings: ObservabilitySettings) -> None:
    """Initialize the OpenTelemetry tracer provider when enabled.

    Safe to call multiple times; subsequent calls are no-ops.

    Args:
        settings: Observability config with otel_enabled, otel_endpoint,
            and otel_service_name.
    """
    global _otel_initialized  # noqa: PLW0603
    if _otel_initialized or not settings.otel_enabled:
        return
    from opentelemetry import trace  # noqa: PLC0415
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (  # noqa: PLC0415
        OTLPSpanExporter,
    )
    from opentelemetry.sdk.resources import Resource  # noqa: PLC0415
    from opentelemetry.sdk.trace import TracerProvider  # noqa: PLC0415
    from opentelemetry.sdk.trace.export import BatchSpanProcessor  # noqa: PLC0415

    resource = Resource.create({"service.name": settings.otel_service_name})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_endpoint))
    )
    trace.set_tracer_provider(provider)
    _auto_instrument()
    _otel_initialized = True
    _tracers.clear()


def _auto_instrument() -> None:
    from opentelemetry.instrumentation.asyncpg import (  # noqa: PLC0415
        AsyncPGInstrumentor,
    )
    from opentelemetry.instrumentation.fastapi import (  # noqa: PLC0415
        FastAPIInstrumentor,
    )
    from opentelemetry.instrumentation.httpx import (  # noqa: PLC0415
        HTTPXClientInstrumentor,
    )

    HTTPXClientInstrumentor().instrument()
    AsyncPGInstrumentor().instrument()  # type: ignore[no-untyped-call]
    FastAPIInstrumentor.instrument()


_sentry_initialized: bool = False


def init_sentry(settings: ObservabilitySettings) -> None:
    """Initialize Sentry error tracking when a DSN is configured.

    Safe to call multiple times; subsequent calls are no-ops.

    Args:
        settings: Observability config with sentry_dsn and sentry_environment.
    """
    global _sentry_initialized  # noqa: PLW0603
    if _sentry_initialized or settings.sentry_dsn is None:
        return
    import sentry_sdk  # noqa: PLC0415

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.sentry_environment,
        traces_sample_rate=0,
    )
    _sentry_initialized = True


def _otel_trace_injector(
    _logger: WrappedLogger, _method: str, event_dict: EventDict
) -> EventDict:
    from opentelemetry import trace  # noqa: PLC0415

    ctx = trace.get_current_span().get_span_context()
    if ctx.trace_id:
        event_dict["trace_id"] = f"{ctx.trace_id:032x}"
        event_dict["span_id"] = f"{ctx.span_id:016x}"
    return event_dict


def configure_logging(
    log_level: str = "info",
    log_format: str = "human",
    *,
    otel_enabled: bool = False,
) -> None:
    """Configure structlog for command and service output.

    Args:
        log_level: Minimum level name such as ``"info"`` or ``"debug"``.
        log_format: ``"json"`` for JSON lines or ``"human"`` for console.
        otel_enabled: When True, inject trace/span ids into log events.

    Raises:
        ValueError: If ``log_level`` or ``log_format`` is unsupported.
    """
    level = _level_from_name(log_level)
    renderer = _renderer_for_format(log_format)
    processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    if otel_enabled:
        processors.append(_otel_trace_injector)
    processors.append(renderer)
    structlog.contextvars.clear_contextvars()
    structlog.configure(
        processors=processors,
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
    "SpanWrapper",
    "bind_run_id",
    "configure_logging",
    "get_logger",
    "get_tracer",
    "init_otel",
    "init_sentry",
]
