"""JWT issuing and local validation, shared by both services.

Tokens are HS256-signed with the shared ``JWT_SECRET``. Each service checks
them locally; there is no call back to the Companies Service per request.
The ``sub`` claim carries the ``user_id``.

Protected routes also check that the user still exists (one primary-key
lookup on the shared DB), so a deleted account's tokens stop working at once
instead of at expiry (BE-044).
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import column, select, table
from sqlalchemy.ext.asyncio import AsyncEngine

from .settings import Settings

ALGORITHM = "HS256"

_bearer = HTTPBearer(auto_error=False)

# Owned by the Companies Service. Both services only check a row exists.
_users = table("users", column("id"))


class InvalidTokenError(Exception):
    pass


def issue_token(user_id: uuid.UUID, settings: Settings) -> str:
    now = datetime.now(UTC)
    claims = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(seconds=settings.jwt_ttl_seconds),
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm=ALGORITHM)


def decode_token(token: str, secret: str) -> uuid.UUID:
    """Return the token's ``user_id``. Raises ``InvalidTokenError`` otherwise."""
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=[ALGORITHM],
            options={"require": ["sub", "exp", "iat"]},
        )
        return uuid.UUID(claims["sub"])
    except (jwt.PyJWTError, ValueError, TypeError) as exc:
        raise InvalidTokenError from exc


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user_id(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> uuid.UUID:
    """FastAPI dependency for protected routes.

    401 on a missing, invalid or expired token, or one whose user is gone.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized()
    settings: Settings = request.app.state.settings
    try:
        user_id = decode_token(credentials.credentials, settings.jwt_secret)
    except InvalidTokenError:
        raise _unauthorized() from None
    if not await user_exists(request.app.state.engine, user_id):
        raise _unauthorized()
    return user_id


async def user_exists(engine: AsyncEngine, user_id: uuid.UUID) -> bool:
    # Its own short connection: the route may not use the DB at all, or may
    # hand its session's connection back early (e.g. before a slow fetch).
    async with engine.connect() as conn:
        found = await conn.scalar(select(_users.c.id).where(_users.c.id == user_id))
    return found is not None


CurrentUserId = Annotated[uuid.UUID, Depends(get_current_user_id)]
