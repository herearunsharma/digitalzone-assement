"""RFC 9457 problem+json error bodies.

FastAPI returns `{"detail": ...}` for HTTPException but `{"detail": [...]}` for
validation failures. Normalising both to one shape means clients write a single
error handler rather than branching on status code.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_JSON = "application/problem+json"


class ProblemDetail(Exception):
    """Raise this instead of HTTPException to control the whole problem body."""

    def __init__(
        self,
        status_code: int,
        title: str,
        detail: str,
        type_: str = "about:blank",
        **extra: Any,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.title = title
        self.detail = detail
        self.type_ = type_
        self.extra = extra


def _problem(
    request: Request, status_code: int, title: str, detail: str, **extra: Any
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": "about:blank",
        "title": title,
        "status": status_code,
        "detail": detail,
        "instance": request.url.path,
    }
    body.update(extra)
    return JSONResponse(status_code=status_code, content=body, media_type=PROBLEM_JSON)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ProblemDetail)
    async def _handle_problem(request: Request, exc: ProblemDetail) -> JSONResponse:
        return _problem(request, exc.status_code, exc.title, exc.detail, **exc.extra)

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _problem(
            request,
            exc.status_code,
            title=str(exc.detail) if exc.status_code != 404 else "Not Found",
            detail=str(exc.detail),
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Field errors kept structured under `errors` so a client can map them
        # back to inputs; `detail` stays readable prose.
        errors = [
            {
                "field": ".".join(str(part) for part in err.get("loc", [])[1:]),
                "message": err.get("msg", ""),
                "type": err.get("type", ""),
            }
            for err in exc.errors()
        ]
        summary = "; ".join(
            f"{e['field']}: {e['message']}" if e["field"] else e["message"]
            for e in errors
        )
        return _problem(
            request,
            422,  # see app/api/metrics.py for why this is a literal
            title="Invalid request parameters",
            detail=summary or "Request parameters failed validation.",
            errors=errors,
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        # Generic by design; the trace stays in the server log.
        return _problem(
            request,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            title="Internal Server Error",
            detail="An unexpected error occurred while handling the request.",
        )
