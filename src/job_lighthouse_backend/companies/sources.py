"""``Companies.source``: a discriminated union on ``kind``.

Stored as one ``jsonb`` column; these models are the only validation.
Unknown fields are rejected so a source can't carry another kind's fields.
"""

from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    StringConstraints,
    model_validator,
)

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
    # "eu": a Lever board on jobs.eu.lever.co, fetched from api.eu.lever.co.
    # Unset = the default host; left out of dumps so US boards keep their
    # stored shape. EU Greenhouse boards share the default API.
    region: Literal["eu"] | None = Field(default=None, exclude_if=lambda v: v is None)

    @model_validator(mode="after")
    def _region_only_for_lever(self) -> Self:
        if self.region is not None and self.board != "lever":
            raise ValueError(f"{self.board} has no region")
        return self


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

# What detection can find. It never yields `custom`: a handler is code a
# developer writes later.
DetectedSource = Annotated[BoardSource | ScraperSource, Field(discriminator="kind")]
