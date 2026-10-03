"""BE-050: find a known board embedded on a careers page. No network."""

import pytest

from job_lighthouse_backend.companies.embeds import match_embedded_board

PAGE_URL = "https://acme.example/careers"


def _board(html: str) -> dict | None:
    board = match_embedded_board(html, PAGE_URL)
    return None if board is None else board.model_dump()


@pytest.mark.parametrize(
    ("html", "board", "board_id"),
    [
        (
            '<script src="https://boards.greenhouse.io/embed/job_board/js?for=acme">'
            "</script>",
            "greenhouse",
            "acme",
        ),
        (
            '<iframe src="https://job-boards.greenhouse.io/embed/job_board?for=acme">'
            "</iframe>",
            "greenhouse",
            "acme",
        ),
        (
            '<script src="https://jobs.ashbyhq.com/acme/embed?version=2"></script>',
            "ashby",
            "acme",
        ),
        ('<iframe src="//jobs.ashbyhq.com/acme"></iframe>', "ashby", "acme"),
        ('<a href="https://jobs.lever.co/acme/123">Apply</a>', "lever", "acme"),
        (
            '<a href="https://jobs.smartrecruiters.com/Acme/1">x</a>'
            '<a href="https://jobs.smartrecruiters.com/Acme/2">y</a>',
            "smartrecruiters",
            "Acme",
        ),
    ],
)
def test_finds_embedded_board(html, board, board_id):
    assert _board(f"<html><body>{html}</body></html>") == {
        "kind": "board",
        "board": board,
        "board_id": board_id,
    }


def test_widget_beats_links():
    # Linking to another company's board is common; embedding it isn't.
    html = (
        '<a href="https://jobs.lever.co/partner">Partner jobs</a>'
        '<script src="https://boards.greenhouse.io/embed/job_board/js?for=acme">'
        "</script>"
    )
    assert _board(html) == {"kind": "board", "board": "greenhouse", "board_id": "acme"}


@pytest.mark.parametrize(
    "html",
    [
        "<html><body><a href='/jobs/1'>Engineer</a></body></html>",
        # Unknown and look-alike hosts.
        '<script src="https://cdn.example/boards.greenhouse.io/x.js"></script>',
        '<a href="https://jobs.lever.co.evil.example/acme">x</a>',
        # Board host, no slug.
        '<a href="https://jobs.lever.co/">Lever</a>',
        # Two different boards: ambiguous.
        '<a href="https://jobs.lever.co/acme">x</a>'
        '<a href="https://jobs.ashbyhq.com/acme">y</a>',
        '<iframe src="https://jobs.ashbyhq.com/a"></iframe>'
        '<iframe src="https://jobs.ashbyhq.com/b"></iframe>',
        # Missing, empty and odd attributes.
        "<script></script><iframe src=''></iframe><a>x</a>",
        '<a href="http://[::1">x</a>',
    ],
)
def test_no_embedded_board(html):
    assert _board(html) is None


def test_relative_links_resolve_against_the_page():
    # A careers page served from the board's own host.
    board = match_embedded_board('<a href="/acme/123">x</a>', "https://jobs.lever.co/")
    assert board is not None
    assert board.board_id == "acme"
