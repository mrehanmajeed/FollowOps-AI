"""Operator authentication.

The MVP has a single operator role, so authorization is a shared bearer token
held server-side and by the operator's browser. Deliberately narrow: the token
authenticates the *operator console*, not end users, and grants nothing to the
browser beyond calling this API. Provider credentials never leave the backend.

Multi-user deployment replaces this with Supabase Auth JWT verification and
per-account scoping; the dependency signature is the seam for that.
"""

import secrets

from fastapi import Depends, Header

from app.core.config import Settings, get_settings
from app.core.exceptions import AppError


class UnauthorizedError(AppError):
    def __init__(self, message: str = "Missing or invalid operator credentials"):
        super().__init__(message, 401)


async def require_operator(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> str:
    """Accept either `X-API-Key: <token>` or `Authorization: Bearer <token>`."""
    presented = x_api_key
    if not presented and authorization and authorization.lower().startswith("bearer "):
        presented = authorization[7:].strip()

    expected = settings.operator_api_key.get_secret_value()
    if not presented or not secrets.compare_digest(presented, expected):
        raise UnauthorizedError()
    return "operator"
