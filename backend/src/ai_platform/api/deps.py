"""Request-scoped helpers shared by the routers: session lookup, CSRF and cookies."""

from contextlib import contextmanager
from typing import Annotated

from fastapi import Query

from ai_platform.db import connection
from ai_platform.services import identity as auth
from ai_platform.settings import settings

Limit = Annotated[int, Query(ge=1, le=100)]
Cursor = Annotated[str | None, Query(max_length=96)]


def session_cookie(request):
    return request.cookies.get(settings().session_cookie)


def client_address(request):
    return request.client.host if request.client else "unknown"


def current_session(conn, request):
    return auth.session(conn, session_cookie(request))


def require_csrf(request, current):
    auth.require_csrf(request.headers.get("origin"), request.headers.get("x-csrf-token"), current)


def set_session_cookie(response, issued):
    config = settings()
    response.set_cookie(
        config.session_cookie, issued.raw, max_age=issued.max_age,
        secure=config.secure_cookie, httponly=True, samesite="lax", path="/",
    )


def clear_cookie(response):
    config = settings()
    response.delete_cookie(config.session_cookie, secure=config.secure_cookie, httponly=True, samesite="lax")


@contextmanager
def authenticated(request, *, admin=False, stage="full", purpose=None):
    """Open a transaction for an authenticated request and yield ``(conn, session, user)``."""
    with connection() as conn:
        current = current_session(conn, request)
        user = auth.require_session(current, stage, purpose=purpose, admin=admin)
        if request.method not in ("GET", "HEAD"):
            require_csrf(request, current)
        auth.refresh(conn, current)
        yield conn, current, user


@contextmanager
def anonymous(request):
    """Open a transaction for a CSRF-protected request made before sign-in."""
    with connection() as conn:
        current = current_session(conn, request)
        require_csrf(request, current)
        yield conn, current


@contextmanager
def enrollment(request):
    with connection() as conn:
        current = current_session(conn, request)
        user = auth.enrollment_user(current)
        require_csrf(request, current)
        yield conn, current, user
