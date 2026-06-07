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
from .providers.teams import TeamResolver, canonical_name

logger = logging.getLogger("sportsup.sync")


@dataclass
class EventFixtures:
    event: EventConfig
    fixtures: list[Fixture] = field(default_factory=list)
    # Watchlist names the provider's full team roster never contains -> likely a typo.
    unknown_teams: list[str] = field(default_factory=list)
    # Watched teams that are valid but simply have no fixture in the lookahead window.
    idle_teams: list[str] = field(default_factory=list)
    error: str | None = None


def _competition_team_names(router: ProviderRouter, event: EventConfig) -> set[str] | None:
    """Full roster for spelling validation. None if unavailable (don't block sync)."""
    try:
        teams = router.get_teams(competition_code=event.competition_code, season=event.season)
    except ProviderError as exc:
        logger.info("team roster unavailable for %s (%s); skipping spelling check", event.id, exc)
        return None
    return {t.name for t in teams}


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

        # Filter fixtures to watched teams and track which watched teams actually play.
        played_canonical: set[str] = set()
        for fx in fixtures:
            home_watched = resolver.is_watched(fx.home.name)
            away_watched = resolver.is_watched(fx.away.name)
            if home_watched:
                played_canonical.add(canonical_name(fx.home.name))
            if away_watched:
                played_canonical.add(canonical_name(fx.away.name))
            if not event.teams or home_watched or away_watched:
                result.fixtures.append(fx)
        result.fixtures.sort(key=lambda f: f.utc_kickoff)

        if event.teams:
            roster = _competition_team_names(router, event)
            unknown = set(resolver.unmatched(roster)) if roster is not None else set()
            result.unknown_teams = [t for t in event.teams if t in unknown]
            # Idle = watched, validly spelled, but no fixture in the window.
            result.idle_teams = [
                t for t in event.teams
                if t not in unknown and canonical_name(t) not in played_canonical
            ]
        out.append(result)
    return out
