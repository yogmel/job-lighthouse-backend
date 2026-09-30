"""Request/response shapes shared by the auth and account routes."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

# Upper bound keeps hashing cost bounded for huge inputs.
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 256

# For passwords being set (signup, change). Login takes any string.
NewPassword = Annotated[
    str, Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)
]


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
