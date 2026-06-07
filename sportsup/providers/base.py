"""Provider interface, capabilities, and error taxonomy."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum

from .models import Fixture, MatchOdds, MatchResult, Standing


class Capability(str, Enum):
    FIXTURES = "fixtures"
    RESULTS = "results"
    STANDINGS = "standings"
    ODDS = "odds"


class ProviderError(Exception):
    """Base for all provider failures."""


class ProviderUnavailableError(ProviderError):
    """Network error, 5xx, or auth failure — the router should fail over."""


class RateLimitError(ProviderUnavailableError):
    """429 / quota exhausted. Carries an optional retry-after hint (seconds)."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class NotSupportedError(ProviderError):
    """This provider does not offer the requested capability (e.g. odds)."""


class SportsDataProvider(ABC):
    """A swappable data source. Implement the capabilities you support; raise
    :class:`NotSupportedError` for the rest (declare them in :meth:`capabilities`)."""

    #: short stable name used in logs and Fixture.provider
    name: str = "abstract"

    @abstractmethod
    def capabilities(self) -> set[Capability]:
        ...

    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities()

    def health_check(self) -> bool:
        """Cheap connectivity/auth probe. Default: try a tiny fixtures call."""
        return True

    def get_fixtures(
        self, *, competition_code: str, season: int, date_from: datetime, date_to: datetime
    ) -> list[Fixture]:
        raise NotSupportedError(f"{self.name} does not support fixtures")

    def get_results(
        self, *, competition_code: str, season: int, date_from: datetime, date_to: datetime
    ) -> list[MatchResult]:
        raise NotSupportedError(f"{self.name} does not support results")

    def get_standings(self, *, competition_code: str, season: int) -> list[Standing]:
        raise NotSupportedError(f"{self.name} does not support standings")

    def get_match_odds(
        self,
        *,
        competition_code: str,
        season: int,
        home_team: str,
        away_team: str,
        kickoff: datetime,
    ) -> MatchOdds | None:
        raise NotSupportedError(f"{self.name} does not support odds")
