"""Deterministic ATS detection: match a careers URL against known boards.

Pure string matching, no network and no LLM. A known board's careers or API
URL resolves to a ``BoardSource``; anything else (other hosts, junk input)
is ``None``, never an error.

Only hosts the board fetchers can serve are matched. EU-hosted Greenhouse
(``*.eu.greenhouse.io``) and Lever (``jobs.eu.lever.co``) use other API
hosts, so they fall through to selector discovery for now.
"""

import re
from urllib.parse import parse_qs, urlsplit

from .sources import Board, BoardSource

# Board slugs in the wild: letters, digits, dot, dash, underscore.
_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# host -> (board, path segments before the slug). Hosts are lowercase.
_SIGNATURES: dict[str, tuple[Board, tuple[str, ...]]] = {
    "boards.greenhouse.io": ("greenhouse", ()),
    "job-boards.greenhouse.io": ("greenhouse", ()),
    "boards-api.greenhouse.io": ("greenhouse", ("v1", "boards")),
    "jobs.lever.co": ("lever", ()),
    "api.lever.co": ("lever", ("v0", "postings")),
    "jobs.ashbyhq.com": ("ashby", ()),
    "api.ashbyhq.com": ("ashby", ("posting-api", "job-board")),
    "jobs.smartrecruiters.com": ("smartrecruiters", ()),
    "careers.smartrecruiters.com": ("smartrecruiters", ()),
    "api.smartrecruiters.com": ("smartrecruiters", ("v1", "companies")),
}

# Greenhouse's embeddable board names the company in ``?for=``.
_GREENHOUSE_EMBED = ("embed", "job_board")


def normalize_url(raw: str) -> str:
    """Strip whitespace and add ``https://`` when no scheme is given."""
    url = raw.strip()
    if url and "://" not in url:
        url = f"https://{url}"
    return url


def match_board(url: str) -> BoardSource | None:
    """The board ``url`` belongs to, or ``None`` when it matches none."""
    try:
        parts = urlsplit(normalize_url(url))
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or host is None:
        return None
    host = host.removeprefix("www.")
    if host not in _SIGNATURES:
        return None
    board, prefix = _SIGNATURES[host]
    segments = [s for s in parts.path.split("/") if s]

    if board == "greenhouse" and tuple(segments[:2]) == _GREENHOUSE_EMBED:
        slugs = parse_qs(parts.query).get("for", [])
        slug = slugs[0] if slugs else None
    elif tuple(segments[: len(prefix)]) == prefix and len(segments) > len(prefix):
        slug = segments[len(prefix)]
    else:
        slug = None

    if slug is None or not _SLUG.match(slug):
        return None
    return BoardSource(kind="board", board=board, board_id=slug)
