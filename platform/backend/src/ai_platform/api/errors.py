from uuid import uuid4

import psycopg
from fastapi import Request
from fastapi.exceptions import RequestValidationError
from redis.exceptions import RedisError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from ai_platform.security import KeyUnavailable
from ai_platform.services.errors import ServiceError

CODES = {401: "UNAUTHENTICATED", 403: "FORBIDDEN", 404: "NOT_FOUND",
         409: "CONFLICT", 422: "INVALID_INPUT", 429: "RATE_LIMITED",
         503: "DEPENDENCY_UNAVAILABLE"}
PASSTHROUGH = ("IDEMPOTENCY_CONFLICT", "CONVERSATION_BUSY", "EVENT_REPLAY_EXPIRED", "DELETION_ARCHIVE_PENDING")


def install_errors(app):
    @app.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        if request.url.path.startswith("/api/v1/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def response(request, status, code, message):
        return JSONResponse(status_code=status, content={"error": {
            "code": code, "message": message, "request_id": request.state.request_id,
            "retryable": status in {429, 503},
        }})

    def status_error(request, status, detail):
        code = detail if detail in PASSTHROUGH else CODES.get(status, "HTTP_ERROR")
        return response(request, status, code, detail)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return status_error(request, exc.status_code, exc.detail)

    @app.exception_handler(ServiceError)
    async def service_error(request, exc):
        return status_error(request, exc.status, exc.detail)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return response(request, 422, "INVALID_INPUT", "Invalid request")

    @app.exception_handler(psycopg.OperationalError)
    @app.exception_handler(RedisError)
    @app.exception_handler(KeyUnavailable)
    async def dependency_error(request, exc):
        return response(request, 503, "DEPENDENCY_UNAVAILABLE", "Dependency unavailable")
