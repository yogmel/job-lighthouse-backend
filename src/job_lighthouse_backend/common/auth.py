"""JWT issuing and local validation, shared by both services.

Tokens are HS256-signed with the shared ``JWT_SECRET``. Each service checks
them locally; there is no call back to the Companies Service per request.
The ``sub`` claim carries the ``user_id``.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .settings import Settings

ALGORITHM = "HS256"

_bearer = HTTPBearer(auto_error=False)


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
    """FastAPI dependency for protected routes. 401 on missing/invalid/expired."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise _unauthorized()
    settings: Settings = request.app.state.settings
    try:
        return decode_token(credentials.credentials, settings.jwt_secret)
    except InvalidTokenError:
        raise _unauthorized() from None


CurrentUserId = Annotated[uuid.UUID, Depends(get_current_user_id)]
