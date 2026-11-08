"""Uniform, non-reflective public errors and request correlation identifiers."""

import uuid

from fastapi import Request
from fastapi.responses import JSONResponse

from app.schemas import ErrorResponse

ERROR_CODES = {
    400: "invalid_request",
    401: "authentication_required",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "request_too_large",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    503: "service_unavailable",
}
ERROR_RESPONSES = {
    code: {"model": ErrorResponse, "description": name} for code, name in ERROR_CODES.items()
}


def correlation_id(request: Request) -> str:
    if not getattr(request.state, "correlation_id", None):
        request.state.correlation_id = str(uuid.uuid4())
    return request.state.correlation_id


def error_response(request, status, message, *, fields=None, headers=None):
    identifier = correlation_id(request)
    return JSONResponse(
        {
            "code": ERROR_CODES.get(status, "request_failed"),
            "message": message,
            "detail": message,
            "field_errors": fields or [],
            "correlation_id": identifier,
        },
        status_code=status,
        headers={
            **(headers or {}),
            "X-Correlation-ID": identifier,
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
