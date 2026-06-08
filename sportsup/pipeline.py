"""Shared alert-gathering pipeline used by both the `notify`/`alerts` CLI and the runtime.

Turns config + provider data into candidate (unsent) alerts. Delivery and dedup-marking
are the caller's job — this module only decides *what* is alertable right now.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from .alerts import AlertEngine
from .alerts.models import Alert
from .config import AppConfig
from .providers import ProviderError
from .providers.router import ProviderRouter
from .providers.teams import TeamResolver
from .sync import collect_watched_fixtures

_log = logging.getLogger("sportsup.pipeline")


def plan_all_reminders(
    config: AppConfig, router: ProviderRouter, engine: AlertEngine, now: datetime,
    *, include_past: bool = False,
) -> list[Alert]:
    """Reminders (unsent) for watched fixtures across enabled events. `include_past`
    also returns reminders whose lead-window has arrived (for delivery/catch-up)."""
    out: list[Alert] = []
    fixtures_by_event = {ef.event.id: ef for ef in collect_watched_fixtures(config, router, now=now)}
    for ev in config.enabled_events:
        ef = fixtures_by_event.get(ev.id)
        if ef is None or ef.error:
            continue
        out.extend(engine.unsent(
            engine.plan_reminders(ev, ef.fixtures, now=now, include_past=include_past)
        ))
    return out


def gather_result_alerts(
    config: AppConfig,
    router: ProviderRouter,
    engine: AlertEngine,
    now: datetime,
    *,
    lookback_days: int,
    logger: logging.Logger | None = None,
) -> list[Alert]:
    """Final-score / shock alerts (unsent) for watched matches finished in the window."""
    logger = logger or _log
    out: list[Alert] = []
    since = now - timedelta(days=lookback_days)
    for ev in config.enabled_events:
        resolver = TeamResolver(ev.teams)
        try:
            results = router.get_results(
                competition_code=ev.competition_code, season=ev.season,
                date_from=since, date_to=now,
            )
        except ProviderError as exc:
            logger.warning("%s: results unavailable (%s)", ev.name, exc)
            continue
        watched = [
            r for r in results
            if not ev.teams or resolver.is_watched(r.fixture.home.name)
            or resolver.is_watched(r.fixture.away.name)
        ]
        standings = None
        if ev.alerts.shock_result and watched:
            try:
                standings = router.get_standings(competition_code=ev.competition_code, season=ev.season)
            except ProviderError:
                standings = None

        def odds_lookup(r, _ev=ev):
            try:
                return router.get_match_odds(
                    competition_code=_ev.competition_code, season=_ev.season,
                    home_team=r.fixture.home.name, away_team=r.fixture.away.name,
                    kickoff=r.fixture.utc_kickoff,
                )
            except ProviderError:
                return None

        out.extend(engine.unsent(
            engine.evaluate_results(ev, watched, odds_lookup=odds_lookup, standings=standings)
        ))
    return out
