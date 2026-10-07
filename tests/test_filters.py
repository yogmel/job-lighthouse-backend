"""BE-063: Config keyword and location filters for new openings."""

import pytest

from job_lighthouse_backend.job_runner.filters import JobFilter
from job_lighthouse_backend.job_runner.openings import Opening


def _keeps(job_filter: JobFilter, title: str = "Engineer", location: str = "") -> bool:
    return job_filter.keeps(Opening(title, "https://jobs.example/1", location))


def test_empty_filter_keeps_everything():
    assert _keeps(JobFilter(), "Anything", "Anywhere")


# --- include ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "kept"),
    [
        ("Senior Backend Engineer", True),
        ("PYTHON developer", True),
        # Substring: "engineer" is inside "Engineering".
        ("Engineering Manager", True),
        ("Designer", False),
    ],
)
def test_include_any_keyword_case_insensitive(title, kept):
    job_filter = JobFilter(include=("python", "engineer"))
    assert _keeps(job_filter, title) is kept


def test_include_matches_title_only():
    job_filter = JobFilter(include=("python",))
    opening = Opening("Designer", "https://jobs.example/1", "Python City", "Python")
    assert not job_filter.keeps(opening)


def test_blank_include_keywords_are_ignored():
    assert _keeps(JobFilter(include=(" ", "")), "Designer")


# --- exclude ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "kept"),
    [
        ("Software Intern", False),
        ("INTERN, Summer 2027", False),
        ("Internal Tools Engineer", True),
        ("Engineer", True),
    ],
)
def test_exclude_whole_word_case_insensitive(title, kept):
    assert _keeps(JobFilter(exclude=("intern",)), title) is kept


def test_exclude_phrase_and_symbols():
    job_filter = JobFilter(exclude=("staff engineer", "c++"))
    assert not _keeps(job_filter, "Staff Engineer, Platform")
    assert not _keeps(job_filter, "C++ Developer")
    assert _keeps(job_filter, "Staff Engineering Manager")


def test_exclude_wins_over_include():
    job_filter = JobFilter(include=("engineer",), exclude=("senior",))
    assert not _keeps(job_filter, "Senior Engineer")


def test_blank_exclude_keywords_are_ignored():
    assert _keeps(JobFilter(exclude=(" ",)), "Engineer")


# --- location --------------------------------------------------------------


@pytest.mark.parametrize(
    ("location", "kept"),
    [
        ("Berlin, Germany", True),
        ("BERLIN", True),
        ("Munich, Germany", False),
        # No location on the opening always passes.
        ("", True),
        ("   ", True),
        # Remote always passes.
        ("Remote - US", True),
        ("Fully remote", True),
    ],
)
def test_location_substring_case_insensitive(location, kept):
    assert _keeps(JobFilter(location="berlin"), location=location) is kept


def test_blank_location_setting_keeps_everything():
    assert _keeps(JobFilter(location="  "), location="Munich")
