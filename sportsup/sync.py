"""Fixture-sync service: pull upcoming matches for watched teams.

Phase 2 provides the read path (fetch + filter to the watchlist). Scheduling reminders
off these fixtures and persisting them is Phase 3/5.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .config import AppConfig, EventConfig
from .providers import Fixture, ProviderError
from .providers.router import ProviderRouter
from .providers.teams import TeamResolver

logger = logging.getLogger("sportsup.sync")


@dataclass
class EventFixtures:
    event: EventConfig
    fixtures: list[Fixture] = field(default_factory=list)
    unmatched_teams: list[str] = field(default_factory=list)
    error: str | None = None


def collect_watched_fixtures(
    config: AppConfig, router: ProviderRouter, *, now: datetime | None = None
) -> list[EventFixtures]:
    """For each enabled event, fetch upcoming fixtures and keep only watched teams."""
    now = now or datetime.now(timezone.utc)
    date_to = now + timedelta(days=config.fixture_sync_lookahead_days)

    out: list[EventFixtures] = []
    for event in config.enabled_events:
        resolver = TeamResolver(event.teams)
        result = EventFixtures(event=event)
        try:
            fixtures = router.get_fixtures(
                competition_code=event.competition_code,
                season=event.season,
                date_from=now,
                date_to=date_to,
            )
        except ProviderError as exc:
            result.error = str(exc)
            logger.error("fixture sync failed for %s: %s", event.id, exc)
            out.append(result)
            continue

        seen_team_names: set[str] = set()
        for fx in fixtures:
            seen_team_names.add(fx.home.name)
            seen_team_names.add(fx.away.name)
            if not event.teams or (
                resolver.is_watched(fx.home.name) or resolver.is_watched(fx.away.name)
            ):
                result.fixtures.append(fx)

        result.fixtures.sort(key=lambda f: f.utc_kickoff)
        result.unmatched_teams = resolver.unmatched(seen_team_names) if event.teams else []
        out.append(result)
    return out
