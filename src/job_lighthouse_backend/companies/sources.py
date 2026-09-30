"""``Companies.source``: a discriminated union on ``kind``.

Stored as one ``jsonb`` column; these models are the only validation.
Unknown fields are rejected so a source can't carry another kind's fields.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints

Board = Literal["lever", "greenhouse", "ashby", "smartrecruiters"]

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class _SourceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Selectors(_SourceModel):
    careers_url: HttpUrl
    job: NonEmptyStr
    title: NonEmptyStr
    link: NonEmptyStr
    location: NonEmptyStr | None = None


class BoardSource(_SourceModel):
    kind: Literal["board"]
    board: Board
    # The company's slug on that board; often differs from its name.
    board_id: NonEmptyStr


class ScraperSource(_SourceModel):
    kind: Literal["scraper"]
    strategy: Literal["static", "dynamic"]
    selectors: Selectors


class CustomSource(_SourceModel):
    kind: Literal["custom"]
    # Names a per-company fetch function shipped in code.
    handler: NonEmptyStr


Source = Annotated[
    BoardSource | ScraperSource | CustomSource, Field(discriminator="kind")
]

# What a user may set by hand. `custom` needs a handler shipped in code
# first, so it can't be created from the API yet (v0.10).
ManualSource = Annotated[BoardSource | ScraperSource, Field(discriminator="kind")]
