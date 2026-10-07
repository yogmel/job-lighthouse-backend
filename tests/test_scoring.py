"""BE-028: match scoring (pipeline step 6)."""

import asyncio
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from job_lighthouse_backend.job_runner.models import Run
from job_lighthouse_backend.job_runner.openings import Opening
from job_lighthouse_backend.job_runner.pipeline import run_pipeline
from job_lighthouse_backend.job_runner.scoring import (
    MAX_DESCRIPTION_CHARS,
    MAX_REASON_CHARS,
    Match,
    Posting,
    ScoringError,
    _MatchOut,
    openai_scorer,
)

from .aio import in_session
from .conftest import BOARD_SOURCE, needs_db

PROFILE = "# Profile\nBackend engineer."


class FakeScorer:
    """Scores by title: ``"fail"`` in the title raises."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Posting]] = []

    async def __call__(self, profile: str, posting: Posting) -> Match:
        self.calls.append((profile, posting))
        if "fail" in posting.title:
            raise RuntimeError("LLM down")
        return Match(score=len(posting.title), description=f"why {posting.title}")


def _board(board_id: str) -> dict:
    return {**BOARD_SOURCE, "board_id": board_id}


def _url() -> str:
    return f"https://jobs.example/{uuid.uuid4().hex}"


def _set_profile(db, user_id, profile: str, version: int) -> None:
    db.execute(
        "INSERT INTO config (user_id, location, cron, profile, profile_version)"
        " VALUES (%s, '', '0 7 * * *', %s, %s)",
        (user_id, profile, version),
    )


def _run(db, user_id, fetch, scorer) -> int:
    row = db.execute(
        "INSERT INTO runs (user_id, status, trigger) VALUES (%s, 'running', 'manual')"
        " RETURNING id",
        (user_id,),
    ).fetchone()
    run = Run(id=row[0], user_id=user_id, status="running", trigger="manual")
    return in_session(lambda s: run_pipeline(s, run, fetch, scorer))


def _scores(db, user_id) -> dict[str, tuple]:
    rows = db.execute(
        "SELECT title, match_score, match_description, profile_version FROM jobs"
        " WHERE user_id = %s",
        (user_id,),
    ).fetchall()
    return {r[0]: r[1:] for r in rows}


# --- pipeline --------------------------------------------------------------


@needs_db
def test_new_jobs_scored_with_config_version(db, make_user, make_company):
    user = make_user()
    make_company(user["id"], source=_board("a"))
    make_company(user["id"], source=_board("b"))
    _set_profile(db, user["id"], PROFILE, 7)
    fetch = {"a": [Opening("Eng", _url())], "b": [Opening("Lead", _url())]}
    scorer = FakeScorer()

    assert _run(db, user["id"], lambda s: fetch[s.board_id], scorer) == 2
    assert _scores(db, user["id"]) == {
        "Eng": (3.0, "why Eng", 7),
        "Lead": (4.0, "why Lead", 7),
    }
    assert {c[0] for c in scorer.calls} == {PROFILE}


@needs_db
def test_posting_sent_to_scorer(db, make_user, make_company):
    user = make_user()
    make_company(user["id"], name="Acme", source=_board("a"))
    _set_profile(db, user["id"], PROFILE, 1)
    opening = Opening("Eng", _url(), location="Berlin", description="Python")
    scorer = FakeScorer()

    _run(db, user["id"], lambda s: [opening], scorer)
    assert scorer.calls == [(PROFILE, Posting("Eng", "Acme", "Berlin", "Python"))]


@needs_db
def test_existing_jobs_are_not_rescored(db, make_user, make_company):
    user = make_user()
    make_company(user["id"], source=_board("a"))
    _set_profile(db, user["id"], PROFILE, 1)
    old, new = Opening("Old", _url()), Opening("New", _url())
    scorer = FakeScorer()
    _run(db, user["id"], lambda s: [old], scorer)
    db.execute(
        "UPDATE config SET profile = 'changed', profile_version = 2 WHERE user_id = %s",
        (user["id"],),
    )

    _run(db, user["id"], lambda s: [old, new], scorer)
    assert [c[1].title for c in scorer.calls] == ["Old", "New"]
    assert _scores(db, user["id"]) == {
        "Old": (3.0, "why Old", 1),
        "New": (3.0, "why New", 2),
    }


@needs_db
def test_profile_read_once_per_run(db, make_user, make_company):
    user = make_user()
    make_company(user["id"], source=_board("a"))
    make_company(user["id"], source=_board("b"))
    _set_profile(db, user["id"], PROFILE, 1)
    fetch = {"a": [Opening("Eng", _url())], "b": [Opening("Lead", _url())]}

    class EditsMidRun(FakeScorer):
        async def __call__(self, profile: str, posting: Posting) -> Match:
            # The user saves a new profile while the run is going.
            db.execute(
                "UPDATE config SET profile = 'new', profile_version = 2"
                " WHERE user_id = %s",
                (user["id"],),
            )
            return await super().__call__(profile, posting)

    scorer = EditsMidRun()
    _run(db, user["id"], lambda s: fetch[s.board_id], scorer)
    assert {c[0] for c in scorer.calls} == {PROFILE}
    assert {v[2] for v in _scores(db, user["id"]).values()} == {1}


@needs_db
def test_config_filters_drop_new_jobs_before_scoring(db, make_user, make_company):
    user = make_user()
    make_company(user["id"], source=_board("a"))
    _set_profile(db, user["id"], PROFILE, 1)
    db.execute(
        "UPDATE config SET keywords_include = '{engineer}',"
        " keywords_exclude = '{intern}', location = 'Berlin' WHERE user_id = %s",
        (user["id"],),
    )
    fetch = [
        Opening("Engineer", _url(), location="Remote"),
        Opening("Engineer Intern", _url()),
        Opening("Designer", _url()),
        Opening("Engineer, Munich", _url(), location="Munich"),
    ]
    scorer = FakeScorer()

    assert _run(db, user["id"], lambda s: fetch, scorer) == 1
    assert [c[1].title for c in scorer.calls] == ["Engineer"]
    assert set(_scores(db, user["id"])) == {"Engineer"}


@needs_db
def test_failed_call_leaves_job_unscored(db, make_user, make_company):
    user = make_user()
    company = make_company(user["id"], source=_board("a"))
    _set_profile(db, user["id"], PROFILE, 1)
    fetch = [Opening("Eng", _url()), Opening("fail", _url())]

    assert _run(db, user["id"], lambda s: fetch, FakeScorer()) == 2
    assert _scores(db, user["id"]) == {
        "Eng": (3.0, "why Eng", 1),
        "fail": (None, None, None),
    }
    # The company still succeeded.
    row = db.execute(
        "SELECT status FROM run_company_results WHERE company_id = %s", (company,)
    ).fetchone()
    assert row == ("ok",)


@needs_db
def test_failed_job_is_not_retried_next_run(db, make_user, make_company):
    user = make_user()
    make_company(user["id"], source=_board("a"))
    _set_profile(db, user["id"], PROFILE, 1)
    fetch = [Opening("fail", _url())]
    scorer = FakeScorer()

    _run(db, user["id"], lambda s: fetch, scorer)
    _run(db, user["id"], lambda s: fetch, scorer)
    assert len(scorer.calls) == 1


@needs_db
@pytest.mark.parametrize("profile", [None, "", "  \n "])
def test_no_profile_skips_scoring(db, make_user, make_company, profile):
    user = make_user()
    make_company(user["id"], source=_board("a"))
    if profile is not None:
        _set_profile(db, user["id"], profile, 3)
    scorer = FakeScorer()

    assert _run(db, user["id"], lambda s: [Opening("Eng", _url())], scorer) == 1
    assert scorer.calls == []
    assert _scores(db, user["id"]) == {"Eng": (None, None, None)}


@needs_db
def test_no_scorer_stores_unscored(db, make_user, make_company):
    user = make_user()
    make_company(user["id"], source=_board("a"))
    _set_profile(db, user["id"], PROFILE, 1)

    assert _run(db, user["id"], lambda s: [Opening("Eng", _url())], None) == 1
    assert _scores(db, user["id"]) == {"Eng": (None, None, None)}


@needs_db
def test_only_own_profile_is_used(db, make_user, make_company):
    me, other = make_user(), make_user()
    make_company(me["id"], source=_board("a"))
    _set_profile(db, other["id"], "theirs", 5)
    scorer = FakeScorer()

    _run(db, me["id"], lambda s: [Opening("Eng", _url())], scorer)
    assert scorer.calls == []


# --- OpenAI scorer ---------------------------------------------------------


class FakeCompletions:
    def __init__(self, parsed: _MatchOut | None) -> None:
        self.parsed = parsed
        self.kwargs: dict[str, Any] = {}

    async def parse(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        message = SimpleNamespace(parsed=self.parsed)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _client(parsed: _MatchOut | None) -> tuple[Any, FakeCompletions]:
    completions = FakeCompletions(parsed)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


POSTING = Posting("Eng", "Acme", "", "x" * (MAX_DESCRIPTION_CHARS + 500))


def test_openai_scorer_request_and_answer():
    client, completions = _client(_MatchOut(score=82, reason="  Good fit.  "))
    match = asyncio.run(openai_scorer(client, "some-model")(PROFILE, POSTING))

    assert match == Match(score=82, description="Good fit.")
    assert completions.kwargs["model"] == "some-model"
    assert completions.kwargs["response_format"] is _MatchOut
    system, user = completions.kwargs["messages"]
    assert system["role"] == "system"
    assert "not instructions" in system["content"]
    assert PROFILE in user["content"]
    assert "Title: Eng" in user["content"]
    assert "Location: not given" in user["content"]
    # Long descriptions are cut.
    assert "x" * MAX_DESCRIPTION_CHARS in user["content"]
    assert "x" * (MAX_DESCRIPTION_CHARS + 1) not in user["content"]


@pytest.mark.parametrize(("raw", "expected"), [(-5, 0), (0, 0), (100, 100), (250, 100)])
def test_openai_scorer_clamps_score(raw, expected):
    client, _ = _client(_MatchOut(score=raw, reason="r"))
    match = asyncio.run(openai_scorer(client, "m")(PROFILE, POSTING))
    assert match.score == expected


def test_openai_scorer_truncates_reason():
    client, _ = _client(_MatchOut(score=50, reason="y" * (MAX_REASON_CHARS + 10)))
    match = asyncio.run(openai_scorer(client, "m")(PROFILE, POSTING))
    assert match.description == "y" * MAX_REASON_CHARS


def test_openai_scorer_without_answer_raises():
    client, _ = _client(None)
    with pytest.raises(ScoringError):
        asyncio.run(openai_scorer(client, "m")(PROFILE, POSTING))
