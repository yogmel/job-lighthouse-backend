"""PROJ-005: one-time import of the old script's config.yaml."""

from pathlib import Path

import psycopg
import pytest

from job_lighthouse_backend.companies.import_config import (
    WEBSITES,
    ConfigEntryError,
    import_companies,
    load_companies,
    main,
    to_company,
)

from .aio import in_session
from .conftest import needs_db

CONFIG = """\
companies:
  - {name: PostHog, tier: 1, type: ashby, ashby_id: posthog}
  - {name: Stripe, tier: 1, type: greenhouse, greenhouse_id: stripe}
  - {name: Redcare Pharmacy, tier: 2, type: smartrecruiters,
     smartrecruiters_id: Redcare-Pharmacy}
  - name: Wolt
    tier: 1
    type: dynamic
    url: https://careers.wolt.com/en/jobs
    job_selector: "a[href*='/jobs/']"
    title_selector: h3
    link_selector: a
  - name: Framer
    tier: 2
    type: static
    url: https://www.framer.com/careers/
    job_selector: "a[data-framer-name='Role']"
    title_selector: "div p"
    location_label: Berlin
  - {name: Google, tier: 1, type: google}
  - {name: Langfuse, tier: 3, type: ashby, ashby_id: clickhouse,
     title_prefix: Langfuse}
"""


@pytest.fixture
def config(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(CONFIG)
    return path


def test_maps_each_kind(config: Path):
    companies, skipped = load_companies(config)
    by_name = {c.name: c for c in companies}

    assert by_name["PostHog"].source == {
        "kind": "board",
        "board": "ashby",
        "board_id": "posthog",
    }
    assert by_name["Redcare Pharmacy"].source["board"] == "smartrecruiters"
    assert by_name["Redcare Pharmacy"].source["board_id"] == "Redcare-Pharmacy"
    assert by_name["Wolt"].source["strategy"] == "dynamic"
    assert by_name["Wolt"].source["selectors"]["job"] == "a[href*='/jobs/']"
    # No link_selector in the old config: the old scrapers default to "a".
    assert by_name["Framer"].source["strategy"] == "static"
    assert by_name["Framer"].source["selectors"]["link"] == "a"
    assert by_name["Google"].source == {"kind": "custom", "handler": "google"}
    assert by_name["Stripe"].tier == 1
    assert [s.split(":")[0] for s in skipped] == ["Langfuse"]


def test_custom_companies_are_paused_and_others_active(config: Path):
    companies, _ = load_companies(config)
    active = {c.name: c.active for c in companies}
    assert active["Google"] is False
    assert all(v for n, v in active.items() if n != "Google")


def test_unknown_type_and_missing_website_fail():
    with pytest.raises(ConfigEntryError, match="unknown type"):
        to_company({"name": "PostHog", "type": "workable"})
    with pytest.raises(ConfigEntryError, match="website_url"):
        to_company({"name": "Nobody", "type": "google"})


def test_websites_are_valid_urls():
    assert all(u.startswith("https://") for u in WEBSITES.values())


def test_dry_run_needs_no_database(
    config: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert main([str(config), "--email", "x@example.com", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "6 companies mapped, 1 skipped" in out
    assert "Google [custom, paused]" in out


@needs_db
def test_import_is_idempotent_and_scoped_to_user(
    config: Path, db: psycopg.Connection, make_user
):
    me, other = make_user(), make_user()
    companies, _ = load_companies(config)

    async def run(session, user_id):
        return await import_companies(session, user_id, companies)

    created, present = in_session(lambda s: run(s, me["id"]))
    assert len(created) == 6 and present == []

    created, present = in_session(lambda s: run(s, me["id"]))
    assert created == [] and len(present) == 6

    # Two users tracking the same company get two rows.
    created, _ = in_session(lambda s: run(s, other["id"]))
    assert len(created) == 6

    row = db.execute(
        "SELECT active, source FROM companies WHERE user_id = %s AND name = 'Google'",
        (me["id"],),
    ).fetchone()
    assert row == (False, {"kind": "custom", "handler": "google"})
    count = db.execute(
        "SELECT count(*) FROM companies WHERE user_id = %s", (me["id"],)
    ).fetchone()
    assert count == (6,)
