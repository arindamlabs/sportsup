"""Multi-user fan-out: fetch each competition ONCE, then alert every subscriber.

This is what keeps SportsUp $0 at scale. The old single-user path fetched data per
configured event; here we fetch each distinct (competition, season) watched by *any*
active subscriber exactly once, then fan those fixtures/results out to every user who
watches them — applying each user's own timezone, quiet-hours, lead-times, alert
toggles, and a per-user (chat_id-namespaced) dedup key. Adding users adds zero API
calls. Pre-match odds are cached per match, so even upset detection costs one odds
lookup per match no matter how many users share it.

The heavy reuse: each user is turned into an :func:`effective_config` and run through
the existing :class:`~sportsup.alerts.engine.AlertEngine`, unchanged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .alerts import AlertEngine
from .alerts.models import Alert
from .catalog import competition_name, get_competition
from .config import AppConfig, EventConfig
from .providers import Fixture, MatchOdds, MatchResult, ProviderError, Standing
from .providers.router import ProviderRouter
from .providers.teams import TeamResolver
from .state import StateStore
from .subscribers import (
    Subscriber,
    Subscription,
    SubscriberStore,
    effective_config,
    watchlist_for,
)

logger = logging.getLogger("sportsup.fanout")


@dataclass
class CompetitionData:
    """Everything fetched once for a (competition, season), shared by all users."""

    code: str
    season: int
    fixtures: list[Fixture] = field(default_factory=list)
    results: list[MatchResult] = field(default_factory=list)
    standings: list[Standing] | None = None
    error: str | None = None
    _odds: dict[str, MatchOdds | None] = field(default_factory=dict)

    def odds_lookup(self, router: ProviderRouter, *, budget=None, now: datetime | None = None):
        """A per-match-cached odds lookup. Multiple users watching the same match
        trigger at most one real API call. When an ``OddsBudget`` is supplied, a call
        is made only if the daily cap allows — otherwise we return None and the engine
        uses its standings/form fallback."""

        def lookup(result: MatchResult) -> MatchOdds | None:
            key = result.fixture.stable_id()
            if key not in self._odds:
                if budget is not None and not budget.try_consume(now or datetime.now(timezone.utc)):
                    self._odds[key] = None   # budget spent — fall back to standings
                    return None
                try:
                    self._odds[key] = router.get_match_odds(
                        competition_code=self.code, season=self.season,
                        home_team=result.fixture.home.name,
                        away_team=result.fixture.away.name,
                        kickoff=result.fixture.utc_kickoff,
                    )
                except ProviderError as exc:
                    logger.info("odds unavailable for %s (%s)", key, exc)
                    self._odds[key] = None
            return self._odds[key]

        return lookup


@dataclass
class SubscriberPlan:
    """The alerts owed to one subscriber this cycle (already deduped, unsent)."""

    subscriber: Subscriber
    alerts: list[Alert] = field(default_factory=list)


def fetch_competition_data(
    router: ProviderRouter, keys: set[tuple[str, int]], now: datetime,
    *, lookahead_days: int, lookback_days: int,
) -> dict[tuple[str, int], CompetitionData]:
    """Fetch fixtures/results/standings for each distinct competition ONCE.

    Per-competition failures are logged and isolated (an empty CompetitionData with an
    `error`) so one bad competition never blocks the others."""
    out: dict[tuple[str, int], CompetitionData] = {}
    date_to = now + timedelta(days=lookahead_days)
    since = now - timedelta(days=lookback_days)
    for code, season in sorted(keys):
        data = CompetitionData(code=code, season=season)
        try:
            data.fixtures = router.get_fixtures(
                competition_code=code, season=season, date_from=now, date_to=date_to
            )
        except ProviderError as exc:
            data.error = str(exc)
            logger.warning("fixtures unavailable for %s/%s: %s", code, season, exc)
        try:
            data.results = router.get_results(
                competition_code=code, season=season, date_from=since, date_to=now
            )
        except ProviderError as exc:
            logger.warning("results unavailable for %s/%s: %s", code, season, exc)
        if data.results:
            try:
                data.standings = router.get_standings(competition_code=code, season=season)
            except ProviderError:
                data.standings = None
        out[(code, season)] = data
    return out


def _event_for(sub: Subscriber, subs: list[Subscription], code: str, season: int) -> EventConfig:
    comp = get_competition(code)
    return EventConfig(
        id=f"{code.lower()}-{season}",
        name=competition_name(code),
        competition_code=code,
        season=season,
        api_football_league=comp.api_football_league if comp else None,
        teams=watchlist_for(subs, code, season),
        alerts=sub.toggles,
    )


def plan_for_subscriber(
    sub: Subscriber, subs: list[Subscription], base_config: AppConfig, store: StateStore,
    comp_data: dict[tuple[str, int], CompetitionData], router: ProviderRouter, now: datetime,
    *, include_past: bool, odds_budget=None,
) -> list[Alert]:
    """All unsent alerts owed to one subscriber, drawn from the shared competition data."""
    alerts: list[Alert] = []
    # Distinct (competition, season) this user watches.
    keys = {(s.competition_code, s.season) for s in subs}
    for code, season in sorted(keys):
        data = comp_data.get((code, season))
        if data is None:
            continue
        event = _event_for(sub, subs, code, season)
        eff = effective_config(sub, base_config, [event])
        engine = AlertEngine(eff, store, dedup_prefix=f"{sub.chat_id}:")
        resolver = TeamResolver(event.teams)

        def watched(name_home: str, name_away: str) -> bool:
            return (not event.teams) or resolver.is_watched(name_home) or resolver.is_watched(name_away)

        if sub.reminders_enabled and not data.error:
            mine = [fx for fx in data.fixtures if watched(fx.home.name, fx.away.name)]
            alerts.extend(engine.unsent(
                engine.plan_reminders(event, mine, now=now, include_past=include_past)
            ))

        if sub.upsets_enabled or sub.finals_enabled:
            mine = [r for r in data.results if watched(r.fixture.home.name, r.fixture.away.name)]
            standings = data.standings if sub.upsets_enabled else None
            alerts.extend(engine.unsent(
                engine.evaluate_results(
                    event, mine,
                    odds_lookup=data.odds_lookup(router, budget=odds_budget, now=now),
                    standings=standings,
                )
            ))
    return alerts


def plan_for_all_subscribers(
    base_config: AppConfig, router: ProviderRouter, store: StateStore, sub_store: SubscriberStore,
    *, now: datetime | None = None, include_past: bool = False, lookback_days: int | None = None,
    odds_budget=None,
) -> list[SubscriberPlan]:
    """Fetch every watched competition once, then build each active subscriber's alerts."""
    now = now or datetime.now(timezone.utc)
    lookback_days = lookback_days if lookback_days is not None else base_config.scheduling.result_lookback_days

    subscribers = sub_store.list_subscribers(status="active")
    keys = sub_store.active_competitions()
    comp_data = fetch_competition_data(
        router, keys, now,
        lookahead_days=base_config.fixture_sync_lookahead_days, lookback_days=lookback_days,
    )
    logger.info(
        "fan-out: %d active subscriber(s) across %d competition(s)", len(subscribers), len(keys)
    )

    plans: list[SubscriberPlan] = []
    for sub in subscribers:
        subs = sub_store.list_subscriptions(sub.chat_id)
        if not subs:
            continue
        alerts = plan_for_subscriber(
            sub, subs, base_config, store, comp_data, router, now,
            include_past=include_past, odds_budget=odds_budget,
        )
        plans.append(SubscriberPlan(subscriber=sub, alerts=alerts))
    return plans
