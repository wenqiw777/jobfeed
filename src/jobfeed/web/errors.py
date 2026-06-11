"""Per-request IDs and JSON error responses for the web API.

Every request gets a uuid4-hex ``request_id`` bound into structlog context
and echoed in error bodies. All error responses use one JSON shape —
``{"error": {"code", "message", "request_id"}}`` — with no HTML pages and
no tracebacks.
"""

from __future__ import annotations

import traceback
import uuid

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import RequestResponseEndpoint

from jobfeed.observability import get_logger

_HTTP_VALIDATION_ERROR = 422
_HTTP_INTERNAL_ERROR = 500
_CODES_BY_STATUS = {404: "not_found", 422: "validation_error"}


def install_error_handling(app: FastAPI) -> None:
    """Attach the request-id middleware and the JSON error handlers.

    Args:
        app: FastAPI app to configure.
    """
    app.middleware("http")(_assign_request_id)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)


async def _assign_request_id(
    request: Request, call_next: RequestResponseEndpoint
) -> Response:
    """Assign a request id, bind it to structlog context, and log the request."""
    request_id = uuid.uuid4().hex
    request.state.request_id = request_id
    structlog.contextvars.bind_contextvars(request_id=request_id)
    try:
        return await _dispatch(request, call_next, request_id)
    finally:
        structlog.contextvars.unbind_contextvars("request_id")


async def _dispatch(
    request: Request, call_next: RequestResponseEndpoint, request_id: str
) -> Response:
    """Run the handler chain, converting unexpected errors to JSON 500s."""
    try:
        response: Response = await call_next(request)
    except Exception as exc:
        get_logger().error(
            "http_unhandled_error",
            method=request.method,
            path=request.url.path,
            error=str(exc) or type(exc).__name__,
            traceback=traceback.format_exc(),
            request_id=request_id,
        )
        response = _error_response(
            _HTTP_INTERNAL_ERROR, "internal_error", "internal server error", request_id
        )
    get_logger().info(
        "http_request",
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        request_id=request_id,
    )
    return response


async def _handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
    """Convert Starlette HTTP exceptions (404 et al.) into the error shape.

    Args:
        request: Request carrying the middleware-assigned request id.
        exc: Always a StarletteHTTPException per handler registration.

    Returns:
        JSON error response with the shared shape.

    Raises:
        Exception: Re-raises any non-HTTP exception (defensive; not expected).
    """
    if not isinstance(exc, StarletteHTTPException):
        raise exc
    code = _CODES_BY_STATUS.get(exc.status_code, "http_error")
    return _error_response(
        exc.status_code, code, str(exc.detail), _request_id_of(request)
    )


async def _handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    """Convert request validation failures into a JSON 422 error.

    The validation details are logged server-side only; the response body
    stays generic.

    Args:
        request: Request carrying the middleware-assigned request id.
        exc: Always a RequestValidationError per handler registration.

    Returns:
        JSON 422 error response with the shared shape.
    """
    request_id = _request_id_of(request)
    if isinstance(exc, RequestValidationError):
        get_logger().warning(
            "http_validation_error",
            method=request.method,
            path=request.url.path,
            errors=_compact_validation_errors(exc),
            request_id=request_id,
        )
    return _error_response(
        _HTTP_VALIDATION_ERROR,
        "validation_error",
        "request validation failed",
        request_id,
    )


def _compact_validation_errors(exc: RequestValidationError) -> list[str]:
    """Render validation errors as compact ``location: message`` strings."""
    return [
        f"{'.'.join(str(part) for part in error.get('loc', ()))}: "
        f"{error.get('msg', '')}"
        for error in exc.errors()
    ]


def _request_id_of(request: Request) -> str:
    """Return the middleware-assigned request id, or "" before middleware."""
    return str(getattr(request.state, "request_id", ""))


def _error_response(
    status_code: int, code: str, message: str, request_id: str
) -> JSONResponse:
    """Build the shared JSON error body."""
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "request_id": request_id}},
    )


__all__ = ["install_error_handling"]
