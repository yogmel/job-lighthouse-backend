"""BE-033: deterministic ATS signature matcher. No network."""

import pytest

from job_lighthouse_backend.companies.ats import match_board, normalize_url


@pytest.mark.parametrize(
    ("url", "board", "board_id"),
    [
        # Greenhouse
        ("https://boards.greenhouse.io/stripe", "greenhouse", "stripe"),
        ("https://boards.greenhouse.io/stripe/jobs/123456", "greenhouse", "stripe"),
        ("https://job-boards.greenhouse.io/figma?gh_src=x", "greenhouse", "figma"),
        (
            "https://boards.greenhouse.io/embed/job_board?for=airbnb&b=x",
            "greenhouse",
            "airbnb",
        ),
        (
            "https://boards-api.greenhouse.io/v1/boards/stripe/jobs",
            "greenhouse",
            "stripe",
        ),
        # Lever
        ("https://jobs.lever.co/netflix", "lever", "netflix"),
        ("https://jobs.lever.co/netflix/abc-123/apply", "lever", "netflix"),
        ("https://api.lever.co/v0/postings/netflix?mode=json", "lever", "netflix"),
        # Ashby
        ("https://jobs.ashbyhq.com/openai", "ashby", "openai"),
        ("https://jobs.ashbyhq.com/openai/abc#apply", "ashby", "openai"),
        (
            "https://api.ashbyhq.com/posting-api/job-board/openai",
            "ashby",
            "openai",
        ),
        # SmartRecruiters
        ("https://jobs.smartrecruiters.com/Visa", "smartrecruiters", "Visa"),
        (
            "https://careers.smartrecruiters.com/BoschGroup/abc",
            "smartrecruiters",
            "BoschGroup",
        ),
        (
            "https://api.smartrecruiters.com/v1/companies/Visa/postings",
            "smartrecruiters",
            "Visa",
        ),
        # Host case, www., no scheme, http, whitespace, dotted/dashed slugs.
        ("HTTPS://Jobs.Lever.CO/Kraken", "lever", "Kraken"),
        ("https://www.boards.greenhouse.io/stripe", "greenhouse", "stripe"),
        ("jobs.ashbyhq.com/kraken-technologies", "ashby", "kraken-technologies"),
        ("http://jobs.lever.co/a.b_c", "lever", "a.b_c"),
        ("  https://jobs.lever.co/netflix  ", "lever", "netflix"),
    ],
)
def test_known_board(url, board, board_id):
    source = match_board(url)
    assert source is not None
    assert source.model_dump() == {
        "kind": "board",
        "board": board,
        "board_id": board_id,
    }


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "not a url",
        "https://stripe.com/jobs",
        "https://greenhouse.io/stripe",
        "https://boards.greenhouse.io.evil.example/stripe",
        "https://evil.example/boards.greenhouse.io/stripe",
        # Board host, but no company slug.
        "https://jobs.lever.co/",
        "https://boards.greenhouse.io/embed/job_board",
        "https://boards.greenhouse.io/embed/job_board?for=",
        "https://api.lever.co/v0/",
        "https://api.ashbyhq.com/other/openai",
        # EU hosts use other API hosts: not served by the board fetchers.
        "https://jobs.eu.lever.co/acme",
        "https://job-boards.eu.greenhouse.io/acme",
        # Slug with characters no board uses.
        "https://jobs.lever.co/%2e%2e",
        "https://jobs.lever.co/-dash-first",
        # Other schemes.
        "ftp://jobs.lever.co/netflix",
        "javascript:alert(1)",
        "https://[::1/x",
    ],
)
def test_no_match(url):
    assert match_board(url) is None


def test_normalize_url():
    assert normalize_url("  jobs.lever.co/x ") == "https://jobs.lever.co/x"
    assert normalize_url("http://a.example") == "http://a.example"
    assert normalize_url("") == ""
