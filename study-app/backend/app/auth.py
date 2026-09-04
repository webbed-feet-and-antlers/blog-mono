"""Request identity — Clerk session JWTs → the current user id.

The frontend (ClerkProvider) attaches the Clerk session JWT to every API
call: as an ``Authorization: Bearer`` header (fetch/XHR/SSE), or as a
``?token=`` query parameter where headers can't travel — ``<img src>``/
file downloads and ``navigator.sendBeacon`` telemetry.

The verified Clerk user id (the JWT ``sub`` claim) is the request's owner.
It is stored in a ContextVar so the whole call tree — routes, agent
memory, event handlers, background jobs — resolves the current user
without threading an explicit parameter through every signature. Code
running outside a request (tests, evals, the proactive loop's per-user
scopes) sets it explicitly or sees the ambient default "" — the same
single implicit user those callers have always used.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Mapping

from fastapi import HTTPException, Request

from .config import settings

logger = logging.getLogger(__name__)

# The current request's owner (a Clerk user id), or None outside requests.
current_user_id: ContextVar[str | None] = ContextVar("current_user_id", default=None)


def user_ref_id() -> str:
    """The owner key for user-scope memory blobs: the current user id, or
    "" for ambient contexts (tests/evals/CLI) with no request identity."""
    return current_user_id.get() or ""


@contextmanager
def user_scope(user_id: str) -> Iterator[None]:
    """Run a block as a specific user (background jobs iterating users)."""
    token = current_user_id.set(user_id)
    try:
        yield
    finally:
        current_user_id.reset(token)


# --- Clerk verification -----------------------------------------------------


class _HeaderInjected:
    """Requestish adapter: a query-param token presented as a Bearer header.

    The Clerk SDK reads the token via a case-sensitive headers.get
    ("Authorization"); Starlette's Headers lowercase everything, so the
    merged mapping must use the exact key the SDK looks up.
    """

    def __init__(self, request: Request, bearer: str) -> None:
        self._request = request
        self._bearer = bearer

    @property
    def headers(self) -> Mapping[str, str]:
        merged = {
            k: v
            for k, v in self._request.headers.items()
            if k.lower() != "authorization"
        }
        merged["Authorization"] = self._bearer
        return merged


_clerk: Any = None


def _get_clerk() -> Any:
    global _clerk
    if _clerk is None:
        if not settings.clerk_secret_key:
            raise HTTPException(
                status_code=503,
                detail=(
                    "CLERK_SECRET_KEY is not set — create a Clerk app "
                    "(dashboard.clerk.com) and add the key to backend/.env. "
                    "See study-app/README.md."
                ),
            )
        from clerk_backend_api import Clerk

        _clerk = Clerk(bearer_auth=settings.clerk_secret_key)
    return _clerk


async def get_current_user(request: Request) -> str:
    """FastAPI dependency: verify the Clerk session JWT, return the user id.

    Raises 401 when the request carries no valid session, and 503 when the
    backend has no Clerk secret configured. Mounted on every router; tests
    override it via app.dependency_overrides.
    """
    from clerk_backend_api.security.types import AuthenticateRequestOptions

    clerk = _get_clerk()

    token = request.query_params.get("token")
    req: Any = request
    if token and "authorization" not in request.headers:
        req = _HeaderInjected(request, f"Bearer {token}")

    state = await clerk.authenticate_request_async(
        req,
        AuthenticateRequestOptions(
            secret_key=settings.clerk_secret_key,
            authorized_parties=settings.clerk_authorized_parties,
        ),
    )
    if not state.is_signed_in:
        raise HTTPException(
            status_code=401,
            detail=f"Not authenticated: {state.message or state.reason}",
        )

    payload = state.payload or {}
    user_id = payload.get("sub") if isinstance(payload, Mapping) else None
    if not user_id:
        raise HTTPException(status_code=401, detail="Session token has no user id")

    current_user_id.set(user_id)
    return user_id
