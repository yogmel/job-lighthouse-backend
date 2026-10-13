"""``Companies.source``: a discriminated union on ``kind``.

Stored as one ``jsonb`` column; these models are the only validation.
Unknown fields are rejected so a source can't carry another kind's fields.
"""

import re
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


_ATTR_REF = re.compile(r"^(?P<css>.*?)@(?P<attr>[A-Za-z_][\w:.-]*)$", re.DOTALL)


def split_attr(spec: str) -> tuple[str, str | None]:
    """Split ``"css@attr"`` into ``(css, attr)``; ``(spec, None)`` if no attr.

    ``"@attr"`` gives ``("", attr)``: the attribute of the card itself. An
    ``@`` inside a CSS attribute value (``a[href*='@']``) is not a reference.
    """
    m = _ATTR_REF.match(spec)
    return (m["css"].strip(), m["attr"]) if m else (spec, None)


class Selectors(_SourceModel):
    careers_url: HttpUrl
    job: NonEmptyStr
    # title / link / location: a CSS selector inside the card, optionally
    # ending in "@attr" to read that attribute instead of the text / href.
    # "@attr" alone reads the card's own attribute.
    title: NonEmptyStr
    link: NonEmptyStr
    location: NonEmptyStr | None = None
    # Builds the job URL from the link value: "{link}" is the value read by
    # `link`, "{careers_url}" the page URL. Unset = the value is the URL.
    url_template: NonEmptyStr | None = Field(
        default=None, exclude_if=lambda v: v is None
    )

    @model_validator(mode="after")
    def _url_template_uses_link(self) -> Self:
        if self.url_template is not None and "{link}" not in self.url_template:
            raise ValueError("url_template must contain {link}")
        return self


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
