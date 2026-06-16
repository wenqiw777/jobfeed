"""Tests for OTel no-op wrappers and structlog bridge in observability."""

from __future__ import annotations

from jobfeed.observability import (
    SpanWrapper,
    _NoOpSpanWrapper,
    _otel_trace_injector,
    get_tracer,
)


def test_noop_span_wrapper_is_usable_context_manager() -> None:
    """The no-op wrapper's context manager should yield without error."""
    wrapper = _NoOpSpanWrapper()
    with wrapper.start_as_current_span("test_span"):
        pass  # should not raise


def test_get_tracer_returns_noop_when_otel_disabled() -> None:
    """get_tracer should return a working wrapper even without OTel init."""
    tracer = get_tracer("test.component")
    with tracer.start_as_current_span("some_span"):
        pass  # should not raise


def test_span_wrapper_protocol_is_importable() -> None:
    """SpanWrapper protocol should be importable from observability."""
    assert hasattr(SpanWrapper, "start_as_current_span")


def test_noop_wrapper_satisfies_span_wrapper_protocol() -> None:
    """_NoOpSpanWrapper should structurally satisfy the SpanWrapper protocol."""
    wrapper: SpanWrapper = _NoOpSpanWrapper()
    with wrapper.start_as_current_span("check"):
        pass


def test_structlog_bridge_handles_missing_span() -> None:
    """The trace injector should pass through when no OTel span is active."""
    event_dict: dict[str, object] = {"event": "hello"}
    result = _otel_trace_injector(None, "info", event_dict)
    # Without an active span, trace_id should not be injected (trace_id == 0
    # is the invalid/no-span sentinel in OTel).
    assert result is event_dict
    assert "trace_id" not in result
