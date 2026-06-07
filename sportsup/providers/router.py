"""Routes each data request to the right provider(s) with failover.

Capability ordering is registration order: football-data.org is registered first, so
it is primary for fixtures/results/standings; API-Football is registered second, so it
is the fallback for those and the only source for odds. If a provider is unavailable
(network/5xx/rate-limit) the router transparently tries the next that supports the call.
"""

from __future__ import annotations

import logging
from datetime import datetime

from .base import (
    Capability,
    NotSupportedError,
    ProviderError,
    ProviderUnavailableError,
    SportsDataProvider,
)
from .models import Fixture, MatchOdds, MatchResult, Standing, TeamRef

logger = logging.getLogger("sportsup.providers.router")


class ProviderRouter:
    def __init__(self, providers: list[SportsDataProvider]) -> None:
        if not providers:
            raise ValueError("ProviderRouter needs at least one provider")
        self.providers = providers

    def _for(self, capability: Capability) -> list[SportsDataProvider]:
        return [p for p in self.providers if p.supports(capability)]

    def _try(self, capability: Capability, op, call):
        """Run `call(provider)` against each capable provider until one succeeds."""
        candidates = self._for(capability)
        if not candidates:
            raise NotSupportedError(f"no provider supports {capability.value}")
        last_exc: ProviderError | None = None
        for provider in candidates:
            try:
                return call(provider)
            except ProviderUnavailableError as exc:
                logger.warning("%s unavailable for %s (%s); failing over", provider.name, op, exc)
                last_exc = exc
            except NotSupportedError as exc:
                last_exc = exc
        assert last_exc is not None
        logger.error("all providers failed for %s", op)
        raise last_exc

    def get_fixtures(
        self, *, competition_code: str, season: int, date_from: datetime, date_to: datetime
    ) -> list[Fixture]:
        return self._try(
            Capability.FIXTURES,
            "fixtures",
            lambda p: p.get_fixtures(
                competition_code=competition_code, season=season,
                date_from=date_from, date_to=date_to,
            ),
        )

    def get_results(
        self, *, competition_code: str, season: int, date_from: datetime, date_to: datetime
    ) -> list[MatchResult]:
        return self._try(
            Capability.RESULTS,
            "results",
            lambda p: p.get_results(
                competition_code=competition_code, season=season,
                date_from=date_from, date_to=date_to,
            ),
        )

    def get_standings(self, *, competition_code: str, season: int) -> list[Standing]:
        return self._try(
            Capability.STANDINGS,
            "standings",
            lambda p: p.get_standings(competition_code=competition_code, season=season),
        )

    def get_teams(self, *, competition_code: str, season: int) -> list[TeamRef]:
        return self._try(
            Capability.TEAMS,
            "teams",
            lambda p: p.get_teams(competition_code=competition_code, season=season),
        )

    def get_match_odds(
        self,
        *,
        competition_code: str,
        season: int,
        home_team: str,
        away_team: str,
        kickoff: datetime,
    ) -> MatchOdds | None:
        return self._try(
            Capability.ODDS,
            "odds",
            lambda p: p.get_match_odds(
                competition_code=competition_code, season=season,
                home_team=home_team, away_team=away_team, kickoff=kickoff,
            ),
        )

    def health(self) -> dict[str, bool]:
        return {p.name: p.health_check() for p in self.providers}


def build_router(secrets, *, league_map: dict[str, int] | None = None) -> ProviderRouter | None:
    """Construct a router from available credentials.

    Returns None if no provider has credentials (so callers can run in a keyless
    dry-run). Registration order sets primary/fallback precedence.
    """
    # Imported here to keep optional httpx provider deps out of module import time.
    from .api_football import ApiFootballProvider
    from .football_data import FootballDataProvider

    providers: list[SportsDataProvider] = []
    if getattr(secrets, "football_data_api_key", None):
        providers.append(FootballDataProvider(secrets.football_data_api_key))
    if getattr(secrets, "api_football_key", None):
        providers.append(ApiFootballProvider(secrets.api_football_key, league_map=league_map))

    if not providers:
        return None
    return ProviderRouter(providers)
