"""One-time import of the old script's ``config.yaml`` companies (PROJ-005).

Run once at cutover, for one user::

    uv run python -m job_lighthouse_backend.companies.import_config \\
        config.yaml --email you@example.com [--dry-run]

Safe to re-run: a company whose name the user already has is left alone.
"""

import argparse
import asyncio
import os
import sys
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from job_lighthouse_backend.common.db import create_engine, create_sessionmaker

from .models import Company
from .sources import Source
from .users import get_user_by_email

BOARD_TYPES = ("greenhouse", "lever", "ashby", "smartrecruiters")

# Old `type` -> `custom` handler name, for sites with no board API or
# selectors (bespoke `scrape_*` functions in the old script's ats_api.py).
# Nothing registers these in `HANDLERS` yet, so these companies are imported
# paused and runs report them `skipped` until a handler ships.
CUSTOM_HANDLERS = {
    "google": "google",
    "shopify": "shopify",
    "deel": "deel",
    "ebay": "ebay",
    "kleinanzeigen": "kleinanzeigen",
    "bolt": "bolt",
    "betterstack": "betterstack",
    "personio": "personio",
    "teamtailor": "eterno-group",
}

# `config.yaml` has no marketing URL, but `Companies.website_url` is required.
WEBSITES = {
    "PostHog": "https://posthog.com",
    "Vercel": "https://vercel.com",
    "Databricks": "https://www.databricks.com",
    "Google": "https://about.google",
    "Linear": "https://linear.app",
    "HubSpot": "https://www.hubspot.com",
    "Wolt": "https://wolt.com",
    "Grammarly (Superhuman)": "https://superhuman.com",
    "Stripe": "https://stripe.com",
    "n8n": "https://n8n.io",
    "Synthesia": "https://www.synthesia.io",
    "Grafana Labs": "https://grafana.com",
    "Personio": "https://www.personio.com",
    "GitLab": "https://about.gitlab.com",
    "Dash0": "https://www.dash0.com",
    "Zapier": "https://zapier.com",
    "Contentful": "https://www.contentful.com",
    "Elastic": "https://www.elastic.co",
    "Airbnb": "https://www.airbnb.com",
    "Shopify": "https://www.shopify.com",
    "Mozilla": "https://www.mozilla.org",
    "JetBrains": "https://www.jetbrains.com",
    "Deel": "https://www.deel.com",
    "ElevenLabs": "https://elevenlabs.io",
    "Qonto": "https://qonto.com",
    "eBay (Dreilinden hub)": "https://www.ebay.com",
    "Kleinanzeigen (Adevinta)": "https://www.kleinanzeigen.de",
    "Lightspeed": "https://www.lightspeedhq.com",
    "Redcare Pharmacy": "https://www.redcare-pharmacy.com",
    "Framer": "https://www.framer.com",
    "Eterno Group": "https://eterno.group",
    "DuckDuckGo": "https://duckduckgo.com",
    "Supabase": "https://supabase.com",
    "GetYourGuide": "https://www.getyourguide.com",
    "Parloa": "https://www.parloa.com",
    "Buffer": "https://buffer.com",
    "Gradle": "https://gradle.com",
    "Netlify": "https://www.netlify.com",
    "Remote": "https://remote.com",
    "Doist": "https://doist.com",
    "Upvest": "https://upvest.co",
    "Bolt": "https://bolt.eu",
    "Kraken Technologies": "https://kraken.tech",
    "Better Stack": "https://betterstack.com",
    "Raycast": "https://www.raycast.com",
    "Cloudbeds": "https://www.cloudbeds.com",
    "Almedia": "https://almedia.com",
    "Celonis": "https://www.celonis.com",
    "deepset (Haystack)": "https://www.deepset.ai",
}

_source_adapter: TypeAdapter[Any] = TypeAdapter(Source)


class ConfigEntryError(ValueError):
    """A config entry can't be mapped to a Company."""


@dataclass(frozen=True)
class ImportedCompany:
    name: str
    tier: int
    website_url: str
    active: bool
    source: dict[str, Any]


def to_source(entry: Mapping[str, Any]) -> dict[str, Any]:
    """Map one old config entry to a validated ``Source`` dict."""
    kind = entry.get("type", "static")
    if kind in BOARD_TYPES:
        raw: dict[str, Any] = {
            "kind": "board",
            "board": kind,
            "board_id": entry[f"{kind}_id"],
        }
    elif kind in ("static", "dynamic"):
        selectors = {
            "careers_url": entry["url"],
            "job": entry["job_selector"],
            "title": entry["title_selector"],
            # The old scrapers default to "a" when the card has no link_selector.
            "link": entry.get("link_selector", "a"),
        }
        raw = {"kind": "scraper", "strategy": kind, "selectors": selectors}
    elif kind in CUSTOM_HANDLERS:
        raw = {"kind": "custom", "handler": CUSTOM_HANDLERS[kind]}
    else:
        raise ConfigEntryError(f"unknown type {kind!r}")
    return _source_adapter.validate_python(raw).model_dump(mode="json")


def to_company(entry: Mapping[str, Any]) -> ImportedCompany:
    name = entry["name"]
    if "title_prefix" in entry:
        # A filter on a parent's shared board; `Source` has no way to say it.
        raise ConfigEntryError("title_prefix filters are not representable")
    if name not in WEBSITES:
        raise ConfigEntryError("no website_url in WEBSITES")
    source = to_source(entry)
    return ImportedCompany(
        name=name,
        tier=entry.get("tier", 0),
        website_url=WEBSITES[name],
        # A custom source with no handler yet stays paused until one ships.
        active=source["kind"] != "custom",
        source=source,
    )


def load_companies(path: Path) -> tuple[list[ImportedCompany], list[str]]:
    """Map every entry. Returns (companies, one message per skipped entry)."""
    entries = yaml.safe_load(path.read_text())["companies"]
    companies: list[ImportedCompany] = []
    skipped: list[str] = []
    for entry in entries:
        try:
            companies.append(to_company(entry))
        except ConfigEntryError as e:
            skipped.append(f"{entry.get('name', '?')}: {e}")
    return companies, skipped


async def import_companies(
    session: AsyncSession, user_id: uuid.UUID, companies: list[ImportedCompany]
) -> tuple[list[str], list[str]]:
    """Insert missing companies. Returns (created names, already-present names)."""
    result = await session.execute(
        select(Company.name).where(Company.user_id == user_id)
    )
    existing = set(result.scalars())
    created: list[str] = []
    present: list[str] = []
    for c in companies:
        if c.name in existing:
            present.append(c.name)
            continue
        session.add(
            Company(
                user_id=user_id,
                name=c.name,
                tier=c.tier,
                website_url=c.website_url,
                active=c.active,
                source=c.source,
            )
        )
        created.append(c.name)
    await session.flush()
    return created, present


async def _run(config: Path, email: str, dry_run: bool) -> int:
    companies, skipped = load_companies(config)
    print(f"{len(companies)} companies mapped, {len(skipped)} skipped")
    for message in skipped:
        print(f"  skipped {message}")
    if dry_run:
        for c in companies:
            state = "active" if c.active else "paused"
            print(f"  {c.name} [{c.source['kind']}, {state}]")
        return 0

    engine = create_engine(os.environ["DATABASE_URL"])
    try:
        async with create_sessionmaker(engine)() as session:
            user = await get_user_by_email(session, email)
            if user is None:
                print("no user with that email", file=sys.stderr)
                return 1
            created, present = await import_companies(session, user.id, companies)
            await session.commit()
    finally:
        await engine.dispose()
    print(f"created {len(created)}, already present {len(present)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Import config.yaml companies for one user"
    )
    parser.add_argument("config", type=Path, help="path to the old config.yaml")
    parser.add_argument("--email", required=True, help="account that gets the rows")
    parser.add_argument(
        "--dry-run", action="store_true", help="print the mapping, touch no database"
    )
    args = parser.parse_args(argv)
    return asyncio.run(_run(args.config, args.email, args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
